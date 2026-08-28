
import asyncio
import hashlib
from shared.logging import get_logger
from shared.utils import payload_as_dict
import os
import socket
import time

import asyncpg
import httpx
from fastapi.concurrency import run_in_threadpool

from database import DATABASE_URL, fetch_one, execute, transaction
from services.storage.cloudflare import get_tenant_r2_client
from services.shared.notary_api import call_notary_sign_pdf
from services.shared.notary_breaker import check_breaker_before_call
from services.shared.signer_data import get_signer_data
from services.shared.settings_utils import get_city_from_settings
from shared.alerts import send_alert_mail
from shared.numbering import confirm_number, finalize_number, cancel_number
from shared.exceptions import (
    NotaryBreakerOpenError,
    NotaryBusinessError,
    NotaryHashMismatchError,
    DocumentRejectedWhileInQueueError,
    StaleReservationError,
    R2ObjectLockedError,
    DatabaseBusyError,
)
from config.constants import (
    ESCRI_CONCURRENCY,
    PUBLISH_PUBLIC_MAX_RETRIES,
    ESCRI_SHUTDOWN_REQUEUE_TIMEOUT_SEC,
    ESCRI_HEARTBEAT_SEC,
    SIGN_TSA_BACKOFF_MINUTES,
    SIGN_TSA_MAX_ATTEMPTS,
    ESCRI_GUARD_MAX_ATTEMPTS,
    escri_worker_enabled,
    check_escri_ttl_coherence,
)

log = get_logger(__name__)

PENDING_TTL_MINUTES       = int(os.getenv("ESCRI_PENDING_TTL_MINUTES", "30"))
PROCESSING_TTL_MINUTES    = int(os.getenv("ESCRI_PROCESSING_TTL_MINUTES", "10"))
FALLBACK_POLL_SECONDS     = int(os.getenv("ESCRI_FALLBACK_POLL_SECONDS", "15"))
HEARTBEAT_LOG_SECONDS     = int(os.getenv("ESCRI_HEARTBEAT_LOG_SECONDS", "30"))
RECONNECT_BACKOFF_SECONDS = int(os.getenv("ESCRI_RECONNECT_BACKOFF_SECONDS", "5"))


ESCRI_MAX_PDF_MB    = int(os.getenv("ESCRI_MAX_PDF_MB", "15"))
ESCRI_MAX_PDF_BYTES = ESCRI_MAX_PDF_MB * 1024 * 1024


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, NotaryHashMismatchError):
        return "pdf_integrity_failed"
    if isinstance(exc, NotaryBreakerOpenError):
        return "notary_circuit_open"
    from shared.exceptions import PreOficialNotProvisionedError
    if isinstance(exc, PreOficialNotProvisionedError):
        return "preoficial_not_provisioned"
    if isinstance(exc, NotaryBusinessError):
        if "notary_fullpage" in str(exc).lower() or "FULLPAGE" in str(exc).upper():
            return "notary_fullpage"
        return "notary_business_error"
    from shared.exceptions import NotaryUnavailableError, NotaryTimeoutError
    if isinstance(exc, NotaryTimeoutError):
        return "notary_timeout"
    if isinstance(exc, NotaryUnavailableError):
        sc = getattr(exc, "status_code", None)
        if sc in (500, 502, 503, 504):
            return f"notary_{sc}"
        if getattr(exc, "exc_kind", None):
            return "notary_connect_error"
        return "notary_5xx"
    try:
        import asyncpg as _apg
        if isinstance(exc, _apg.PostgresError):
            return "db_error"
    except Exception:
        pass
    msg = str(exc)
    if "pdf_too_large" in msg:
        return "pdf_too_large"
    return f"unknown:{type(exc).__name__}"

_DIRECT_DATABASE_URL = DATABASE_URL.replace(":6432/", ":5432/")
_DB_DIRECT_HOST = os.getenv("DB_DIRECT_HOST")
if _DB_DIRECT_HOST:
    from database import DB_HOST as _DB_POOL_HOST
    _DIRECT_DATABASE_URL = _DIRECT_DATABASE_URL.replace(
        f"@{_DB_POOL_HOST}:", f"@{_DB_DIRECT_HOST}:"
    )


