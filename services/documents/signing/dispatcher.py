from shared.logging import get_logger

from database import fetch_one
from services.r2_client import r2_get_object
from services.documents.signing.r2_lock import (
    acquire_signing_lock_R2, release_signing_lock_R2_fail,
)
from services.documents.signing.audit_logger import log_signature_event
from services.documents.retrieval.pending_signatures import _is_my_turn_condition
from shared.exceptions import ValidationError, SignerTurnPendingError

log = get_logger(__name__)


async def _get_signing_data(document_id: str, user_id: str, *, schema_name: str) -> dict:
    row = await fetch_one(
        f"""
        SELECT
            ds.is_numerator,
            ds.signing_order,
            ds.status AS signer_status,
            u."CountryID" as user_cuit,
            dt.signature_policy,
            dt.acronym as doc_type_acronym,
            {_is_my_turn_condition('ds')} AS is_my_turn,
            (SELECT count(*)
               FROM document_signers ds_pend
              WHERE ds_pend.document_id = ds.document_id
                AND ds_pend.is_numerator = false
                AND (ds_pend.status = 'pending' OR ds_pend.status IS NULL)
            ) AS pending_common_signers
        FROM document_signers ds
        JOIN users u ON ds.user_id = u.id
        LEFT JOIN document_draft dd ON ds.document_id = dd.id
        LEFT JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE ds.document_id = $1 AND ds.user_id = $2
        """,
        document_id,
        user_id,
        schema_name=schema_name,
    )

    if not row:
        raise ValidationError("signer_not_found_for_document")

    return dict(row)


async def _fetch_signing_inputs(document_id: str, *, schema_name: str) -> dict:
    from datetime import datetime

    doc_row = await fetch_one(
        """
        SELECT dt.acronym, dd.reference, dd.document_type_id,
               dd.content as content, dd.resume
        FROM document_draft dd
        JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE dd.id = $1
        """,
        document_id,
        schema_name=schema_name,
    )

    signers_row = await fetch_one(
        """
        SELECT json_agg(
            json_build_object(
                'user_id', ds.user_id,
                'full_name', u.full_name,
                'status', ds.status,
                'is_numerator', ds.is_numerator,
                'signing_order', ds.signing_order,
                'signed_at', ds.signed_at
            )
        ) as signers
        FROM document_signers ds
        JOIN users u ON ds.user_id = u.id
        WHERE ds.document_id = $1
        """,
        document_id,
        schema_name=schema_name,
    )

    sectors_row = await fetch_one(
        """
        SELECT ARRAY_AGG(DISTINCT u.sector_id)
               FILTER (WHERE u.sector_id IS NOT NULL) as sector_ids
        FROM document_signers ds
        JOIN users u ON ds.user_id = u.id
        WHERE ds.document_id = $1
        """,
        document_id,
        schema_name=schema_name,
    )

    if not doc_row:
        raise ValidationError("document_not_found_for_numbering")

    content = doc_row["content"] or {}

    return {
        "acronym": doc_row["acronym"],
        "reference": doc_row["reference"],
        "document_type_id": doc_row["document_type_id"],
        "content": content,
        "resume": doc_row.get("resume"),
        "signers": signers_row["signers"] if signers_row else [],
        "signer_sector_ids": sectors_row["sector_ids"] if sectors_row else None,
        "current_year": datetime.now().year,
    }


async def _validate_numbering_permissions(
    user_id: str,
    document_id: str,
    *,
    schema_name: str,
) -> None:
    from database import fetch_one as _fetch_one
    from services.documents.signing.numbering_permissions import (
        can_user_number_document_type,
    )

    type_row = await _fetch_one(
        """
        SELECT dd.document_type_id
        FROM document_draft dd
        WHERE dd.id = $1
        """,
        document_id,
        schema_name=schema_name,
    )
    if not type_row:
        raise ValidationError("document_not_found_for_permission_check")

    has_rank, has_sector, reason = await can_user_number_document_type(
        user_id,
        type_row["document_type_id"],
        schema_name=schema_name,
    )

    if not has_rank or not has_sector:
        raise ValidationError(reason)


async def _reserve_number_async(
    document_id: str,
    user_id: str,
    *,
    schema_name: str,
    batch_id: str | None = None,
) -> tuple[str, int | None, str]:
    from shared.numbering import reserve_number

    inputs = await _fetch_signing_inputs(document_id, schema_name=schema_name)

    official_number, _department_id, sequence, reservation_id = await reserve_number(
        document_type_acronym=inputs["acronym"],
        user_id=user_id,
        year=inputs["current_year"],
        schema_name=schema_name,
        document_id=document_id,
        reference=inputs["reference"],
        document_type_id=inputs["document_type_id"],
        content=inputs["content"],
        resume=inputs.get("resume"),
        signers=inputs["signers"],
        signer_sector_ids=inputs["signer_sector_ids"],
        batch_id=batch_id,
    )

    return official_number, sequence, reservation_id


async def _cancel_number_async(
    document_id: str,
    *,
    schema_name: str,
    reason: str,
) -> None:
    try:
        from shared.numbering import cancel_number
        await cancel_number(document_id, schema_name=schema_name, reason=reason)
    except Exception as e:
        log.warning(f"dispatcher._cancel_number_async soft-fail: {e}")


async def _count_completed_signers(document_id: str, *, schema_name: str) -> int:
    row = await fetch_one(
        "SELECT COUNT(*) AS n FROM document_signers"
        " WHERE document_id = $1::uuid AND signed_at IS NOT NULL",
        document_id,
        schema_name=schema_name,
    )
    return row["n"] if row else 0


