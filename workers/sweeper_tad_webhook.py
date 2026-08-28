import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database import execute
from shared.logging import get_logger

log = get_logger(__name__)

SWEEPER_INTERVAL_SECONDS = int(os.getenv("TAD_WEBHOOK_SWEEPER_INTERVAL_SECONDS", "60"))
PROCESSING_STALE_MINUTES = int(os.getenv("TAD_WEBHOOK_PROCESSING_STALE_MINUTES", "5"))


async def sweep_tad_webhook_jobs() -> None:
    from shared.advisory_lock import global_job_lock, LOCK_ID_SWEEPER_TAD_WEBHOOK

    try:
        async with global_job_lock(
            LOCK_ID_SWEEPER_TAD_WEBHOOK, "sweeper_tad_webhook"
        ) as got_lock:
            if not got_lock:
                return
            result = await execute(
                f"""
                UPDATE public.tad_webhook_jobs
                SET status = 'pending', claimed_by = NULL, updated_at = NOW()
                WHERE status = 'processing'
                  AND updated_at < NOW() - INTERVAL '{PROCESSING_STALE_MINUTES} minutes'
                """,
                schema_name="public",
            )
            if result and result != "UPDATE 0":
                log.info("sweeper_tad_webhook.requeued %s", result)
    except Exception:
        log.exception("sweeper_tad_webhook.error")


def schedule_sweeper_tad_webhook(scheduler: AsyncIOScheduler) -> None:
    scheduler.add_job(
        sweep_tad_webhook_jobs,
        "interval",
        seconds=SWEEPER_INTERVAL_SECONDS,
        id="sweeper_tad_webhook",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    log.info("sweeper_tad_webhook.scheduled interval=%ds", SWEEPER_INTERVAL_SECONDS)
