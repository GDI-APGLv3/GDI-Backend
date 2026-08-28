import asyncio
import os
import socket
import time

import asyncpg

from database import DATABASE_URL, fetch_one, execute
from shared.logging import get_logger
from services.webhooks.tad_notify import send_tad_webhook_job

log = get_logger(__name__)

FALLBACK_POLL_SECONDS = int(os.getenv("TAD_WEBHOOK_FALLBACK_POLL_SECONDS", "20"))
RECONNECT_BACKOFF_SECONDS = int(os.getenv("TAD_WEBHOOK_RECONNECT_BACKOFF_SECONDS", "5"))
PROCESSING_TTL_MINUTES = int(os.getenv("TAD_WEBHOOK_PROCESSING_TTL_MINUTES", "5"))

_DIRECT_DATABASE_URL = DATABASE_URL.replace(":6432/", ":5432/")
_DB_DIRECT_HOST = os.getenv("DB_DIRECT_HOST")
if _DB_DIRECT_HOST:
    from database import DB_HOST as _DB_POOL_HOST
    _DIRECT_DATABASE_URL = _DIRECT_DATABASE_URL.replace(
        f"@{_DB_POOL_HOST}:", f"@{_DB_DIRECT_HOST}:"
    )


class TadWebhookWorker:

    def __init__(self) -> None:
        self._running = False
        self._worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._notify_event: asyncio.Event | None = None

    def stop(self) -> None:
        self._running = False
        evt = self._notify_event
        if evt is not None:
            evt.set()

    async def run(self) -> None:
        self._running = True
        log.info("tad_webhook.starting worker_id=%s", self._worker_id)
        backoff = RECONNECT_BACKOFF_SECONDS
        while self._running:
            try:
                await self._listen_loop()
                backoff = RECONNECT_BACKOFF_SECONDS
            except asyncio.CancelledError:
                log.info("tad_webhook.cancelled worker_id=%s", self._worker_id)
                break
            except Exception:
                log.exception(
                    "tad_webhook.connection_error — reconectando en %ds worker_id=%s",
                    backoff, self._worker_id,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
        log.info("tad_webhook.stopped worker_id=%s", self._worker_id)

    async def _listen_loop(self) -> None:
        conn = await asyncpg.connect(
            _DIRECT_DATABASE_URL, statement_cache_size=0,
            server_settings={"jit": "off"},
        )
        log.info("tad_webhook.connected worker_id=%s", self._worker_id)
        self._notify_event = asyncio.Event()

        async def _on_notify(_conn, _pid, _channel, _payload):
            self._notify_event.set()

        await conn.add_listener("tad_webhook", _on_notify)
        try:
            await self._drain_pending()
            while self._running:
                self._notify_event.clear()
                try:
                    await asyncio.wait_for(self._notify_event.wait(), timeout=float(FALLBACK_POLL_SECONDS))
                except asyncio.TimeoutError:
                    await conn.execute("SELECT 1")
                await self._drain_pending()
        finally:
            try:
                await conn.remove_listener("tad_webhook", _on_notify)
                await conn.close()
            except Exception:
                pass
            self._notify_event = None
            log.info("tad_webhook.disconnected worker_id=%s", self._worker_id)

    async def _drain_pending(self) -> None:
        while self._running:
            job = await self._claim_one()
            if job is None:
                break
            await send_tad_webhook_job(job)

    async def _claim_one(self) -> dict | None:
        row = await fetch_one(
            """
            UPDATE public.tad_webhook_jobs
            SET status = 'processing', claimed_by = $1, updated_at = NOW()
            WHERE id IN (
                SELECT id FROM public.tad_webhook_jobs
                WHERE status = 'pending' AND available_at <= NOW()
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id::text, schema_name, api_key_id::text, event_type, payload, attempts
            """,
            self._worker_id,
            schema_name="public",
        )
        if row:
            log.info("tad_webhook.claimed id=%s schema=%s", str(row["id"])[:8], row["schema_name"])
        return dict(row) if row else None