async def dispatch_digital_signing(
    document_id: str,
    user_id: str,
    *,
    schema_name: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    batch_id: str | None = None,
) -> dict:
    log.info(
        f"dispatch_digital_signing start doc={document_id[:8]}... user={user_id[:8]}..."
    )

    signing_data = await _get_signing_data(document_id, user_id, schema_name=schema_name)

    is_numerator: bool = bool(signing_data.get("is_numerator"))
    user_cuit: str | None = signing_data.get("user_cuit")

    signer_status = signing_data.get("signer_status")
    if signer_status not in ("pending", None):
        raise ValidationError(
            f"El usuario ya firmó este documento (status: {signer_status})"
        )

    if not signing_data.get("is_my_turn"):
        pendientes = int(signing_data.get("pending_common_signers") or 0)
        log.info(
            "dispatch_digital_signing: firma fuera de turno doc=%s user=%s "
            "(pendientes=%d, numerador=%s)",
            document_id[:8], user_id[:8], pendientes, is_numerator,
        )
        raise SignerTurnPendingError(pendientes)

    if user_cuit is None:
        raise ValidationError(
            "Este documento requiere Firma Digital, para proceder es necesario que su usuario tenga registrado su Número de Identificación Nacional. Solicite a su administrador local del sistema."
        )

    lock_acquired = await acquire_signing_lock_R2(
        schema_name=schema_name, doc_id=document_id
    )
    if not lock_acquired:
        raise ValidationError("document_already_signing")

    reserved_number: str | None = None
    reservation_id: str | None = None

    try:
        if is_numerator:
            await _validate_numbering_permissions(
                user_id,
                document_id,
                schema_name=schema_name,
            )

            try:
                reserved_number, _seq, reservation_id = await _reserve_number_async(
                    document_id,
                    user_id,
                    schema_name=schema_name,
                    batch_id=batch_id,
                )
                log.info(
                    f"dispatch_digital_signing number_reserved={reserved_number} "
                    f"ticket={reservation_id[:8] if reservation_id else 'N/A'}..."
                )
            except Exception:
                await release_signing_lock_R2_fail(
                    schema_name=schema_name,
                    doc_id=document_id,
                )
                raise

        inprocess_key = f"inprocess/{document_id.replace('-', '')}.pdf"
        pdf_bytes = await r2_get_object(
            schema_name=schema_name, key=inprocess_key, bucket="tosign",
        )

        sig_llx, sig_lly, sig_urx, sig_ury = 50.0, 30.0, 250.0, 110.0
        from services.shared.notary_api import call_notary_stamp_only
        from services.shared.settings_utils import get_city_from_settings
        number_to_stamp = reserved_number if (is_numerator and reserved_number) else ""
        city_for_stamp = await get_city_from_settings(schema_name=schema_name) if number_to_stamp else ""
        completed_signers = await _count_completed_signers(document_id, schema_name=schema_name)
        try:
            pdf_bytes, sig_llx, sig_lly, sig_urx, sig_ury = await call_notary_stamp_only(
                pdf_bytes, number_to_stamp, city=city_for_stamp, existing_count=completed_signers,
            )
            log.info(
                f"dispatch_digital_signing stamp_ok number={number_to_stamp or 'NONE'} "
                f"sig=({sig_llx},{sig_lly})-({sig_urx},{sig_ury})"
            )
        except Exception as stamp_err:
            log.warning(f"dispatch_digital_signing stamp_notary_fail (usando posición default): {stamp_err}")

        from services.documents.signing.providers.firmador_gdi import FirmadorGDIProvider

        provider = FirmadorGDIProvider()
        session_data = await provider.start_signing(
            document_id=document_id,
            user_id=user_id,
            schema_name=schema_name,
            pdf_bytes=pdf_bytes,
            is_numerator=is_numerator,
            number=reserved_number,
            user_cuit=user_cuit,
            ip_address=ip_address,
            user_agent=user_agent,
            sig_llx=sig_llx,
            sig_lly=sig_lly,
            sig_urx=sig_urx,
            sig_ury=sig_ury,
            reservation_id=reservation_id,
            batch_id=batch_id,
        )

    except Exception as exc:
        try:
            await release_signing_lock_R2_fail(
                schema_name=schema_name, doc_id=document_id,
            )
        except Exception as lock_err:
            log.warning(f"dispatch_digital_signing lock_rollback_error: {lock_err}")

        if reserved_number:
            try:
                await _cancel_number_async(
                    document_id,
                    schema_name=schema_name,
                    reason=f"dispatch_digital_error: {str(exc)[:300]}",
                )
            except Exception as cancel_err:
                log.warning(f"dispatch_digital_signing cancel_rollback_error: {cancel_err}")

        raise

    try:
        await log_signature_event(
            schema_name=schema_name,
            document_id=document_id,
            user_id=user_id,
            signature_method="digital_token",
            result="pending",
            session_id=session_data["session_id"],
            user_cuit=user_cuit,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception as audit_err:
        log.warning(f"dispatch_digital_signing audit_log soft-fail: {audit_err}")

    expires_at = session_data["expires_at"]
    expires_at_iso = (
        expires_at.isoformat()
        if hasattr(expires_at, "isoformat")
        else str(expires_at)
    )

    return {
        "success": True,
        "message": "Sesion de firma digital iniciada",
        "document_id": document_id,
        "signature_id": session_data["session_id"],
        "document_status": "sent_to_sign",
        "signed_at": None,
        "is_numerator": is_numerator,
        "official_number": reserved_number,
        "signed_pdf_url": None,
        "flow": "digital",
        "session_id": session_data["session_id"],
        "poll_url": f"/digital-signature/poll/{session_data['session_id']}",
        "user_payload": session_data["user_payload"],
        "expires_at": expires_at_iso,
        "file_id": session_data["file_id"],
    }
