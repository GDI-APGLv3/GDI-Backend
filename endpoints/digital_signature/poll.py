from shared.logging import get_logger
import asyncio
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from auth import get_current_user
from models.schemas import AuthenticatedUser
from database import fetch_one, fetch_all, execute
from services.documents.signing.providers import (
    PollSigningPending, PollSigningSigned, PollSigningCancelled,
    PollSigningFailed,
)
from services.documents.signing.providers.firmador_gdi import FirmadorGDIProvider
from services.documents.signing.r2_lock import release_signing_lock_R2_fail
from services.documents.signing.audit_logger import log_signature_event
from services.documents.signing.cert_validator import validate_cert_full
from services.cache import redis_client
from shared.dependencies import get_tenant_schema
from shared.numbering import cancel_number, confirm_number
from shared.exceptions import StaleReservationError, ValidationError
from services.shared.notary_api import call_notary_verify
from services.documents.signing.digital_completion import (
    encolar_cierre_digital,
    guardar_pdf_firmado,
    marcar_sesion_completing,
)

log = get_logger(__name__)
router = APIRouter()


_POLL_RATE_PER_SEC = float(os.getenv("POLL_RATE_PER_SEC", "3"))
_POLL_BURST        = int(os.getenv("POLL_BURST", "5"))
_POLL_BUCKET_MAX   = int(os.getenv("POLL_BUCKET_MAX", "10000"))
_POLL_BUCKET_TTL   = float(os.getenv("POLL_BUCKET_TTL_SECONDS", "300"))

_poll_buckets: dict[str, tuple[float, float]] = {}

def _poll_rate_limit_ok(user_id: str, session_id: str) -> bool:
    key = f"{user_id}:{session_id}"
    now = time.monotonic()
    tokens, last = _poll_buckets.get(key, (_POLL_BURST, now))
    elapsed = now - last
    tokens = min(_POLL_BURST, tokens + elapsed * _POLL_RATE_PER_SEC)
    if tokens < 1.0:
        _poll_buckets[key] = (tokens, now)
        return False
    tokens -= 1.0
    _poll_buckets[key] = (tokens, now)

    if len(_poll_buckets) > _POLL_BUCKET_MAX:
        cutoff = now - _POLL_BUCKET_TTL
        stale_keys = [k for k, (_, ts) in _poll_buckets.items() if ts < cutoff]
        for k in stale_keys:
            _poll_buckets.pop(k, None)

    return True


async def _get_session(session_id: str) -> dict | None:
    row = await fetch_one(
        """
        SELECT session_id, file_id, schema_name, user_id::text, document_id::text,
               is_numerator, number, status, expires_at, consumed_at,
               provider_name, user_cuit, failure_reason,
               reservation_id::text, created_at, batch_id::text
        FROM public.digital_signature_sessions
        WHERE session_id = $1
        """,
        session_id,
        schema_name="public",
    )
    return dict(row) if row else None


async def _mark_session_status(session_id: str, status: str, reason: str | None = None) -> bool:
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


