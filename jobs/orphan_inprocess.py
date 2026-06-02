"""
Cron job: reclaim_expired_pending_sessions

Recupera PDFs huerfanos en tosign/inprocess/ cuya sesion de firma
ya expiro en digital_signature_sessions. Se ejecuta cada 5 minutos
via APScheduler (AsyncIOScheduler) arrancado en main.py startup.

Por cada sesion expirada:
  1. Mueve PDF de inprocess/ → tosign/ (release_signing_lock_R2_fail)
  2. Marca sesion como expired en BD (UPDATE)
  3. Inserta evento en public.firma_audit_log (soft-fail)
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

log = logging.getLogger(__name__)

ORPHAN_INTERVAL_SECONDS = 300


async def _reclaim_async() -> None:
    """
    Consulta digital_signature_sessions y recupera huerfanos.
    """
    from database import fetch_one, fetch_all, execute
    from services.documents.signing.r2_lock import reclaim_orphan_inprocess
    from services.documents.signing.audit_logger import log_signature_event

    # Check explícito de existencia de tabla antes del query principal.
    # digital_signature_sessions solo existe desde Fase 2 — en Fase 1 la tabla
    # no existe todavía y el cron no debe tirar UndefinedTable cada 5 minutos.
    exists = await fetch_one(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'digital_signature_sessions'",
        schema_name="public",
    )
    if exists is None:
        log.debug(
            "orphan_reclaim: tabla digital_signature_sessions aun no existe (Fase 1 - esperado)"
        )
        return

    rows = await fetch_all(
        """
        SELECT session_id, schema_name, document_id, user_id
        FROM public.digital_signature_sessions
        WHERE status = 'pending'
          AND expires_at < NOW() - INTERVAL '1 minute'
        ORDER BY expires_at ASC
        LIMIT 100
        """,
        schema_name="public",
    )

    if not rows:
        return

    reclaimed = 0
    for row in rows:
        session_id = str(row["session_id"])
        schema_name = row["schema_name"]
        doc_id = str(row["document_id"])
        user_id = str(row["user_id"])

        # Paso 1: liberar lock R2 (mueve PDF inprocess/ → tosign/) — soft-fail
        try:
            await reclaim_orphan_inprocess(
                schema_name=schema_name,
                doc_id=doc_id,
                session_id=session_id,
            )
        except Exception:
            log.exception(
                "orphan_reclaim.r2_release_failed",
                extra={"session_id": session_id, "doc_id": doc_id},
            )

        # Paso 2: marcar sesion como expired en BD — soft-fail
        try:
            await execute(
                """
                UPDATE public.digital_signature_sessions
                   SET status = 'expired',
                       updated_at = NOW()
                 WHERE session_id = $1
                   AND status = 'pending'
                """,
                row["session_id"],
                schema_name="public",
            )
        except Exception:
            log.exception(
                "orphan_reclaim.db_update_failed",
                extra={"session_id": session_id},
            )

        # Paso 3: audit log — soft-fail (log_signature_event ya maneja sus propios errores)
        try:
            await log_signature_event(
                schema_name=schema_name,
                document_id=doc_id,
                user_id=user_id,
                signature_method="digital_token",
                result="fail",
                failure_reason="session_expired_orphan_reclaimed",
                session_id=session_id,
            )
        except Exception:
            log.exception(
                "orphan_reclaim.audit_log_failed",
                extra={"session_id": session_id},
            )

        reclaimed += 1

    log.info(
        "orphan_reclaim.completed",
        extra={"reclaimed": reclaimed, "total_found": len(rows)},
    )


async def reclaim_expired_pending_sessions() -> None:
    """Wrapper async: ejecuta la lógica de recuperación de huerfanos."""
    try:
        await _reclaim_async()
    except Exception:
        log.exception("orphan_reclaim.error")


def schedule_orphan_reclaim(scheduler: AsyncIOScheduler) -> None:
    """Registra el job en el scheduler. Llamar antes de scheduler.start()."""
    scheduler.add_job(
        reclaim_expired_pending_sessions,
        "interval",
        seconds=ORPHAN_INTERVAL_SECONDS,
        id="reclaim_orphan_inprocess",
        max_instances=1,
        coalesce=True,
    )
