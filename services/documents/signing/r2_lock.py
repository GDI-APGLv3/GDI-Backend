import asyncio

from shared.logging import get_logger
from services.r2_client import r2_copy, r2_delete, r2_put, r2_head, R2KeyNotFound

log = get_logger(__name__)

_INPROCESS_PREFIX = "inprocess"
_REDIS_LOCK_TTL = 120


def _redis_lock_key(schema_name: str, doc_id: str) -> str:
    return f"signing:lock:{schema_name}:{doc_id.replace('-', '')}"


def _acquire_redis_lock(schema_name: str, doc_id: str) -> bool:
    try:
        from services.cache import redis_client
        if redis_client is None:
            return True
        acquired = redis_client.set(
            _redis_lock_key(schema_name, doc_id), "1", nx=True, ex=_REDIS_LOCK_TTL
        )
        return bool(acquired)
    except Exception as e:
        log.error(
            f"DEGRADADO: Redis no disponible, lock deshabilitado. "
            f"Riesgo de concurrencia en firma. error={e}"
        )
        return True


def _release_redis_lock(schema_name: str, doc_id: str) -> None:
    try:
        from services.cache import redis_client
        if redis_client is None:
            return
        redis_client.delete(_redis_lock_key(schema_name, doc_id))
    except Exception as e:
        log.warning(f"r2_lock.redis_release_error: {e}")


def _tosign_key(doc_id: str) -> str:
    return doc_id.replace("-", "") + ".pdf"


def _inprocess_key(doc_id: str) -> str:
    return f"{_INPROCESS_PREFIX}/{doc_id.replace('-', '')}.pdf"


async def acquire_signing_lock_R2(*, schema_name: str, doc_id: str) -> bool:
    src = _tosign_key(doc_id)
    dst = _inprocess_key(doc_id)

    if not _acquire_redis_lock(schema_name, doc_id):
        log.warning(
            "r2_lock.redis_already_locked",
            extra={"schema_name": schema_name, "doc_id": doc_id},
        )
        return False

    try:
        await r2_head(schema_name=schema_name, key=dst)
        log.warning(
            "r2_lock.r2_already_locked",
            extra={"schema_name": schema_name, "doc_id": doc_id, "dst": dst},
        )
        _release_redis_lock(schema_name, doc_id)
        return False
    except R2KeyNotFound:
        pass

    try:
        await r2_copy(schema_name=schema_name, src=src, dst=dst)
        await r2_delete(schema_name=schema_name, key=src)
        log.info(
            "r2_lock.acquired",
            extra={"schema_name": schema_name, "doc_id": doc_id, "src": src, "dst": dst},
        )
        return True
    except R2KeyNotFound:
        log.warning(
            "r2_lock.tosign_not_found",
            extra={"schema_name": schema_name, "doc_id": doc_id, "src": src},
        )
        _release_redis_lock(schema_name, doc_id)
        return False


async def release_signing_lock_R2_success(
    *,
    schema_name: str,
    doc_id: str,
    signed_pdf: bytes,
    is_numerator: bool,
    number: str | None,
    session_id: str | None = None,
) -> None:

    inprocess = _inprocess_key(doc_id)

    if is_numerator and number:
        log.info(
            "r2_lock.release_success.numerator",
            extra={
                "schema_name": schema_name,
                "doc_id": doc_id,
                "official_number": number,
            },
        )
    else:
        dest_key = _tosign_key(doc_id)
        await r2_put(schema_name=schema_name, key=dest_key, body=signed_pdf, bucket="tosign")
        log.info(
            "r2_lock.release_success.common",
            extra={"schema_name": schema_name, "doc_id": doc_id, "dest_key": dest_key},
        )
        if session_id:
            try:
                from database import execute
                await execute(
                    """
                    UPDATE public.signing_sessions
                    SET payload = COALESCE(payload, '{}'::jsonb)
                                  || jsonb_build_object('signed_uploaded', true)
                    WHERE session_id = $1::uuid
                    """,
                    session_id, schema_name="public",
                )
            except Exception as e:
                log.warning(
                    "r2_lock.release_success.marker_write_failed session=%s doc=%s error=%s",
                    session_id[:8], doc_id, e,
                )

    _release_redis_lock(schema_name, doc_id)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            await r2_delete(schema_name=schema_name, key=inprocess)
            log.info(
                "r2_lock.inprocess_cleaned",
                extra={"schema_name": schema_name, "doc_id": doc_id, "attempt": attempt + 1},
            )
            last_err = None
            break
        except R2KeyNotFound:
            log.warning(
                "r2_lock.release_success.inprocess_already_gone",
                extra={"schema_name": schema_name, "doc_id": doc_id},
            )
            last_err = None
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(0.2 * (2 ** attempt))
    if last_err is not None:
        log.critical(
            "r2_lock.release_success.inprocess_delete_failed_final "
            "schema=%s doc=%s key=%s error=%s — BORRAR A MANO o el "
            "siguiente firmante queda bloqueado (409 document_already_signing)",
            schema_name, doc_id, inprocess, last_err,
        )


async def release_signing_lock_R2_fail(*, schema_name: str, doc_id: str) -> None:
    src = _inprocess_key(doc_id)
    dst = _tosign_key(doc_id)

    _release_redis_lock(schema_name, doc_id)

    try:
        await r2_copy(schema_name=schema_name, src=src, dst=dst)
        await r2_delete(schema_name=schema_name, key=src)
        log.info(
            "r2_lock.rollback_ok",
            extra={"schema_name": schema_name, "doc_id": doc_id},
        )
    except R2KeyNotFound:
        log.warning(
            "r2_lock.rollback.inprocess_not_found",
            extra={"schema_name": schema_name, "doc_id": doc_id, "src": src},
        )


async def reclaim_orphan_inprocess(*, schema_name: str, doc_id: str, session_id: str) -> None:
    log.info(
        "orphan_reclaim.start",
        extra={"schema_name": schema_name, "doc_id": doc_id, "session_id": session_id},
    )
    await release_signing_lock_R2_fail(schema_name=schema_name, doc_id=doc_id)
    log.info(
        "orphan_reclaim.done",
        extra={"schema_name": schema_name, "doc_id": doc_id, "session_id": session_id},
    )


async def delete_inprocess_on_reject(*, schema_name: str, doc_id: str) -> None:
    _release_redis_lock(schema_name, doc_id)

    key = _inprocess_key(doc_id)
    try:
        await r2_delete(schema_name=schema_name, key=key)
        log.info(
            "r2_lock.reject_cleanup_inprocess",
            extra={"schema_name": schema_name, "doc_id": doc_id, "key": key},
        )
    except R2KeyNotFound:
        log.info(
            "r2_lock.reject_cleanup_inprocess.not_found",
            extra={"schema_name": schema_name, "doc_id": doc_id},
        )
    except Exception as e:
        log.warning(
            "r2_lock.reject_cleanup_inprocess.error",
            extra={"schema_name": schema_name, "doc_id": doc_id, "error": str(e)},
        )
