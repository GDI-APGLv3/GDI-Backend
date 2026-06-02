"""
GET /digital-signature/poll/{session_id}
Polleado por el frontend cada 2s. Cierra el flujo cuando AutoFirma postea la firma.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from auth import get_current_user
from models.schemas import AuthenticatedUser
from database import fetch_one, execute, transaction as db_transaction
from services.documents.signing.providers import (
    PollSigningPending, PollSigningSigned, PollSigningCancelled,
    PollSigningFailed, PollSigningExpired,
)
from services.documents.signing.providers.autofirma import AutoFirmaProvider
from services.documents.signing.r2_lock import (
    release_signing_lock_R2_success, release_signing_lock_R2_fail,
)
from services.documents.signing.audit_logger import log_signature_event
from services.documents.signing.cert_validator import validate_cert_full
from services.cache import redis_client
from shared.dependencies import get_tenant_schema
from shared.numbering import cancel_number
from services.shared.notary_api import call_notary_verify

log = logging.getLogger(__name__)
router = APIRouter()


async def _get_session(session_id: str) -> dict | None:
    row = await fetch_one(
        """
        SELECT session_id, file_id, schema_name, user_id::text, document_id::text,
               is_numerator, number, status, expires_at, consumed_at,
               provider_name, user_cuit, failure_reason
        FROM public.digital_signature_sessions
        WHERE session_id = $1
        """,
        session_id,
        schema_name="public",
    )
    return dict(row) if row else None


async def _mark_session_status(session_id: str, status: str, reason: str | None = None) -> bool:
    """Actualiza status. Retorna True si cambio (CAS simple)."""
    row = await fetch_one(
        """
        UPDATE public.digital_signature_sessions
        SET status = $1, updated_at = NOW(),
            completed_at = CASE WHEN $2 = 'signed' THEN NOW() ELSE completed_at END,
            cancelled_at = CASE WHEN $3 = 'cancelled' THEN NOW() ELSE cancelled_at END,
            failure_reason = COALESCE($4, failure_reason)
        WHERE session_id = $5 AND status = 'pending'
        RETURNING session_id
        """,
        status, status, status, reason, session_id,
        schema_name="public",
    )
    return row is not None


async def _mark_consumed(session_id: str) -> bool:
    """Anti-replay: marca consumed_at atomicamente. Retorna True si fue el primero."""
    row = await fetch_one(
        """
        UPDATE public.digital_signature_sessions
        SET consumed_at = NOW()
        WHERE session_id = $1 AND consumed_at IS NULL
        RETURNING session_id
        """,
        session_id,
        schema_name="public",
    )
    return row is not None


async def _upload_pdf_to_oficial(
    schema_name: str,
    official_number: str,
    signed_pdf: bytes,
) -> None:
    """Sube el PDF firmado al bucket oficial. get_tenant_r2_client es async; boto3 es sync (threadpool)."""
    from services.storage.cloudflare import get_tenant_r2_client

    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    oficial_filename = f"{official_number}.pdf"
    await run_in_threadpool(r2_client.upload_oficial, signed_pdf, oficial_filename)
    log.info(f"poll.numerator_complete: PDF subido a oficial/{oficial_filename}")


async def _update_numerator_in_db(
    document_id: str,
    user_id: str,
    official_number: str,
    *,
    schema_name: str,
) -> None:
    """Actualiza official_documents y document_draft en una transacción atómica."""
    async with db_transaction(schema_name=schema_name) as conn:
        await conn.execute(
            """
            UPDATE official_documents
            SET signed_at = CURRENT_TIMESTAMP
            WHERE id = $1 AND signed_at IS NULL
            """,
            document_id,
        )
        await conn.execute(
            """
            UPDATE document_draft
            SET status = 'signed',
                document_number = $1,
                numbered_at = CURRENT_TIMESTAMP,
                numbered_by = $2,
                last_modified_at = CURRENT_TIMESTAMP
            WHERE id = $3
            """,
            official_number, user_id, document_id,
        )
    log.info(
        f"poll.numerator_complete: document_draft y official_documents actualizados "
        f"doc={document_id[:8]}... number={official_number}"
    )


async def _complete_numerator_async(
    document_id: str,
    user_id: str,
    schema_name: str,
    official_number: str,
    signed_pdf: bytes,
) -> None:
    """
    Finaliza la firma digital del numerador: sube PDF, confirma número, actualiza BD.

    Reemplaza a _complete_numerator_digital_signing (que usaba asyncio.run internamente).
    Flujo:
        1. Upload PDF a R2 (sync → threadpool)
        2. confirm_number() (async → await directo, sin asyncio.run)
        3. Update BD (sync → threadpool)
    """
    from shared.numbering import confirm_number

    await _upload_pdf_to_oficial(
        schema_name,
        official_number,
        signed_pdf,
    )

    await confirm_number(document_id, schema_name=schema_name)
    log.info(f"poll.numerator_complete: número {official_number} confirmado")

    await _update_numerator_in_db(document_id, user_id, official_number, schema_name=schema_name)


async def _update_document_signer(
    document_id: str, user_id: str, schema_name: str,
    session_id: str, cert_serial: str | None, cert_cuit: str | None, provider: str,
) -> None:
    await execute(
        """
        UPDATE document_signers
        SET signed_at = NOW(),
            status = 'signed',
            signed_with_provider = $1,
            cert_serial = $2,
            cert_subject_cuit = $3,
            signature_session_id = $4
        WHERE document_id = $5 AND user_id = $6
        """,
        provider, cert_serial, cert_cuit, session_id, document_id, user_id,
        schema_name=schema_name,
    )


@router.get("/digital-signature/poll/{session_id}")
async def poll_signing(
    session_id: str,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> dict:
    if not session_id.isalnum():
        raise HTTPException(status_code=400, detail="session_id_invalid")

    session = await _get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session_not_found")

    # Verificar que el usuario es el dueno de la sesion
    if session["user_id"] != str(request.state.tenant_user_id):
        raise HTTPException(status_code=403, detail="not_session_owner")

    # Si ya fue procesada, devolver estado final
    if session["status"] in ("signed", "cancelled", "expired", "failed"):
        return {"status": session["status"], "failure_reason": session.get("failure_reason")}

    # Verificar expiracion
    now = datetime.now(timezone.utc)
    expires_at = session["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        await release_signing_lock_R2_fail(
            schema_name=session["schema_name"],
            doc_id=session["document_id"],
        )
        await _mark_session_status(session_id, "expired")
        return {"status": "expired"}

    # Pollear al provider
    provider = AutoFirmaProvider()
    result = await run_in_threadpool(
        provider.poll_signing,
        session_id=session_id,
        schema_name=session["schema_name"],
    )

    if isinstance(result, PollSigningPending):
        return {"status": "pending"}

    if isinstance(result, PollSigningCancelled):
        await release_signing_lock_R2_fail(
            schema_name=session["schema_name"],
            doc_id=session["document_id"],
        )
        await _mark_session_status(session_id, "cancelled")
        try:
            await log_signature_event(
                schema_name=session["schema_name"],
                document_id=session["document_id"],
                user_id=session["user_id"],
                signature_method="digital_token",
                result="fail",
                failure_reason="cancelled_by_user",
                session_id=session_id,
            )
        except Exception as e:
            log.warning(f"poll audit_log soft-fail: {e}")
        return {"status": "cancelled"}

    if isinstance(result, PollSigningFailed):
        await release_signing_lock_R2_fail(
            schema_name=session["schema_name"],
            doc_id=session["document_id"],
        )
        await _mark_session_status(session_id, "failed", result.error_message)
        return {"status": "failed", "failure_reason": result.error_message}

    if isinstance(result, PollSigningSigned):
        # Anti-replay
        consumed_now = await _mark_consumed(session_id)
        if not consumed_now:
            return {"status": "signed"}

        # Validar cert
        cert_result = await run_in_threadpool(
            validate_cert_full,
            result.cert_der,
            expected_cuit=session.get("user_cuit"),
        )

        if not cert_result.ok:
            await release_signing_lock_R2_fail(
                schema_name=session["schema_name"],
                doc_id=session["document_id"],
            )
            if session["is_numerator"] and session.get("number"):
                try:
                    await cancel_number(
                        session["document_id"],
                        schema_name=session["schema_name"],
                        reason=f"cert_validation_failed:{cert_result.failure_reason}",
                    )
                except Exception as e:
                    log.warning(f"poll cancel_number soft-fail: {e}")
            await _mark_session_status(session_id, "failed", cert_result.failure_reason)
            try:
                await log_signature_event(
                    schema_name=session["schema_name"],
                    document_id=session["document_id"],
                    user_id=session["user_id"],
                    signature_method="digital_token",
                    result="fail",
                    failure_reason=cert_result.failure_reason,
                    session_id=session_id,
                    cert_serial=cert_result.cert_serial,
                    cert_subject_dn=cert_result.cert_subject_dn,
                    cert_issuer_dn=cert_result.cert_issuer_dn,
                    cert_subject_cuit=cert_result.cert_subject_cuit,
                )
            except Exception as e:
                log.warning(f"poll cert_fail audit_log soft-fail: {e}")
            raise HTTPException(status_code=422, detail=cert_result.failure_reason)

        # Verify integridad pyHanko (Notary /sign-pdf/verify)
        # TODO V3: activar hard-fail cuando GDI-Notary tenga trust store ONTI configurado.
        # Actualmente pyHanko rechaza certs de AC ONTI porque no tiene los certs raiz:
        #   "AC Raiz 2016" + "AC ONTI 2016" (https://pki.jefatura.gob.ar/)
        # Por ahora: soft-fail — loguear el resultado pero no bloquear la firma.
        verify_result = await call_notary_verify(result.signed_pdf_bytes)
        if not verify_result.get("ok"):
            log.warning(
                "notary_verify_soft_fail doc=%s reason=%s — continuando (V3 pendiente)",
                session["document_id"],
                verify_result.get("failure_reason"),
            )

        # Release lock
        await release_signing_lock_R2_success(
            schema_name=session["schema_name"],
            doc_id=session["document_id"],
            signed_pdf=result.signed_pdf_bytes,
            is_numerator=session["is_numerator"],
            number=session.get("number"),
        )

        # Completar firma del numerador: subir PDF a oficial + confirmar número + actualizar BD
        if session["is_numerator"] and session.get("number"):
            try:
                await _complete_numerator_async(
                    session["document_id"], session["user_id"], session["schema_name"],
                    session["number"], result.signed_pdf_bytes,
                )
            except Exception as e:
                log.error(f"poll _complete_numerator_async FAILED: {e}")
                # No relanzar: el PDF puede ya estar en oficial si falló solo el UPDATE BD

        # Actualizar document_signers (soft-fail: columnas pueden no existir aun)
        try:
            await _update_document_signer(
                session["document_id"], session["user_id"], session["schema_name"],
                session_id, cert_result.cert_serial, cert_result.cert_subject_cuit, "autofirma",
            )
        except Exception as e:
            log.warning(f"poll _update_document_signer soft-fail: {e}")

        # Audit log exito
        try:
            await log_signature_event(
                schema_name=session["schema_name"],
                document_id=session["document_id"],
                user_id=session["user_id"],
                signature_method="digital_token",
                result="ok",
                session_id=session_id,
                user_cuit=session.get("user_cuit"),
                official_number=session.get("number"),
                cert_serial=cert_result.cert_serial,
                cert_subject_dn=cert_result.cert_subject_dn,
                cert_issuer_dn=cert_result.cert_issuer_dn,
                cert_subject_cuit=cert_result.cert_subject_cuit,
                cert_not_after=cert_result.cert_not_after,
                revocation_status=cert_result.revocation_status,
                tsa_url=verify_result.get("tsa_url"),
                tsa_time=verify_result.get("tsa_time"),
            )
        except Exception as e:
            log.warning(f"poll success audit_log soft-fail: {e}")

        # Marcar session como signed
        await _mark_session_status(session_id, "signed")

        # Limpiar Redis
        if redis_client:
            try:
                redis_client.delete(
                    f"firma:storage:{session['schema_name']}:{session['file_id']}",
                    f"firma:storage:{session['schema_name']}:{session_id}",
                    f"firma:storage:meta:{session['schema_name']}:{session_id}",
                )
            except Exception as e:
                log.warning(f"poll redis_cleanup soft-fail: {e}")

        return {
            "status": "signed",
            "official_number": session.get("number"),
        }

    raise HTTPException(status_code=500, detail="unknown_poll_result")