class EscriWorker:

    def __init__(self) -> None:
        self._running: bool = False
        self._worker_id: str = f"{socket.gethostname()}:{os.getpid()}"
        self._notify_event: asyncio.Event | None = None
        self.last_heartbeat_at: float = 0.0
        self._pause_logged: bool = False

    def stop(self) -> None:
        self._running = False
        evt = self._notify_event
        if evt is not None:
            evt.set()

    def _beat(self) -> None:
        self.last_heartbeat_at = time.monotonic()

    async def run(self) -> None:
        self._running = True
        self._beat()
        log.info("escri.starting worker_id=%s", self._worker_id)

        _ttl_ok, _ttl_msg = check_escri_ttl_coherence()
        if _ttl_ok:
            log.info("escri.ttl_coherence %s", _ttl_msg)
        else:
            log.error("escri.ttl_coherence %s", _ttl_msg)

        backoff = RECONNECT_BACKOFF_SECONDS
        while self._running:
            self._beat()
            try:
                await self._listen_loop()
                backoff = RECONNECT_BACKOFF_SECONDS
            except asyncio.CancelledError:
                log.info("escri.cancelled worker_id=%s", self._worker_id)
                break
            except Exception:
                log.exception(
                    "escri.connection_error — reconectando en %ds worker_id=%s",
                    backoff, self._worker_id,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
        log.info("escri.stopped worker_id=%s", self._worker_id)

    async def _listen_loop(self) -> None:
        conn = await asyncpg.connect(
            _DIRECT_DATABASE_URL, statement_cache_size=0,
            server_settings={"jit": "off"},
        )
        log.info("escri.connected worker_id=%s", self._worker_id)

        self._notify_event = asyncio.Event()

        async def _on_notify(_conn: object, _pid: int, _channel: str, _payload: str) -> None:
            self._notify_event.set()

        await conn.add_listener("escri", _on_notify)
        try:
            await self._drain_pending()

            last_heartbeat = time.monotonic()
            while self._running:
                self._notify_event.clear()
                try:
                    await asyncio.wait_for(
                        self._notify_event.wait(),
                        timeout=float(FALLBACK_POLL_SECONDS),
                    )
                except asyncio.TimeoutError:
                    log.debug("escri.fallback_poll worker_id=%s", self._worker_id)
                    await conn.execute("SELECT 1")

                await self._drain_pending()

                self._beat()
                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_LOG_SECONDS:
                    log.info("escri.heartbeat worker_id=%s", self._worker_id)
                    last_heartbeat = now
        finally:
            try:
                await conn.remove_listener("escri", _on_notify)
                await conn.close()
            except Exception:
                pass
            self._notify_event = None
            log.info("escri.disconnected worker_id=%s", self._worker_id)


    async def _drain_pending(self) -> None:
        if not escri_worker_enabled():
            if not self._pause_logged:
                log.warning(
                    "escri.worker_paused — ESCRI_WORKER_ENABLED=false: no se reclaman jobs. "
                    "La cola se acumula y drena sola al volver a prenderlo. OJO: el TTL de las "
                    "sesiones sigue corriendo — una pausa larga hace que el sweeper las expire "
                    "y CANCELE sus números. Es un freno de minutos, no un modo de operación."
                )
                self._pause_logged = True
            self._beat()
            return
        self._pause_logged = False

        while self._running:
            batch = await self._claim_batch(ESCRI_CONCURRENCY)
            if not batch:
                break
            await self._process_batch(batch)
            self._beat()

    async def _claim_batch(self, max_n: int) -> list[dict]:
        jobs: list[dict] = []
        for _ in range(max_n):
            job = await self._claim_one()
            if job is None:
                break
            jobs.append(job)

        seen: dict[tuple[str, str], str] = {}
        for job in jobs:
            doc_key = (str(job.get("schema_name")), str(job.get("document_id")))
            if doc_key in seen:
                log.error(
                    "escri.batch_duplicate_document schema=%s doc=%s "
                    "sessions=%s,%s — dos jobs del mismo documento en el mismo "
                    "lote paralelo; no debería pasar (ver comentario en _claim_batch)",
                    doc_key[0], doc_key[1][:8], seen[doc_key][:8],
                    str(job.get("session_id"))[:8],
                )
            else:
                seen[doc_key] = str(job.get("session_id"))

        return jobs

    async def _process_batch(self, jobs: list[dict]) -> None:

        async def _run_one(job: dict) -> None:
            job_type = str(job.get("job_type", "sign"))
            session_id = str(job.get("session_id", ""))
            try:
                if job_type == "sign":
                    await self._process_job(job)
                elif job_type == "sign_common":
                    await self._process_common_job(job)
                elif job_type == "sign_citizen":
                    await self._process_citizen_job(job)
                elif job_type == "digital_complete":
                    await self._process_digital_complete_job(job)
                else:
                    log.warning("escri.unknown_job_type job_type=%s session=%s",
                                job_type, session_id[:8])
                    await self._mark_session_failed(
                        session_id, f"unknown_job_type: {job_type}"
                    )
            except Exception:
                log.exception(
                    "escri.batch_job_unhandled_error session=%s job_type=%s "
                    "— no debería llegar acá (los _process_* "
                    "capturan sus propias excepciones); revisar",
                    session_id[:8], job_type,
                )
            self._beat()

        por_documento: dict[str, list[dict]] = {}
        for job in jobs:
            clave = str(job.get("document_id") or f"sin-doc:{job.get('session_id')}")
            por_documento.setdefault(clave, []).append(job)

        if len(por_documento) < len(jobs):
            log.info(
                "escri.batch_serializado_por_documento jobs=%d grupos=%d — "
                "hay jobs del mismo documento en el lote (GDI-215)",
                len(jobs), len(por_documento),
            )

        async def _run_group(grupo: list[dict]) -> None:
            for job in grupo:
                await _run_one(job)

        await asyncio.gather(
            *[_run_group(grupo) for grupo in por_documento.values()],
            return_exceptions=True,
        )

    async def _claim_one(self) -> dict | None:
        row = await fetch_one(
            """
            UPDATE public.signing_sessions
            SET status       = 'processing',
                claimed_at   = NOW(),
                claimed_by   = $1,
                expires_at   = NOW() + $2::text::interval,
                available_at = NULL,
                updated_at   = NOW()
            WHERE session_id IN (
                SELECT s.session_id
                FROM public.signing_sessions s
                LEFT JOIN (
                    -- GDI-339: cuántos jobs tiene cada municipio en vuelo AHORA.
                    -- Son pocos por definición (acotados por ESCRI_CONCURRENCY x
                    -- máquinas) y salen del índice (status, expires_at).
                    SELECT schema_name, COUNT(*) AS en_vuelo
                    FROM public.signing_sessions
                    WHERE status = 'processing'
                    GROUP BY schema_name
                ) v ON v.schema_name = s.schema_name
                WHERE s.status    = 'pending'
                  AND s.job_type  IN ('sign', 'sign_common', 'sign_citizen', 'digital_complete')
                  AND (s.available_at IS NULL OR s.available_at <= NOW())
                ORDER BY COALESCE(v.en_vuelo, 0) ASC,
                         s.created_at ASC
                FOR UPDATE OF s SKIP LOCKED
                LIMIT 1
            )
            RETURNING
                session_id::text,
                schema_name,
                document_id::text,
                reservation_id::text,
                user_id::text,
                citizen_id::text,
                payload,
                job_type,
                created_at
            """,
            self._worker_id,
            f"{PROCESSING_TTL_MINUTES} minutes",
            schema_name="public",
        )
        if row:
            log.info(
                "escri.claimed session=%s doc=%s schema=%s job_type=%s",
                str(row["session_id"])[:8],
                str(row["document_id"])[:8],
                row["schema_name"],
                row["job_type"],
            )
        return dict(row) if row else None


    async def _requeue_in_flight_on_cancel(self, session_id: str, doc_id: str) -> None:
        try:
            await asyncio.wait_for(
                execute(
                    """
                    UPDATE public.signing_sessions
                    SET status       = 'pending',
                        claimed_by   = NULL,
                        claimed_at   = NULL,
                        available_at = NULL,
                        updated_at   = NOW()
                    WHERE session_id = $1::uuid
                      AND claimed_by = $2
                      AND status     = 'processing'
                    """,
                    session_id,
                    self._worker_id,
                    schema_name="public",
                ),
                timeout=ESCRI_SHUTDOWN_REQUEUE_TIMEOUT_SEC,
            )
            log.warning(
                "escri.cancelled_requeued session=%s doc=%s — job en vuelo "
                "devuelto a pending por shutdown limpio",
                session_id[:8], doc_id[:8],
            )
        except Exception as _req_err:
            log.error(
                "escri.cancelled_requeue_failed session=%s doc=%s: %s "
                "(soft-fail; el sweeper lo recupera por processing_expired)",
                session_id[:8], doc_id[:8], _req_err,
            )


    async def _renew_claim(self, session_id: str) -> bool:
        row = await fetch_one(
            """
            UPDATE public.signing_sessions
            SET expires_at = NOW() + $1::text::interval,
                updated_at = NOW()
            WHERE session_id = $2::uuid
              AND status     = 'processing'
              AND claimed_by = $3
            RETURNING session_id
            """,
            f"{PROCESSING_TTL_MINUTES} minutes",
            session_id,
            self._worker_id,
            schema_name="public",
        )
        return row is not None

    def _spawn_heartbeat(self, session_id: str) -> tuple[asyncio.Task, asyncio.Event]:
        lost_event = asyncio.Event()

        async def _loop() -> None:
            while True:
                await asyncio.sleep(ESCRI_HEARTBEAT_SEC)
                try:
                    ok = await self._renew_claim(session_id)
                except Exception as _hb_err:
                    log.warning(
                        "escri.heartbeat_renew_error session=%s: %s "
                        "(soft-fail, se reintenta en el próximo ciclo)",
                        session_id[:8], _hb_err,
                    )
                    continue
                if not ok:
                    log.error(
                        "escri.heartbeat_lost_fence session=%s — otro worker "
                        "reclamó el job; el procesamiento en curso abortará "
                        "antes de repetir Notary/upload",
                        session_id[:8],
                    )
                    lost_event.set()
                    return

        task = asyncio.create_task(_loop())
        return task, lost_event

    async def _stop_heartbeat(self, task: asyncio.Task) -> None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as _stop_err:
            log.warning("escri.heartbeat_stop_error: %s", _stop_err)


    async def _process_job(self, job: dict) -> None:

        session_id = str(job["session_id"])
        heartbeat_task, heartbeat_lost = self._spawn_heartbeat(session_id)
        try:
            doc_id         = str(job["document_id"])
            schema         = str(job["schema_name"])
            user_id        = str(job["user_id"])
            reservation_id = str(job["reservation_id"]) if job.get("reservation_id") else None
            payload        = payload_as_dict(job.get("payload"))

            official_number      = payload.get("official_number")
            is_confirming_resume = bool(payload.get("is_confirming", False))

            is_autoheal = bool(payload.get("confirmed_autoheal", False))

            log.info(
                "escri.processing session=%s doc=%s schema=%s resume=%s autoheal=%s",
                session_id[:8], doc_id[:8], schema, is_confirming_resume, is_autoheal,
            )

            try:
                r2       = await get_tenant_r2_client(schema_name=schema)
                filename = doc_id.replace("-", "") + ".pdf"

                if not official_number:
                    official_number = await self._get_official_number(doc_id, schema)

                if is_autoheal:
                    log.info(
                        "escri.autoheal session=%s doc=%s num=%s "
                        "— PDF ya en oficial/; solo actualizar BD + sesión",
                        session_id[:8], doc_id[:8], official_number,
                    )
                    await self._mark_document_signed(doc_id, user_id, official_number, schema)
                    await self._mark_session_signed(session_id, official_number)
                    await self._audit_firma(
                        schema=schema, doc_id=doc_id, user_id=user_id,
                        session_id=session_id, official_number=official_number,
                    )
                    log.info("escri.autoheal.ok session=%s", session_id[:8])
                    return

                try:
                    _reservation_row = await fetch_one(
                        """
                        SELECT reservation_status
                        FROM official_documents
                        WHERE id = $1
                        """,
                        doc_id,
                        schema_name=schema,
                    )
                except Exception as _res_chk_err:
                    log.warning(
                        "escri.reservation_check_failed session=%s doc=%s err=%s "
                        "— reencolando (fail-closed GDI-276): no podemos saber "
                        "si el PDF ya fue firmado, re-firmar sería pisarlo",
                        session_id[:8], doc_id[:8], _res_chk_err,
                    )
                    await asyncio.shield(
                        self._requeue_guard_unverifiable(
                            session_id=session_id,
                            doc_id=doc_id,
                            payload=payload,
                            origen="reservation_check",
                            retry_after=30,
                        )
                    )
                    return
                if (
                    _reservation_row is not None
                    and _reservation_row.get("reservation_status") == "CONFIRMED"
                ):
                    log.warning(
                        "escri.resume.already_confirmed session=%s doc=%s num=%s "
                        "— reserva CONFIRMED antes de re-firmar (crash post-finalize, "
                        "pre-mark_session_signed). Desviando al camino autoheal para "
                        "NO pisar el PDF ya firmado (GDI-276 crítico 3).",
                        session_id[:8], doc_id[:8], official_number,
                    )
                    await self._mark_document_signed(doc_id, user_id, official_number, schema)
                    await self._mark_session_signed(session_id, official_number)
                    await self._audit_firma(
                        schema=schema, doc_id=doc_id, user_id=user_id,
                        session_id=session_id, official_number=official_number,
                    )
                    return

                await check_breaker_before_call()

                log.info("escri.download_tosign session=%s", session_id[:8])
                pdf_url = await run_in_threadpool(r2.get_tosign_url, filename)
                if not pdf_url:
                    raise RuntimeError("No se pudo obtener URL del PDF desde R2 tosign")

                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(pdf_url)
                    resp.raise_for_status()
                    content_length_hdr = resp.headers.get("content-length")
                    if content_length_hdr and int(content_length_hdr) > ESCRI_MAX_PDF_BYTES:
                        raise RuntimeError(
                            f"pdf_too_large: content-length {content_length_hdr} bytes "
                            f"> límite {ESCRI_MAX_PDF_BYTES} ({ESCRI_MAX_PDF_MB} MB)"
                        )
                    pdf_bytes = resp.content
                    if len(pdf_bytes) > ESCRI_MAX_PDF_BYTES:
                        raise RuntimeError(
                            f"pdf_too_large: {len(pdf_bytes)} bytes "
                            f"> límite {ESCRI_MAX_PDF_BYTES} ({ESCRI_MAX_PDF_MB} MB)"
                        )

                signer_data = await get_signer_data(user_id, schema_name=schema)
                city        = await get_city_from_settings(schema_name=schema)

                source_type = await self._get_source_type(doc_id, schema)
                stamp_position = "last" if source_type == "Importado" else ""
                if stamp_position:
                    log.info(
                        "escri.importado session=%s stamp_position=%s",
                        session_id[:8], stamp_position,
                    )

                if heartbeat_lost.is_set():
                    log.warning(
                        "escri.aborting_lost_fence session=%s doc=%s — no se llama "
                        "a Notary, otro worker ya tiene el claim",
                        session_id[:8], doc_id[:8],
                    )
                    return

                seal_inline = False

                log.info(
                    "escri.notary_sign session=%s seal_inline=%s",
                    session_id[:8], seal_inline,
                )
                signed_pdf = await call_notary_sign_pdf(
                    pdf_bytes=pdf_bytes,
                    signer_name=signer_data["full_name"],
                    signer_seal=signer_data["seal"],
                    signer_department=signer_data["department_name"],
                    signer_municipality=signer_data["municipality_name"],
                    official_number=official_number,
                    city=city,
                    stamp_position=stamp_position,
                    tenant_id=schema,
                    schema_name=schema,
                    defer_timestamp=not seal_inline,
                )
                log.info("escri.notary_ok session=%s bytes=%d", session_id[:8], len(signed_pdf))

                if not is_confirming_resume:
                    log.info("escri.confirm_number session=%s", session_id[:8])
                    try:
                        await confirm_number(doc_id, reservation_id, schema_name=schema)
                    except StaleReservationError:
                        try:
                            _od_stale = await fetch_one(
                                """
                                SELECT reservation_status, reservation_id::text
                                FROM official_documents
                                WHERE id = $1
                                """,
                                doc_id,
                                schema_name=schema,
                            )
                        except Exception as _stale_chk_err:
                            log.warning(
                                "escri.stale_check_failed session=%s err=%s "
                                "— reencolando (fail-closed GDI-276): sin poder "
                                "leer reservation_status no sabemos si auto-promover "
                                "a resume pisaría un PDF ya firmado",
                                session_id[:8], _stale_chk_err,
                            )
                            await asyncio.shield(
                                self._requeue_guard_unverifiable(
                                    session_id=session_id,
                                    doc_id=doc_id,
                                    payload=payload,
                                    origen="stale_check",
                                    retry_after=30,
                                )
                            )
                            return
                        if (
                            _od_stale is not None
                            and _od_stale["reservation_status"] in ("CONFIRMING", "CONFIRMED")
                            and _od_stale["reservation_id"] == reservation_id
                        ):
                            if _od_stale["reservation_status"] == "CONFIRMED":
                                log.warning(
                                    "escri.stale_already_confirmed session=%s doc=%s num=%s "
                                    "— reserva ya CONFIRMED al momento del CAS (otro worker "
                                    "cerró el ciclo). Desviando al camino idempotente para "
                                    "NO pisar el PDF (GDI-276 crítico 3, 2ª puerta).",
                                    session_id[:8], doc_id[:8], official_number,
                                )
                                await self._mark_document_signed(doc_id, user_id, official_number, schema)
                                await self._mark_session_signed(session_id, official_number)
                                await self._audit_firma(
                                    schema=schema, doc_id=doc_id, user_id=user_id,
                                    session_id=session_id, official_number=official_number,
                                )
                                return
                            log.info(
                                "escri.stale_is_own_confirm session=%s doc=%s od_status=%s "
                                "— confirm anterior ya aplicado; continuando upload (RESUME)",
                                session_id[:8], doc_id[:8], _od_stale["reservation_status"],
                            )
                            is_confirming_resume = True
                        else:
                            log.warning(
                                "escri.stale_superseded session=%s doc=%s od_status=%s "
                                "— reserva pertenece a otro proceso; abortando sin cancelar",
                                session_id[:8], doc_id[:8],
                                _od_stale["reservation_status"] if _od_stale else "N/A",
                            )
                            await self._mark_session_failed(session_id, "superseded")
                            return

                log.info("escri.upload_fence session=%s", session_id[:8])
                fence_row = await fetch_one(
                    """
                    UPDATE public.signing_sessions
                    SET updated_at = NOW()
                    WHERE session_id = $1::uuid
                      AND claimed_by = $2
                      AND status     = 'processing'
                    RETURNING session_id
                    """,
                    session_id,
                    self._worker_id,
                    schema_name="public",
                )
                if not fence_row:
                    log.warning(
                        "escri.upload_fence.lost session=%s — otro worker tomó el relevo; "
                        "abortando upload (el PDF será subido por el worker que ganó)",
                        session_id[:8],
                    )
                    return

                from services.storage.pdf_location import target_pdf_location
                pdf_location_target = target_pdf_location()
                log.info(
                    "escri.upload_oficial session=%s target=%s",
                    session_id[:8], pdf_location_target,
                )
                oficial_filename = f"{official_number}.pdf"
                _upload_res = await run_in_threadpool(
                    r2.upload_oficial, signed_pdf, oficial_filename, pdf_location_target
                )
                from services.storage.pdf_location import (
                    effective_pdf_location, persist_pdf_location,
                )
                pdf_location_effective = effective_pdf_location(_upload_res, pdf_location_target)

                await persist_pdf_location(
                    doc_id, pdf_location_effective,
                    schema_name=schema, official_number=official_number,
                )

                log.info("escri.finalize_number session=%s", session_id[:8])
                await finalize_number(doc_id, reservation_id, schema_name=schema)

                try:
                    await self._publish_public_with_retry(
                        schema_name=schema,
                        official_number=official_number,
                        document_id=doc_id,
                        document_type_id=None,
                        signed_pdf_bytes=signed_pdf,
                        session_id=session_id,
                        payload={**payload, "official_number": official_number},
                    )
                except Exception as _pub_err:
                    log.warning(
                        "escri.publish_public_failed session=%s doc=%s num=%s "
                        "error=%s (soft-fail, GDI-253 no bloquea la firma)",
                        session_id[:8], doc_id[:8], official_number, _pub_err,
                    )

                try:
                    from services.documents.lifecycle.images import purge_document_images
                    await purge_document_images(doc_id, schema_name=schema)
                except Exception as _img_purge_err:
                    log.warning(
                        "escri.purge_images_failed session=%s doc=%s error=%s (soft-fail)",
                        session_id[:8], doc_id[:8], _img_purge_err,
                    )

                try:
                    from services.documents.lifecycle.embedded_files import promote_embedded_files_to_official
                    await promote_embedded_files_to_official(doc_id, doc_id, schema_name=schema)
                except Exception as _emb_purge_err:
                    log.warning(
                        "escri.promote_embedded_files_failed session=%s doc=%s error=%s (soft-fail)",
                        session_id[:8], doc_id[:8], _emb_purge_err,
                    )

                log.info("escri.update_db session=%s", session_id[:8])
                await self._mark_document_signed(doc_id, user_id, official_number, schema)

                await self._mark_session_signed(session_id, official_number)
                await self._audit_firma(
                    schema=schema, doc_id=doc_id, user_id=user_id,
                    session_id=session_id, official_number=official_number,
                    r2_object_key=oficial_filename,
                )


                log.info(
                    "escri.ok session=%s doc=%s num=%s seal_inline=%s",
                    session_id[:8], doc_id[:8], official_number, seal_inline,
                )

            except DocumentRejectedWhileInQueueError:
                _od_rejected_status = None
                try:
                    _od_check_rej = await fetch_one(
                        "SELECT reservation_status FROM official_documents WHERE id = $1",
                        doc_id,
                        schema_name=schema,
                    )
                    if _od_check_rej:
                        _od_rejected_status = _od_check_rej["reservation_status"]
                except Exception as _rej_status_err:
                    log.warning(
                        "escri.doc_rejected_status_check_failed session=%s: %s",
                        session_id[:8], _rej_status_err,
                    )

                if _od_rejected_status == "CONFIRMED":
                    log.error(
                        "escri.confirmed_and_rejected session=%s doc=%s num=%s "
                        "— doc en CONFIRMED pero fue rechazado; PDF preservado, número NO "
                        "cancelado. Requiere revisión manual.",
                        session_id[:8], doc_id[:8], official_number,
                    )
                    try:
                        await send_alert_mail(
                            subject=(
                                f"[GDI ESCRI] Conflicto CONFIRMED+rechazado — {official_number}"
                            ),
                            body=(
                                f"correlationId={session_id[:8]}\n"
                                f"doc={doc_id}\nnum={official_number}\nschema={schema}\n\n"
                                f"El documento fue firmado (CONFIRMED, PDF en oficial/) "
                                f"pero también fue rechazado mientras el job procesaba. "
                                f"El número NO fue cancelado y el PDF NO fue borrado. "
                                f"Requiere revisión y resolución manual."
                            ),
                        )
                    except Exception as _ae:
                        log.error("escri.confirmed_and_rejected.alert_err: %s", _ae)
                    await self._mark_session_failed(session_id, "confirmed_and_rejected_conflict")
                else:
                    log.warning(
                        "escri.doc_rejected_in_queue session=%s doc=%s num=%s od_status=%s "
                        "— documento rechazado antes de comprometer el número; abortando",
                        session_id[:8], doc_id[:8], official_number, _od_rejected_status,
                    )
                    try:
                        r2_del = await get_tenant_r2_client(schema_name=schema)
                        oficial_filename_del = f"{official_number}.pdf"
                        await run_in_threadpool(r2_del.delete_oficial, oficial_filename_del)
                        log.info(
                            "escri.doc_rejected_in_queue.pdf_deleted session=%s num=%s",
                            session_id[:8], official_number,
                        )
                    except Exception as _del_err:
                        log.warning(
                            "escri.doc_rejected_in_queue.delete_failed session=%s: %s (soft-fail)",
                            session_id[:8], _del_err,
                        )
                    if not is_confirming_resume and reservation_id:
                        try:
                            await cancel_number(
                                doc_id,
                                schema_name=schema,
                                reason="document_rejected_while_in_queue",
                                reservation_id=reservation_id,
                            )
                        except Exception as _cn_err:
                            log.exception(
                                "escri.doc_rejected_in_queue.cancel_failed session=%s: %s",
                                session_id[:8], _cn_err,
                            )
                    await self._mark_session_failed(session_id, "document_no_longer_signable")

            except asyncio.CancelledError:
                await self._requeue_in_flight_on_cancel(session_id, doc_id)
                raise

            except NotaryBreakerOpenError as exc:
                log.warning(
                    "escri.breaker_open session=%s doc=%s retry_after=%ds "
                    "— devolviendo a pending con available_at (sin cancel_number)",
                    session_id[:8], doc_id[:8], exc.retry_after,
                )
                await asyncio.shield(self._requeue_session_pending(session_id, retry_after=exc.retry_after))

            except Exception as exc:
                log.exception(
                    "escri.failed session=%s doc=%s err=%s",
                    session_id[:8], doc_id[:8], exc,
                )

                _superseded = False
                try:
                    _od_row = await fetch_one(
                        """
                        SELECT reservation_status
                        FROM official_documents
                        WHERE id = $1
                        """,
                        doc_id,
                        schema_name=schema,
                    )
                    if _od_row and _od_row["reservation_status"] in ("CONFIRMING", "CONFIRMED"):
                        _superseded = True
                        log.warning(
                            "escri.superseded session=%s doc=%s od_status=%s "
                            "— otro worker terminó primero; no cancelar ni marcar failed",
                            session_id[:8], doc_id[:8], _od_row["reservation_status"],
                        )
                except Exception as _chk_err:
                    log.warning(
                        "escri.superseded_check_failed session=%s: %s",
                        session_id[:8], _chk_err,
                    )

                if _superseded:
                    if (
                        _od_row
                        and _od_row["reservation_status"] == "CONFIRMED"
                        and official_number
                    ):
                        try:
                            await self._mark_session_signed(session_id, official_number)
                        except Exception as _mark_err:
                            log.error(
                                "escri.superseded.session_mark_failed session=%s: %s "
                                "(el poll self-healing lo reconcilia)",
                                session_id[:8], _mark_err,
                            )
                    return

                if not is_confirming_resume and reservation_id:
                    try:
                        await cancel_number(
                            doc_id,
                            schema_name=schema,
                            reason=f"escri_worker_failed:{_failure_code(exc)}",
                            reservation_id=reservation_id,
                        )
                    except Exception:
                        log.exception(
                            "escri.cancel_number_failed session=%s", session_id[:8]
                        )
                await self._audit_firma(
                    schema=schema, doc_id=doc_id, user_id=user_id,
                    session_id=session_id, result="fail",
                    failure_reason=str(exc)[:300],
                )
                await self._mark_session_failed(session_id, _failure_code(exc))

        finally:
            await self._stop_heartbeat(heartbeat_task)


    async def _process_common_job(self, job: dict) -> None:
        session_id = str(job["session_id"])
        heartbeat_task, heartbeat_lost = self._spawn_heartbeat(session_id)

        doc_id  = str(job["document_id"])
        schema  = str(job["schema_name"])
        user_id = str(job["user_id"])

        from services.documents.signing.r2_lock import (
            release_signing_lock_R2_success,
            release_signing_lock_R2_fail,
        )
        from services.documents.signing.audit_logger import log_signature_event
        from services.shared.signer_data import get_signer_data
        from services.shared.notary_api import call_notary_sign_pdf
        from services.documents.core.queries import update_signer_status_to_signed_query
        from services.r2_client import r2_get_object, R2KeyNotFound

        inprocess_key = f"inprocess/{doc_id.replace('-', '')}.pdf"
        failure_reason: str | None = None
        signed_uploaded = False

        try:
            log.info(
                "escri.common.processing session=%s doc=%s schema=%s",
                session_id[:8], doc_id[:8], schema,
            )

            signer_row = await fetch_one(
                "SELECT signed_at FROM document_signers WHERE document_id = $1 AND user_id = $2",
                doc_id, user_id, schema_name=schema,
            )
            if signer_row and signer_row["signed_at"] is not None:
                log.info(
                    "escri.common.already_signed session=%s doc=%s — reentrada tras "
                    "re-encole, solo cerrando sesión",
                    session_id[:8], doc_id[:8],
                )
                await self._mark_session_signed_common(session_id)
                return

            sess_row = await fetch_one(
                "SELECT payload FROM public.signing_sessions WHERE session_id = $1::uuid",
                session_id, schema_name="public",
            )
            sess_payload = payload_as_dict(sess_row["payload"]) if sess_row else {}
            unsigned_sha256_saved = sess_payload.get("unsigned_sha256")

            async def _resume_after_upload(reason: str) -> None:
                log.warning(
                    "escri.common.resume_after_upload session=%s doc=%s reason=%s "
                    "— reintentando UPDATE + cierre sin re-firmar",
                    session_id[:8], doc_id[:8], reason,
                )
                await execute(
                    """
                    UPDATE public.signing_sessions
                    SET payload    = COALESCE(payload, '{}'::jsonb)
                                     || jsonb_build_object('signed_uploaded', true),
                        updated_at = NOW()
                    WHERE session_id = $1::uuid
                    """,
                    session_id, schema_name="public",
                )
                await self._update_signer_signed_with_retry(doc_id, user_id, schema)
                await self._mark_session_signed_common(session_id)
                await log_signature_event(
                    schema_name=schema, document_id=doc_id, user_id=user_id,
                    signature_method="electronic", result="ok",
                    r2_object_key=inprocess_key,
                )
                try:
                    from services.r2_client import r2_delete
                    await r2_delete(schema_name=schema, key=inprocess_key)
                    log.info(
                        "escri.common.resume.inprocess_cleaned session=%s doc=%s",
                        session_id[:8], doc_id[:8],
                    )
                except R2KeyNotFound:
                    pass
                except Exception as e:
                    log.critical(
                        "escri.common.resume.inprocess_delete_failed session=%s doc=%s "
                        "key=%s error=%s — BORRAR A MANO o el siguiente firmante "
                        "queda bloqueado (409 document_already_signing)",
                        session_id[:8], doc_id[:8], inprocess_key, e,
                    )

            if sess_payload.get("signed_uploaded") is True:
                signed_uploaded = True
                await _resume_after_upload("marker_signed_uploaded")
                return

            if unsigned_sha256_saved:
                tosign_key_pre = f"{doc_id.replace('-', '')}.pdf"
                try:
                    tosign_pre = await r2_get_object(
                        schema_name=schema, key=tosign_key_pre, bucket="tosign",
                    )
                    if hashlib.sha256(tosign_pre).hexdigest() != unsigned_sha256_saved:
                        signed_uploaded = True
                        await _resume_after_upload("hash_mismatch_reentry_inprocess_present")
                        return
                except R2KeyNotFound:
                    pass

            if heartbeat_lost.is_set():
                log.warning(
                    "escri.common.aborting_lost_fence session=%s doc=%s",
                    session_id[:8], doc_id[:8],
                )
                return

            try:
                pdf_bytes = await r2_get_object(schema_name=schema, key=inprocess_key, bucket="tosign")
            except R2KeyNotFound:
                if not unsigned_sha256_saved:
                    raise RuntimeError(
                        f"PDF no encontrado en R2 inprocess: {inprocess_key} "
                        "(sesión sin unsigned_sha256, no se puede distinguir upload perdido)"
                    )
                tosign_key = f"{doc_id.replace('-', '')}.pdf"
                try:
                    current_tosign = await r2_get_object(
                        schema_name=schema, key=tosign_key, bucket="tosign",
                    )
                except R2KeyNotFound:
                    raise RuntimeError(
                        f"PDF ausente en R2 inprocess y tosign: {inprocess_key} / {tosign_key}"
                    )
                current_sha = hashlib.sha256(current_tosign).hexdigest()
                if current_sha != unsigned_sha256_saved:
                    signed_uploaded = True
                    await _resume_after_upload("hash_mismatch_signed_upload_lost_marker")
                    return
                raise RuntimeError(
                    "Lock R2 perdido: inprocess ausente, tosign coincide con "
                    "unsigned_sha256 — se requiere intervención manual"
                )

            unsigned_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
            await execute(
                """
                UPDATE public.signing_sessions
                SET payload    = COALESCE(payload, '{}'::jsonb)
                                 || jsonb_build_object('unsigned_sha256', $2::text),
                    updated_at = NOW()
                WHERE session_id = $1::uuid
                """,
                session_id, unsigned_sha256, schema_name="public",
            )

            signer_data = await get_signer_data(user_id, schema_name=schema)

            await check_breaker_before_call()
            if heartbeat_lost.is_set():
                log.warning(
                    "escri.common.aborting_lost_fence_pre_notary session=%s doc=%s",
                    session_id[:8], doc_id[:8],
                )
                return

            log.info("escri.common.notary_sign session=%s", session_id[:8])
            signed_pdf = await call_notary_sign_pdf(
                pdf_bytes=pdf_bytes,
                signer_name=signer_data["full_name"],
                signer_seal=signer_data["seal"],
                signer_department=signer_data["department_name"],
                signer_municipality=signer_data["municipality_name"],
                official_number="",
                city="",
                tenant_id=schema,
                schema_name=schema,
                defer_timestamp=True,
            )
            log.info("escri.common.notary_ok session=%s bytes=%d", session_id[:8], len(signed_pdf))

            fence_row = await fetch_one(
                """
                UPDATE public.signing_sessions
                SET updated_at = NOW()
                WHERE session_id = $1::uuid
                  AND claimed_by = $2
                  AND status     = 'processing'
                RETURNING session_id
                """,
                session_id, self._worker_id, schema_name="public",
            )
            if not fence_row:
                log.warning("escri.common.upload_fence.lost session=%s", session_id[:8])
                return

            await release_signing_lock_R2_success(
                schema_name=schema, doc_id=doc_id, signed_pdf=signed_pdf,
                is_numerator=False, number=None, session_id=session_id,
            )
            signed_uploaded = True

            await execute(
                """
                UPDATE public.signing_sessions
                SET payload    = COALESCE(payload, '{}'::jsonb)
                                 || jsonb_build_object('signed_uploaded', true),
                    updated_at = NOW()
                WHERE session_id = $1::uuid
                """,
                session_id, schema_name="public",
            )

            await self._update_signer_signed_with_retry(doc_id, user_id, schema)

            await self._mark_session_signed_common(session_id)

            await log_signature_event(
                schema_name=schema, document_id=doc_id, user_id=user_id,
                signature_method="electronic", result="ok", r2_object_key=inprocess_key,
            )
            log.info("escri.common.ok session=%s doc=%s", session_id[:8], doc_id[:8])

        except Exception as exc:
            failure_reason = str(exc)[:300]
            log.exception(
                "escri.common.failed session=%s doc=%s err=%s",
                session_id[:8], doc_id[:8], exc,
            )
            if not signed_uploaded:
                try:
                    await release_signing_lock_R2_fail(schema_name=schema, doc_id=doc_id)
                except Exception:
                    log.exception("escri.common.lock_rollback_failed session=%s", session_id[:8])
            try:
                await log_signature_event(
                    schema_name=schema, document_id=doc_id, user_id=user_id,
                    signature_method="electronic", result="fail",
                    failure_reason=failure_reason,
                )
            except Exception:
                pass
            if signed_uploaded:
                await self._requeue_or_fail_post_upload(
                    session_id=session_id, doc_id=doc_id, exc=exc,
                )
            else:
                await self._mark_session_failed(session_id, _failure_code(exc))
        finally:
            await self._stop_heartbeat(heartbeat_task)

    async def _update_signer_signed_with_retry(
        self, doc_id: str, user_id: str, schema: str
    ) -> None:
        from services.documents.core.queries import update_signer_status_to_signed_query
        from tenacity import (
            AsyncRetrying, stop_after_attempt, wait_exponential,
            retry_if_exception_type,
        )

        transient = (
            asyncpg.PostgresConnectionError,
            asyncpg.InterfaceError,
            DatabaseBusyError,
            asyncio.TimeoutError,
            ConnectionError,
            OSError,
        )

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.2, min=0.2, max=2.0),
            retry=retry_if_exception_type(transient),
            reraise=True,
        ):
            with attempt:
                status_str = await execute(
                    update_signer_status_to_signed_query(),
                    doc_id, user_id, schema_name=schema,
                )
                rows_affected = int(status_str.split()[-1]) if status_str else 0
                if rows_affected == 0:
                    raise RuntimeError("No se pudo actualizar el estado del firmante")

    async def _mark_session_signed_common(self, session_id: str) -> None:
        tag = await execute(
            """
            UPDATE public.signing_sessions
            SET status = 'signed', updated_at = NOW()
            WHERE session_id = $1::uuid AND status = 'processing' AND claimed_by = $2
            """,
            session_id, self._worker_id, schema_name="public",
        )
        if tag == "UPDATE 0":
            recovered = await execute(
                """
                UPDATE public.signing_sessions
                SET status = 'signed', updated_at = NOW(), claimed_by = $2
                WHERE session_id = $1::uuid
                  AND (
                        status = 'pending'
                     OR (status = 'processing' AND (claimed_by IS NULL OR claimed_by = $2))
                      )
                """,
                session_id, self._worker_id, schema_name="public",
            )
            if recovered == "UPDATE 0":
                log.critical(
                    "escri.common.session_mark_blocked_active_claim session=%s "
                    "— otro worker tiene el claim activo; NO se fuerza signed",
                    session_id[:8],
                )
            else:
                log.warning(
                    "escri.common.session_mark_recovered session=%s result=%s",
                    session_id[:8], recovered,
                )


    async def _get_official_number(self, doc_id: str, schema: str) -> str:
        row = await fetch_one(
            """
            SELECT official_number
            FROM official_documents
            WHERE id = $1
              AND reservation_status IN ('RESERVED', 'CONFIRMING')
            """,
            doc_id,
            schema_name=schema,
        )
        if not row or not row["official_number"]:
            raise RuntimeError(
                f"official_number no encontrado para doc {doc_id[:8]} "
                f"(schema={schema})"
            )
        return str(row["official_number"])

    async def _get_source_type(self, doc_id: str, schema: str) -> str:
        row = await fetch_one(
            """
            SELECT dt.type
            FROM official_documents od
            JOIN document_types dt ON od.document_type_id = dt.id
            WHERE od.id = $1
            """,
            doc_id,
            schema_name=schema,
        )
        return str(row["type"]) if row and row["type"] else "HTML"


    async def _process_citizen_job(self, job: dict) -> None:
        session_id = str(job["session_id"])
        schema = job["schema_name"]
        doc_id = str(job["document_id"])
        citizen_id = str(job.get("citizen_id") or "")

        if not citizen_id:
            log.error(
                "escri.citizen.sin_citizen_id session=%s doc=%s — job corrupto",
                session_id[:8], doc_id[:8],
            )
            await self._mark_session_failed(session_id, "citizen_job_sin_citizen_id")
            return

        log.info(
            "escri.citizen.start session=%s doc=%s citizen=%s schema=%s",
            session_id[:8], doc_id[:8], citizen_id[:8], schema,
        )

        payload = payload_as_dict(job.get("payload") or {})
        if payload.get("pdf_pendiente"):
            try:
                await self._armar_pdf_del_ciudadano(
                    schema=schema, document_id=doc_id,
                    citizen_id=citizen_id, payload=payload,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "escri.citizen.pdf_failed session=%s doc=%s: %s",
                    session_id[:8], doc_id[:8], exc,
                )
                await self._mark_session_failed(session_id, _failure_code(exc))
                await self._avisar_tad(
                    schema=schema, document_id=doc_id, session_id=session_id,
                    exito=False, resultado=None, error=_failure_code(exc),
                )
                return

        try:
            from services.documents.signing.citizen_signing import (
                sign_and_number_citizen_document,
            )
            resultado = await sign_and_number_citizen_document(
                doc_id, citizen_id, schema_name=schema
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "escri.citizen.failed session=%s doc=%s: %s",
                session_id[:8], doc_id[:8], exc,
            )
            await self._mark_session_failed(session_id, _failure_code(exc))
            await self._avisar_tad(
                schema=schema, document_id=doc_id, session_id=session_id,
                exito=False, resultado=None, error=_failure_code(exc),
            )
            return

        official_number = resultado.get("official_number")
        await self._mark_session_signed(session_id, official_number or "")

        log.info(
            "escri.citizen.ok session=%s doc=%s num=%s",
            session_id[:8], doc_id[:8], official_number,
        )

        await self._avisar_tad(
            schema=schema, document_id=doc_id, session_id=session_id,
            exito=True, resultado=resultado, error=None,
        )


    async def _armar_pdf_del_ciudadano(
        self, *, schema: str, document_id: str, citizen_id: str, payload: dict
    ) -> None:
        original_status = str(payload.get("original_status") or "draft")
        import_pendiente = payload.get("import_pendiente") or None

        if import_pendiente:
            await self._procesar_pdf_importado(
                schema=schema, document_id=document_id,
                datos=import_pendiente, original_status=original_status,
            )

        from services.documents.signing.signing import generar_pdf_y_finalizar
        await generar_pdf_y_finalizar(
            document_id, citizen_id,
            schema_name=schema,
            original_status=original_status,
        )

    async def _procesar_pdf_importado(
        self, *, schema: str, document_id: str, datos: dict, original_status: str
    ) -> None:
        from services.storage.cloudflare import get_tenant_r2_client
        from services.shared.pdfcomposer_api import call_pdfcomposer_import
        from shared.exceptions import ExternalServiceError

        raw_filename = str(datos.get("raw_filename") or "")
        final_filename = document_id.replace("-", "") + ".pdf"
        if not raw_filename:
            raise ExternalServiceError(
                "import_pendiente sin raw_filename: no se puede recuperar el PDF del portal"
            )

        r2 = await get_tenant_r2_client(schema_name=schema)

        existe_crudo = await run_in_threadpool(r2.exists_tosign, raw_filename)
        if not existe_crudo:
            if await run_in_threadpool(r2.exists_tosign, final_filename):
                log.info(
                    "escri.citizen.import_ya_hecho doc=%s — el PDF definitivo ya "
                    "estaba en R2 y el crudo ya no; se saltea (reintento)",
                    document_id[:8],
                )
                return
            raise ExternalServiceError(
                f"El PDF que subio el portal no esta en R2 ({raw_filename}) y "
                "tampoco existe el definitivo: no hay nada que firmar"
            )

        try:
            url_crudo = await run_in_threadpool(r2.get_tosign_url, raw_filename)
            if not url_crudo:
                raise ExternalServiceError(
                    "No se pudo generar URL firmada para el PDF crudo del portal"
                )
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(url_crudo)
                resp.raise_for_status()
                pdf_bytes = resp.content

            procesado = await call_pdfcomposer_import(
                pdf_file=pdf_bytes,
                filename="documento.pdf",
                url_logo=datos.get("url_logo"),
                name_acrony_type=datos.get("name_acrony_type"),
                document_type=datos.get("document_type"),
                reference=datos.get("reference"),
                schema_name=schema,
            )
            await run_in_threadpool(r2.upload_tosign, procesado, final_filename)
            log.info(
                "escri.citizen.import_ok doc=%s bytes=%d",
                document_id[:8], len(procesado or b""),
            )
        except Exception:
            try:
                await execute(
                    "UPDATE document_draft SET status = $1 WHERE id = $2 AND status = 'sent_to_sign'",
                    original_status, document_id,
                    schema_name=schema,
                )
            except Exception as rollback_err:  # noqa: BLE001
                log.critical(
                    "escri.citizen.import_rollback_failed doc=%s — el documento "
                    "queda trabado en 'sent_to_sign'. Requiere revision manual: %s",
                    document_id[:8], rollback_err,
                )
            raise

        try:
            await run_in_threadpool(r2.delete_tosign, raw_filename)
        except Exception as del_err:  # noqa: BLE001
            log.warning(
                "escri.citizen.import_raw_no_borrado doc=%s file=%s (soft-fail): %s",
                document_id[:8], raw_filename, del_err,
            )

    async def _process_digital_complete_job(self, job: dict) -> None:
        session_id = str(job["session_id"])
        schema = job["schema_name"]
        doc_id = str(job["document_id"])
        user_id = str(job.get("user_id") or "")
        reservation_id = job.get("reservation_id")

        payload = payload_as_dict(job.get("payload") or {})

        dss_id = str(payload.get("digital_session_id") or "")
        if not dss_id:
            log.error(
                "escri.digital.sin_session session=%s doc=%s — job corrupto",
                session_id[:8], doc_id[:8],
            )
            await self._mark_session_failed(session_id, "digital_job_sin_session_id")
            return

        log.info(
            "escri.digital.start session=%s doc=%s dss=%s schema=%s",
            session_id[:8], doc_id[:8], dss_id[:12], schema,
        )

        try:
            from services.documents.signing.digital_completion import (
                cerrar_firma_digital,
                marcar_sesion_digital,
            )

            resultado = await cerrar_firma_digital(
                schema_name=schema,
                document_id=doc_id,
                user_id=user_id,
                reservation_id=str(reservation_id) if reservation_id else None,
                official_number=payload.get("official_number"),
                digital_session_id=dss_id,
                is_numerator=bool(payload.get("is_numerator")),
                cas_pre_done=bool(payload.get("cas_pre_done")),
                cert=payload.get("cert") or {},
                file_id=payload.get("file_id"),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "escri.digital.failed session=%s doc=%s: %s",
                session_id[:8], doc_id[:8], exc,
            )
            await self._mark_session_failed(session_id, _failure_code(exc))
            try:
                from services.documents.signing.digital_completion import (
                    marcar_sesion_digital,
                )

                await marcar_sesion_digital(dss_id, "failed", _failure_code(exc))
            except Exception as _e:
                log.warning("escri.digital.marcar_sesion soft-fail: %s", _e)
            return

        if not resultado.get("ok"):
            motivo = resultado.get("failure_reason") or "digital_complete_failed"
            log.error(
                "escri.digital.no_ok session=%s doc=%s motivo=%s",
                session_id[:8], doc_id[:8], motivo,
            )
            await self._mark_session_failed(session_id, motivo)
            await marcar_sesion_digital(dss_id, "failed", motivo)
            if resultado.get("tanda_puede_caer"):
                await self._tirar_la_tanda_si_es_de_una(dss_id, schema, motivo)
            return

        await self._mark_session_signed(session_id, payload.get("official_number") or "")
        log.info(
            "escri.digital.ok session=%s doc=%s num=%s",
            session_id[:8], doc_id[:8], payload.get("official_number"),
        )


    async def _tirar_la_tanda_si_es_de_una(self, dss_id: str, schema: str, motivo: str) -> None:
        try:
            from database import fetch_one
            from services.documents.signing.batch_digital import cancelar_tanda

            fila = await fetch_one(
                """
                SELECT batch_id::text AS batch_id
                FROM public.digital_signature_sessions
                WHERE session_id = $1
                """,
                dss_id, schema_name="public",
            )
            if not fila or not fila.get("batch_id"):
                return
            log.warning(
                "escri.digital.tanda_arrastrada batch=%s por session=%s motivo=%s",
                str(fila["batch_id"])[:8], dss_id[:8], motivo,
            )
            await cancelar_tanda(fila["batch_id"], schema_name=schema, motivo=motivo)
        except Exception as exc:
            log.warning(
                "escri.digital.tanda_arrastrada soft-fail session=%s: %s — lo agarra el sweeper",
                dss_id[:8], exc,
            )


    async def _url_pdf_ciudadano(self, schema: str, resultado: dict) -> str | None:
        official_number = resultado.get("official_number")
        if not official_number:
            return None
        try:
            from services.storage.cloudflare import get_tenant_r2_client

            r2 = await get_tenant_r2_client(schema_name=schema)
            return await run_in_threadpool(
                r2.get_oficial_url,
                official_number,
                resultado.get("pdf_location") or "oficial",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "escri.citizen.url_pdf_failed schema=%s num=%s: %s — el webhook "
                "sale igual, con el número",
                schema, official_number, exc,
            )
            return None

    async def _avisar_tad(
        self, *, schema: str, document_id: str, session_id: str,
        exito: bool, resultado: dict | None, error: str | None,
    ) -> None:
        try:
            from services.webhooks.tad_notify import (
                get_tad_webhook_config, enqueue_tad_webhook,
            )

            config = await get_tad_webhook_config(schema_name=schema)
            if not config:
                log.warning(
                    "escri.citizen.sin_webhook schema=%s doc=%s — el municipio no "
                    "tiene webhook TAD configurado, el portal no se entera",
                    schema, document_id[:8],
                )
                return

            if exito:
                payload = {
                    "document_id": document_id,
                    "official_number": (resultado or {}).get("official_number"),
                    "pdf_url": await self._url_pdf_ciudadano(schema, resultado or {}),
                    "status": "signed",
                }
                evento = "documents.signed"
            else:
                payload = {
                    "document_id": document_id,
                    "status": "failed",
                    "failure_reason": error,
                }
                evento = "documents.signature_failed"

            await enqueue_tad_webhook(
                schema_name=schema,
                api_key_id=str(config["api_key_id"]),
                event_type=evento,
                payload=payload,
            )
            log.info(
                "escri.citizen.webhook_encolado session=%s evento=%s",
                session_id[:8], evento,
            )
        except Exception as exc:  # noqa: BLE001 — ver docstring
            log.error(
                "escri.citizen.webhook_failed session=%s doc=%s: %s — el portal "
                "no se entera de esta firma",
                session_id[:8], document_id[:8], exc,
            )

    async def _publish_public_with_retry(
        self,
        *,
        schema_name: str,
        official_number: str,
        document_id: str,
        document_type_id,
        signed_pdf_bytes: bytes,
        session_id: str,
        payload: dict,
    ) -> None:
        from services.storage.publish_public import maybe_publish_official_pdf

        for attempt in range(1, PUBLISH_PUBLIC_MAX_RETRIES + 1):
            ok = await maybe_publish_official_pdf(
                schema_name=schema_name,
                official_number=official_number,
                document_id=document_id,
                document_type_id=document_type_id,
                signed_pdf_bytes=signed_pdf_bytes,
            )
            if ok:
                return
            log.warning(
                "escri.dts.publish_public_retry document_id=%s schema=%s attempt=%d/%d",
                document_id[:8], schema_name, attempt, PUBLISH_PUBLIC_MAX_RETRIES,
            )
            if attempt < PUBLISH_PUBLIC_MAX_RETRIES:
                await asyncio.sleep(1.0)

        log.error(
            "publish_public_failed document_id=%s schema=%s num=%s attempts=%d",
            document_id, schema_name, official_number, PUBLISH_PUBLIC_MAX_RETRIES,
        )
        try:
            new_payload = {**payload, "publish_failed": True}
            await execute(
                """
                UPDATE public.signing_sessions
                SET payload = $1::jsonb, updated_at = NOW()
                WHERE session_id = $2::uuid
                """,
                new_payload,
                session_id,
                schema_name="public",
            )
        except Exception as _mark_err:
            log.error(
                "escri.dts.publish_failed_marker_error session=%s: %s",
                session_id[:8], _mark_err,
            )

    async def _mark_dts_signed(self, session_id: str) -> None:
        await execute(
            """
            UPDATE public.signing_sessions
            SET status     = 'signed',
                updated_at = NOW()
            WHERE session_id = $1::uuid
              AND job_type   = 'dts'
              AND status     = 'processing'
            """,
            session_id,
            schema_name="public",
        )

    async def _mark_document_signed(
        self, doc_id: str, user_id: str, official_number: str, schema: str
    ) -> None:
        async with transaction(
            schema_name=schema, user_id=user_id, auth_source="escri_worker"
        ) as conn:
            await conn.execute(
                """
                UPDATE official_documents
                SET signed_at = CURRENT_TIMESTAMP
                WHERE id = $1 AND signed_at IS NULL
                """,
                doc_id,
            )
            await conn.execute(
                """
                UPDATE official_documents
                SET signers = (
                    SELECT jsonb_agg(
                        CASE WHEN s->>'user_id' = $1
                            THEN jsonb_set(
                                     jsonb_set(s, '{status}', '"signed"'),
                                     '{signed_at}',
                                     to_jsonb(to_char(
                                         CURRENT_TIMESTAMP AT TIME ZONE 'UTC',
                                         'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
                                     ))
                                 )
                            ELSE s
                        END
                    )
                    FROM jsonb_array_elements(signers) s
                )
                WHERE id = $2
                """,
                user_id,
                doc_id,
            )
            await conn.execute(
                """
                UPDATE document_signers
                SET status = 'signed', signed_at = CURRENT_TIMESTAMP
                WHERE document_id = $1 AND user_id = $2
                """,
                doc_id,
                user_id,
            )
            result = await conn.fetch(
                """
                UPDATE document_draft
                SET status          = 'signed',
                    document_number = $1,
                    numbered_at     = CURRENT_TIMESTAMP,
                    numbered_by     = $2,
                    last_modified_at = CURRENT_TIMESTAMP
                WHERE id = $3
                  AND status = 'sent_to_sign'
                RETURNING id
                """,
                official_number,
                user_id,
                doc_id,
            )
            if not result:
                raise DocumentRejectedWhileInQueueError(doc_id)

    async def _audit_firma(
        self,
        *,
        schema: str,
        doc_id: str,
        user_id: str,
        session_id: str,
        official_number: str | None = None,
        r2_object_key: str | None = None,
        result: str = "ok",
        failure_reason: str | None = None,
    ) -> None:
        try:
            from services.documents.signing.audit_logger import log_signature_event

            await log_signature_event(
                schema_name=schema,
                document_id=doc_id,
                user_id=user_id,
                signature_method="electronic",
                result=result,
                session_id=session_id,
                official_number=official_number,
                failure_reason=failure_reason,
                r2_object_key=r2_object_key,
            )
        except Exception:
            log.exception(
                "escri.audit_soft_fail session=%s doc=%s result=%s",
                session_id[:8], doc_id[:8], result,
            )

    async def _mark_session_signed(self, session_id: str, official_number: str) -> None:
        tag = await execute(
            """
            UPDATE public.signing_sessions
            SET status     = 'signed',
                updated_at = NOW(),
                payload    = COALESCE(payload, '{}'::jsonb)
                             || jsonb_build_object('official_number', $1::text)
            WHERE session_id = $2::uuid
              AND status     = 'processing'
              AND claimed_by = $3
            """,
            official_number,
            session_id,
            self._worker_id,
            schema_name="public",
        )
        if tag == "UPDATE 0":
            recovered = await execute(
                """
                UPDATE public.signing_sessions
                SET status     = 'signed',
                    updated_at = NOW(),
                    claimed_by = $3,
                    payload    = COALESCE(payload, '{}'::jsonb)
                                 || jsonb_build_object('official_number', $1::text)
                WHERE session_id = $2::uuid
                  AND (
                        status = 'pending'
                     OR (status = 'processing' AND (claimed_by IS NULL OR claimed_by = $3))
                      )
                """,
                official_number,
                session_id,
                self._worker_id,
                schema_name="public",
            )
            if recovered == "UPDATE 0":
                log.critical(
                    "escri.session_mark_blocked_active_claim session=%s num=%s "
                    "— otro worker tiene el claim activo; NO se fuerza signed, "
                    "requiere revisión manual si esa otra sesión no converge",
                    session_id[:8], official_number,
                )
                try:
                    await send_alert_mail(
                        subject=(
                            f"[GDI ESCRI] Sesión con claim en disputa — {official_number}"
                        ),
                        body=(
                            f"correlationId={session_id[:8]}\n"
                            f"num={official_number}\n\n"
                            f"La firma se completó de este lado, pero la sesión "
                            f"signing_sessions sigue 'processing' bajo el claim de "
                            f"otro worker (no se sobreescribió para no pisar su "
                            f"trabajo en curso). Si esa otra sesión no converge a "
                            f"'signed' por su cuenta, requiere revisión manual."
                        ),
                    )
                except Exception as _ae:
                    log.error("escri.session_mark_blocked_alert_err: %s", _ae)
            else:
                log.warning(
                    "escri.session_mark_recovered session=%s num=%s result=%s "
                    "— CAS estricto no matcheó tras firma completada",
                    session_id[:8], official_number, recovered,
                )

    async def _mark_session_failed(self, session_id: str, reason: str) -> None:
        row = await fetch_one(
            """
            UPDATE public.signing_sessions
            SET status         = 'failed',
                failure_reason = $1,
                updated_at     = NOW()
            WHERE session_id = $2::uuid
              AND status     = 'processing'
            RETURNING schema_name, document_id::text AS document_id,
                      user_id::text AS user_id
            """,
            reason[:500],
            session_id,
            schema_name="public",
        )
        if row is None:
            return

        await self._avisar_firma_fallida(
            session_id=session_id,
            schema_name=row["schema_name"],
            document_id=row["document_id"],
            user_id=row["user_id"],
            reason=reason,
        )

    async def _avisar_firma_fallida(
        self, *, session_id: str, schema_name: str, document_id: str,
        user_id: str | None, reason: str,
    ) -> None:
        from services.documents.signing.failure_notice import avisar_firma_fallida

        await avisar_firma_fallida(
            session_id=session_id,
            schema_name=schema_name,
            document_id=document_id,
            user_id=user_id,
            reason=reason,
        )

    async def _requeue_or_fail_post_upload(
        self, *, session_id: str, doc_id: str, exc: Exception,
    ) -> None:
        POST_UPLOAD_MAX_RETRIES = 3
        row = await fetch_one(
            """
            UPDATE public.signing_sessions
            SET payload    = COALESCE(payload, '{}'::jsonb)
                             || jsonb_build_object(
                                    'update_retry_count',
                                    COALESCE((payload->>'update_retry_count')::int, 0) + 1
                                ),
                updated_at = NOW()
            WHERE session_id = $1::uuid
            RETURNING (payload->>'update_retry_count')::int AS n
            """,
            session_id, schema_name="public",
        )
        n = int(row["n"]) if row and row["n"] is not None else POST_UPLOAD_MAX_RETRIES

        if n >= POST_UPLOAD_MAX_RETRIES:
            log.critical(
                "escri.common.post_upload_failed_final session=%s doc=%s "
                "attempts=%d err=%s — PDF firmado en tosign/ pero UPDATE "
                "document_signers.signed_at NUNCA corrió. Reconciliar a mano.",
                session_id[:8], doc_id[:8], n, str(exc)[:200],
            )
            await self._mark_session_failed(session_id, _failure_code(exc))
            return

        backoff_seconds = 30 * n
        await execute(
            """
            UPDATE public.signing_sessions
            SET status       = 'pending',
                claimed_by   = NULL,
                claimed_at   = NULL,
                expires_at   = NOW() + ($1 || ' minutes')::interval,
                available_at = NOW() + ($3 || ' seconds')::interval,
                updated_at   = NOW()
            WHERE session_id = $2::uuid
              AND status     = 'processing'
            """,
            str(PENDING_TTL_MINUTES),
            session_id,
            backoff_seconds,
            schema_name="public",
        )
        log.warning(
            "escri.common.post_upload_requeued session=%s doc=%s attempt=%d "
            "backoff=%ds err=%s",
            session_id[:8], doc_id[:8], n, backoff_seconds, str(exc)[:200],
        )

    async def _requeue_session_pending(
        self, session_id: str, retry_after: int = 0
    ) -> None:
        await execute(
            """
            UPDATE public.signing_sessions
            SET status       = 'pending',
                claimed_by   = NULL,
                claimed_at   = NULL,
                expires_at   = NOW() + ($1 || ' minutes')::interval,
                available_at = CASE WHEN $3 > 0
                                   THEN NOW() + ($3 || ' seconds')::interval
                                   ELSE NULL
                               END,
                updated_at   = NOW()
            WHERE session_id = $2::uuid
              AND status = 'processing'
            """,
            str(PENDING_TTL_MINUTES),
            session_id,
            retry_after,
            schema_name="public",
        )

    async def _requeue_guard_unverifiable(
        self,
        *,
        session_id: str,
        doc_id: str,
        payload: dict,
        origen: str,
        retry_after: int = 30,
    ) -> None:
        attempts = int(payload.get("guard_check_attempts", 0)) + 1
        if attempts >= ESCRI_GUARD_MAX_ATTEMPTS:
            reason = (
                f"guard_unverifiable_{origen}: no se pudo leer reservation_status "
                f"tras {attempts} intentos — el job se detiene para no re-firmar "
                f"sin poder verificar (GDI-276 fail-closed + techo 18/08)"
            )
            log.error(
                "escri.guard_requeue_exhausted session=%s doc=%s origen=%s "
                "attempts=%d/%d — sesión a 'failed'. El número NO se cancela: "
                "la reserva sigue vigente y requiere revisión manual.",
                session_id[:8], doc_id[:8], origen, attempts,
                ESCRI_GUARD_MAX_ATTEMPTS,
            )
            await self._mark_session_failed(session_id, reason)
            return

        new_payload = {**payload, "guard_check_attempts": attempts}
        await execute(
            """
            UPDATE public.signing_sessions
            SET status       = 'pending',
                claimed_by   = NULL,
                claimed_at   = NULL,
                expires_at   = NOW() + ($1 || ' minutes')::interval,
                available_at = NOW() + ($2 || ' seconds')::interval,
                payload      = $3::jsonb,
                updated_at   = NOW()
            WHERE session_id = $4::uuid
              AND status = 'processing'
            """,
            str(PENDING_TTL_MINUTES),
            str(retry_after),
            new_payload,
            session_id,
            schema_name="public",
        )
        log.warning(
            "escri.guard_requeued session=%s doc=%s origen=%s attempts=%d/%d "
            "retry_after=%ds",
            session_id[:8], doc_id[:8], origen, attempts,
            ESCRI_GUARD_MAX_ATTEMPTS, retry_after,
        )

    async def _requeue_sign_tsa_pending(
        self,
        *,
        session_id: str,
        payload: dict,
        tsa_attempts: int,
        retry_after_seconds: int,
    ) -> None:
        new_payload = {**payload, "tsa_attempts": tsa_attempts}
        _expires_minutes = max(
            PENDING_TTL_MINUTES,
            -(-retry_after_seconds // 60) + 10,
        )
        await execute(
            """
            UPDATE public.signing_sessions
            SET status       = 'pending',
                claimed_by   = NULL,
                claimed_at   = NULL,
                expires_at   = NOW() + ($1 || ' minutes')::interval,
                available_at = NOW() + ($2 || ' seconds')::interval,
                payload      = $3::jsonb,
                updated_at   = NOW()
            WHERE session_id = $4::uuid
              AND status = 'processing'
            """,
            str(_expires_minutes),
            str(retry_after_seconds),
            new_payload,
            session_id,
            schema_name="public",
        )
        _step_idx = min(tsa_attempts, len(SIGN_TSA_BACKOFF_MINUTES) - 1)
        _remaining_ladder_min = sum(SIGN_TSA_BACKOFF_MINUTES[_step_idx:])
        log.info(
            "escri.sign.tsa_requeued session=%s tsa_attempts=%d/%d "
            "retry_after=%ds expires_in=%dmin remaining_ladder=%dmin",
            session_id[:8], tsa_attempts, SIGN_TSA_MAX_ATTEMPTS,
            retry_after_seconds, _expires_minutes, _remaining_ladder_min,
        )
