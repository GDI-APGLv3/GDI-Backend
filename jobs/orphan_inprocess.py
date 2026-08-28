from shared.logging import get_logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler

log = get_logger(__name__)

ORPHAN_INTERVAL_SECONDS = 300


async def _reclaim_async() -> None:
    from database import fetch_one, fetch_all, execute
    from services.documents.signing.r2_lock import reclaim_orphan_inprocess
    from services.documents.signing.audit_logger import log_signature_event

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

        marcada = None
        try:
            marcada = await fetch_one(
                """
                UPDATE public.digital_signature_sessions
                   SET status = 'expired',
                       updated_at = NOW()
                 WHERE session_id = $1
                   AND status = 'pending'
                RETURNING session_id
                """,
                row["session_id"],
                schema_name="public",
            )
        except Exception:
            log.exception(
                "orphan_reclaim.db_update_failed",
                extra={"session_id": session_id},
            )

        if marcada is None:
            log.info(
                "orphan_reclaim.ya_cerrada_por_otro session=%s — sin fila de "
                "auditoria: la escribe quien la cerró",
                session_id[:8],
            )
            continue

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
    from shared.advisory_lock import global_job_lock, LOCK_ID_ORPHAN_INPROCESS

    try:
        async with global_job_lock(
            LOCK_ID_ORPHAN_INPROCESS, "orphan_reclaim"
        ) as got_lock:
            if not got_lock:
                return
            await _reclaim_async()
    except Exception:
        log.exception("orphan_reclaim.error")


def schedule_orphan_reclaim(scheduler: AsyncIOScheduler) -> None:
    scheduler.add_job(
        reclaim_expired_pending_sessions,
        "interval",
        seconds=ORPHAN_INTERVAL_SECONDS,
        id="reclaim_orphan_inprocess",
        max_instances=1,
        coalesce=True,
    )
