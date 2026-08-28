
from contextlib import asynccontextmanager

from database import get_conn
from shared.logging import get_logger

log = get_logger(__name__)


LOCK_ID_SWEEPER_ESCRI      = 888890
LOCK_ID_SWEEPER_TAD_WEBHOOK = 888891
LOCK_ID_ORPHAN_INPROCESS   = 888892
LOCK_ID_TST_SWEEP          = 888893
LOCK_ID_RECONCILE_R2_DB    = 888894
LOCK_ID_RETRY_FAILED_PUBLICATIONS = 888895


@asynccontextmanager
async def global_job_lock(lock_id: int, job_name: str):
    async with get_conn(schema_name="public") as lock_conn:
        got_lock = await lock_conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", lock_id
        )
        if not got_lock:
            log.info(
                "%s.skip — otro proceso tiene el candado (lock_id=%s)",
                job_name, lock_id,
            )
            yield False
            return
        try:
            log.info("%s.acquired lock_id=%s", job_name, lock_id)
            yield True
        finally:
            await lock_conn.fetchval(
                "SELECT pg_advisory_unlock($1)", lock_id
            )