async def _rebuild_auto_link_results(document_id: str, *, schema_name: str) -> list[dict]:
    from services.case_queries import get_rebuild_auto_link_results_query

    try:
        signed_row = await fetch_one(
            "SELECT signed_at FROM official_documents WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )
        if not signed_row or signed_row["signed_at"] is None:
            log.warning(
                f"_rebuild_auto_link_results: signed_at IS NULL para doc={document_id[:8]}...; "
                "devolviendo [] (documento aún no numerado o id incorrecto)"
            )
            return []

        signed_at = signed_row["signed_at"]

        rows = await fetch_all(
            get_rebuild_auto_link_results_query(),
            document_id,
            signed_at,
            schema_name=schema_name,
        )

        results = [
            {
                "case_id":     str(row["case_id"]),
                "case_number": row["case_number"],
                "linked":      bool(row["linked"]),
                "reason":      None,
            }
            for row in rows
        ]

        log.info(
            f"_rebuild_auto_link_results: {len(results)} resultado(s) re-derivados "
            f"doc={document_id[:8]}... schema={schema_name}"
        )
        return results

    except Exception as exc:
        log.warning(
            f"_rebuild_auto_link_results soft-fail doc={document_id[:8]}...: {exc}"
        )
        return []


async def _cleanup_after_consume_failure(
    session_id: str,
    session: dict,
    reason: str,
    cert_result=None,
) -> None:
    try:
        await release_signing_lock_R2_fail(
            schema_name=session["schema_name"],
            doc_id=session["document_id"],
        )
    except Exception as r2_err:
        log.warning(f"poll consume_failure r2_restore soft-fail: {r2_err}")
    if redis_client:
        try:
            await run_in_threadpool(
                redis_client.delete,
                f"firma:storage:{session['schema_name']}:{session['file_id']}",
                f"firma:storage:{session['schema_name']}:{session_id}",
                f"firma:storage:meta:{session['schema_name']}:{session_id}",
            )
        except Exception as redis_err:
            log.warning(f"poll consume_failure redis_cleanup soft-fail: {redis_err}")
    marcada_consume = await _mark_session_status(session_id, "failed", reason=reason)
    if marcada_consume:
        try:
            _cert_kwargs = {}
            if cert_result is not None:
                _cert_kwargs = dict(
                    cert_serial=cert_result.cert_serial,
                    cert_subject_dn=cert_result.cert_subject_dn,
                    cert_issuer_dn=cert_result.cert_issuer_dn,
                    cert_subject_cuit=cert_result.cert_subject_cuit,
                )
            await log_signature_event(
                schema_name=session["schema_name"],
                document_id=session["document_id"],
                user_id=session["user_id"],
                signature_method="digital_token",
                result="fail",
                failure_reason=reason,
                session_id=session_id,
                user_cuit=session.get("user_cuit"),
                official_number=None,
                **_cert_kwargs,
            )
        except Exception as ae:
            log.warning(f"poll consume_failure audit soft-fail: {ae}")


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

    if session["schema_name"] != schema_name:
        raise HTTPException(status_code=404, detail="session_not_found")

    if session["user_id"] != str(request.state.tenant_user_id):
        raise HTTPException(status_code=403, detail="not_session_owner")

    if not _poll_rate_limit_ok(str(current_user.user_id), session_id):
        raise HTTPException(status_code=429, detail="too_many_poll_requests")

    if session["status"] in ("signed", "cancelled", "expired", "failed"):
        if session["status"] == "signed":
            auto_link_results: list[dict] = []
            if session["is_numerator"] and session.get("number"):
                auto_link_results = await _rebuild_auto_link_results(
                    session["document_id"], schema_name=session["schema_name"]
                )
            return {
                "status": "signed",
                "official_number": session.get("number"),
                "auto_link_results": auto_link_results,
                "failure_reason": session.get("failure_reason"),
            }
        return {"status": session["status"], "failure_reason": session.get("failure_reason")}

    if session["status"] in ("completing", "waiting_batch"):
        return {
            "status": "completing",
            "official_number": session.get("number"),
            "batch_id": session.get("batch_id"),
        }

    now = datetime.now(timezone.utc)
    expires_at = session["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        await release_signing_lock_R2_fail(
            schema_name=session["schema_name"],
            doc_id=session["document_id"],
        )
        marcada_expired = await _mark_session_status(session_id, "expired")

        if session.get("is_numerator") and session.get("number"):
            try:
                await cancel_number(
                    session["document_id"],
                    schema_name=session["schema_name"],
                    reason="digital_session_expired",
                    reservation_id=session.get("reservation_id"),
                )
            except Exception as e:
                log.warning(f"poll.expired cancel_number soft-fail: {e}")

        if marcada_expired:
            try:
                await log_signature_event(
                    schema_name=session["schema_name"],
                    document_id=session["document_id"],
                    user_id=session["user_id"],
                    signature_method="digital_token",
                    result="fail",
                    failure_reason="digital_session_expired",
                    session_id=session_id,
                )
            except Exception as e:
                log.warning(f"poll.expired audit_log soft-fail: {e}")

        return {"status": "expired"}

    provider = FirmadorGDIProvider()
    try:
        result = await run_in_threadpool(
            provider.poll_signing,
            session_id=session_id,
            schema_name=session["schema_name"],
        )
    except ValidationError as _binding_err:
        log.error(
            "poll.binding_rejected session=%s doc=%s — %s",
            session_id[:8], str(session["document_id"])[:8], _binding_err,
        )
        await release_signing_lock_R2_fail(
            schema_name=session["schema_name"],
            doc_id=session["document_id"],
        )
        if session["is_numerator"] and session.get("number"):
            try:
                await cancel_number(
                    session["document_id"],
                    schema_name=session["schema_name"],
                    reason="binding_mismatch",
                )
            except Exception as _cn_err:
                log.warning(f"poll.binding_rejected cancel_number soft-fail: {_cn_err}")
        marcada_binding = await _mark_session_status(session_id, "failed", "binding_mismatch")
        if marcada_binding:
            try:
                await log_signature_event(
                    schema_name=session["schema_name"],
                    document_id=session["document_id"],
                    user_id=session["user_id"],
                    signature_method="digital_token",
                    result="fail",
                    failure_reason="binding_mismatch",
                    session_id=session_id,
                )
            except Exception as _audit_err:
                log.warning(f"poll.binding_rejected audit_log soft-fail: {_audit_err}")
        return {"status": "failed", "failure_reason": "binding_mismatch"}

    if isinstance(result, PollSigningPending):
        return {"status": "pending"}

    if isinstance(result, PollSigningCancelled):
        await release_signing_lock_R2_fail(
            schema_name=session["schema_name"],
            doc_id=session["document_id"],
        )
        if session["is_numerator"] and session.get("number"):
            try:
                await cancel_number(
                    session["document_id"],
                    schema_name=session["schema_name"],
                    reason="cancelled_by_user",
                    reservation_id=session.get("reservation_id"),
                )
            except Exception as _cn_err:
                log.warning(f"poll.cancelled cancel_number soft-fail: {_cn_err}")
        marcada_cancel = await _mark_session_status(session_id, "cancelled")
        if marcada_cancel:
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
        _provider_code = (result.error_code or "provider_error").lower()
        log.warning(
            "poll.provider_failed session=%s code=%s msg=%s",
            session_id[:8], result.error_code, result.error_message,
        )
        marcada_provider = await _mark_session_status(session_id, "failed", _provider_code)
        if marcada_provider:
            try:
                await log_signature_event(
                    schema_name=session["schema_name"],
                    document_id=session["document_id"],
                    user_id=session["user_id"],
                    signature_method="digital_token",
                    result="fail",
                    failure_reason=_provider_code,
                    session_id=session_id,
                )
            except Exception as _audit_err:
                log.warning(f"poll.provider_failed audit_log soft-fail: {_audit_err}")
        return {"status": "failed", "failure_reason": _provider_code}

    if isinstance(result, PollSigningSigned):


        consumed_now = await _mark_consumed(session_id)
        if not consumed_now:
            _fresh_row = await fetch_one(
                """
                SELECT status, failure_reason
                FROM public.digital_signature_sessions
                WHERE session_id = $1
                """,
                session_id,
                schema_name="public",
            )
            _fresh = dict(_fresh_row) if _fresh_row else None
            _fresh_status = _fresh["status"] if _fresh else "pending"
            if _fresh_status == "signed":
                _replay_auto_link: list[dict] = []
                if session["is_numerator"] and session.get("number"):
                    _replay_auto_link = await _rebuild_auto_link_results(
                        session["document_id"], schema_name=session["schema_name"]
                    )
                return {
                    "status": "signed",
                    "official_number": session.get("number"),
                    "auto_link_results": _replay_auto_link,
                }
            if _fresh_status in ("completing", "waiting_batch"):
                return {
                    "status": "completing",
                    "official_number": session.get("number"),
                }
            if _fresh_status in ("failed", "cancelled", "expired"):
                return {
                    "status": _fresh_status,
                    "failure_reason": _fresh.get("failure_reason") if _fresh else None,
                }
            return {"status": "pending"}

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
            _cert_reason = cert_result.failure_reason or "cert_unknown"
            if _cert_reason.startswith("cert_parse_error"):
                _cert_reason = "cert_parse_error"
            elif _cert_reason.startswith("cert_field_error"):
                _cert_reason = "cert_field_error"
            log.warning(
                "poll.cert_validation_failed session=%s reason_raw=%s reason_code=%s",
                session_id[:8], cert_result.failure_reason, _cert_reason,
            )
            marcada_cert = await _mark_session_status(session_id, "failed", _cert_reason)
            if marcada_cert:
                try:
                    await log_signature_event(
                        schema_name=session["schema_name"],
                        document_id=session["document_id"],
                        user_id=session["user_id"],
                        signature_method="digital_token",
                        result="fail",
                        failure_reason=_cert_reason,
                        session_id=session_id,
                        cert_serial=cert_result.cert_serial,
                        cert_subject_dn=cert_result.cert_subject_dn,
                        cert_issuer_dn=cert_result.cert_issuer_dn,
                        cert_subject_cuit=cert_result.cert_subject_cuit,
                    )
                except Exception as e:
                    log.warning(f"poll cert_fail audit_log soft-fail: {e}")
            raise HTTPException(status_code=422, detail=_cert_reason)

        verify_result = await call_notary_verify(result.signed_pdf_bytes)
        if not verify_result.get("ok"):
            log.warning(
                "notary_verify_soft_fail doc=%s reason=%s — continuando (V3 pendiente)",
                session["document_id"],
                verify_result.get("failure_reason"),
            )

        _cas_pre_done = False
        if session["is_numerator"] and session.get("number") and session.get("reservation_id"):
            try:
                await confirm_number(
                    session["document_id"],
                    session["reservation_id"],
                    schema_name=session["schema_name"],
                )
                _cas_pre_done = True
                log.info(
                    f"poll: CAS RESERVED→CONFIRMING (pre-lock) "
                    f"doc={session['document_id'][:8]}... ticket={session['reservation_id'][:8]}..."
                )
            except StaleReservationError as e:
                log.error(
                    f"poll: StaleReservationError pre-lock "
                    f"doc={session['document_id'][:8]}...: {e}"
                )
                await _cleanup_after_consume_failure(
                    session_id, session,
                    reason="stale_reservation",
                    cert_result=cert_result,
                )
                return {"status": "failed", "failure_reason": "stale_reservation"}
            except asyncio.CancelledError:
                log.warning(
                    "poll.cas_confirm_cancelled doc=%s correlationId=%s "
                    "— cleanup best-effort antes de re-lanzar",
                    session["document_id"][:8], session_id[:8],
                )
                try:
                    await asyncio.wait_for(
                        asyncio.shield(
                            _cleanup_after_consume_failure(
                                session_id, session,
                                reason="cas_confirm_cancelled",
                                cert_result=cert_result,
                            )
                        ),
                        timeout=2.0,
                    )
                except BaseException as _cleanup_err:
                    log.warning(
                        "poll.cas_confirm_cancelled_cleanup_soft_fail correlationId=%s: %s",
                        session_id[:8], _cleanup_err,
                    )
                try:
                    await asyncio.wait_for(
                        asyncio.shield(cancel_number(
                            session["document_id"],
                            schema_name=session["schema_name"],
                            reason="cas_confirm_cancelled",
                            reservation_id=session.get("reservation_id"),
                        )),
                        timeout=1.0,
                    )
                except BaseException as _cn_err:
                    log.warning(
                        "poll.cas_confirm_cancelled_cancel_number_soft_fail "
                        "correlationId=%s: %s",
                        session_id[:8], _cn_err,
                    )
                raise
            except Exception as _cas_err:
                log.error(
                    "poll.cas_confirm_failed doc=%s correlationId=%s: %s",
                    session["document_id"][:8], session_id[:8], _cas_err,
                )
                try:
                    await cancel_number(
                        session["document_id"],
                        schema_name=session["schema_name"],
                        reason="cas_confirm_failure",
                        reservation_id=session.get("reservation_id"),
                    )
                except Exception as _cn_err:
                    log.warning(f"poll cas_confirm cancel_number soft-fail: {_cn_err}")
                await _cleanup_after_consume_failure(
                    session_id, session,
                    reason="cas_confirm_failure",
                    cert_result=cert_result,
                )
                return {"status": "failed", "failure_reason": "cas_confirm_failure"}

        try:
            await guardar_pdf_firmado(
                schema_name=session["schema_name"],
                document_id=session["document_id"],
                signed_pdf=result.signed_pdf_bytes,
            )
        except Exception as _persist_err:
            log.error(
                "poll.persist_signed_pdf_failed doc=%s correlationId=%s: %s",
                session["document_id"][:8], session_id[:8], _persist_err,
            )
            await release_signing_lock_R2_fail(
                schema_name=session["schema_name"],
                doc_id=session["document_id"],
            )
            if session["is_numerator"] and session.get("number"):
                try:
                    await cancel_number(
                        session["document_id"],
                        schema_name=session["schema_name"],
                        reason="persist_signed_pdf_failed",
                        reservation_id=session.get("reservation_id"),
                    )
                except Exception as _cn_err:
                    log.warning("poll.persist_failed cancel_number soft-fail: %s", _cn_err)
            marcada_persist = await _mark_session_status(
                session_id, "failed", "persist_signed_pdf_failed"
            )
            if marcada_persist:
                try:
                    await log_signature_event(
                        schema_name=session["schema_name"],
                        document_id=session["document_id"],
                        user_id=session["user_id"],
                        signature_method="digital_token",
                        result="fail",
                        failure_reason="persist_signed_pdf_failed",
                        session_id=session_id,
                    )
                except Exception as _audit_err:
                    log.warning(f"poll.persist_failed audit_log soft-fail: {_audit_err}")
            return {"status": "failed", "failure_reason": "persist_signed_pdf_failed"}

        _cert_para_auditoria = {
            "cert_serial": cert_result.cert_serial,
            "cert_subject_dn": cert_result.cert_subject_dn,
            "cert_issuer_dn": cert_result.cert_issuer_dn,
            "cert_subject_cuit": cert_result.cert_subject_cuit,
            "cert_not_after": (
                cert_result.cert_not_after.isoformat()
                if getattr(cert_result, "cert_not_after", None) else None
            ),
            "revocation_status": cert_result.revocation_status,
            "tsa_url": verify_result.get("tsa_url"),
            "tsa_time": verify_result.get("tsa_time"),
            "file_id": session["file_id"],
        }

        if session.get("batch_id"):
            from services.documents.signing.batch_digital import registrar_firma_de_una

            _resultado_tanda = await registrar_firma_de_una(
                {
                    **session,
                    "cert": _cert_para_auditoria,
                    "cas_pre_done": _cas_pre_done,
                }
            )
            return {
                "status": "completing",
                "official_number": session.get("number"),
                "batch_id": session["batch_id"],
                "batch_pending": _resultado_tanda["faltan"],
            }

        _cola_id = await encolar_cierre_digital(
            schema_name=session["schema_name"],
            document_id=session["document_id"],
            user_id=session["user_id"],
            reservation_id=session.get("reservation_id"),
            official_number=session.get("number"),
            digital_session_id=session_id,
            is_numerator=bool(session["is_numerator"]),
            cas_pre_done=_cas_pre_done,
            cert=_cert_para_auditoria,
            file_id=session.get("file_id"),
        )

        await marcar_sesion_completing(session_id)

        return {
            "status": "completing",
            "official_number": session.get("number"),
            "tracking_session_id": _cola_id,
        }

    raise HTTPException(status_code=500, detail="unknown_poll_result")
