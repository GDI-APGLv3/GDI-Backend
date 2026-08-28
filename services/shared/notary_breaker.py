
import os
import time
import threading
from typing import Optional

import httpx

from shared.logging import get_logger
from config.constants import CB_FAILURE_THRESHOLD, CB_WINDOW_SECONDS, CB_COOLDOWN_SECONDS

log = get_logger(__name__)


_KEY_STATE       = "notary_cb:state"
_KEY_OPENED_AT   = "notary_cb:opened_at"
_KEY_FAILURES    = "notary_cb:failures"
_KEY_HALF_LOCK   = "notary_cb:half_open_lock"

_STATE_CLOSED    = "CLOSED"
_STATE_OPEN      = "OPEN"
_STATE_HALF_OPEN = "HALF_OPEN"


_fallback_lock = threading.Lock()
_fallback: dict = {
    "state":            _STATE_CLOSED,
    "failure_count":    0,
    "first_failure_at": None,
    "opened_at":        None,
}


def _get_redis():
    try:
        from services.cache import redis_client
        return redis_client
    except Exception:
        return None


def _redis_get_state() -> tuple[str, Optional[float]]:
    r = _get_redis()
    if r is None:
        with _fallback_lock:
            return _fallback["state"], _fallback["opened_at"]
    try:
        state = r.get(_KEY_STATE) or _STATE_CLOSED
        opened_at_raw = r.get(_KEY_OPENED_AT)
        opened_at = float(opened_at_raw) if opened_at_raw else None
        return state, opened_at
    except Exception as e:
        log.warning(f"notary_cb._redis_get_state error (fail-open): {e}")
        with _fallback_lock:
            return _fallback["state"], _fallback["opened_at"]


def _redis_open_breaker() -> None:
    now = time.time()
    r = _get_redis()
    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.set(_KEY_STATE, _STATE_OPEN)
            pipe.set(_KEY_OPENED_AT, str(now))
            pipe.delete(_KEY_HALF_LOCK)
            pipe.execute()
        except Exception as e:
            log.warning(f"notary_cb._redis_open_breaker error: {e}")

    with _fallback_lock:
        _fallback["state"]            = _STATE_OPEN
        _fallback["opened_at"]        = now
        _fallback["failure_count"]    = 0
        _fallback["first_failure_at"] = None

    log.warning(
        "notary_cb.open",
        extra={
            "threshold": CB_FAILURE_THRESHOLD,
            "window_s":  CB_WINDOW_SECONDS,
        },
    )


def _redis_close_breaker() -> None:
    r = _get_redis()
    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.set(_KEY_STATE, _STATE_CLOSED)
            pipe.delete(_KEY_OPENED_AT)
            pipe.delete(_KEY_FAILURES)
            pipe.delete(_KEY_HALF_LOCK)
            pipe.execute()
        except Exception as e:
            log.warning(f"notary_cb._redis_close_breaker error: {e}")

    with _fallback_lock:
        _fallback["state"]            = _STATE_CLOSED
        _fallback["failure_count"]    = 0
        _fallback["first_failure_at"] = None
        _fallback["opened_at"]        = None

    log.info("notary_cb.close")


def _redis_set_half_open() -> None:
    r = _get_redis()
    if r is not None:
        try:
            r.set(_KEY_STATE, _STATE_HALF_OPEN)
        except Exception as e:
            log.warning(f"notary_cb._redis_set_half_open error: {e}")

    with _fallback_lock:
        _fallback["state"] = _STATE_HALF_OPEN

    log.info("notary_cb.half_open")


def _try_acquire_half_open_lock() -> bool:
    r = _get_redis()
    if r is None:
        return True
    try:
        acquired = r.set(_KEY_HALF_LOCK, "1", nx=True, ex=10)
        return bool(acquired)
    except Exception as e:
        log.warning(f"notary_cb._try_acquire_half_open_lock error (fail-open): {e}")
        return True


def _local_record_failure() -> int:
    now = time.time()
    with _fallback_lock:
        first = _fallback["first_failure_at"]
        if first is None or now - first > CB_WINDOW_SECONDS:
            _fallback["failure_count"]    = 1
            _fallback["first_failure_at"] = now
        else:
            _fallback["failure_count"] += 1
        return _fallback["failure_count"]


def _redis_record_failure() -> int:
    r = _get_redis()
    new_count: int = 0

    if r is not None:
        try:
            new_count = r.incr(_KEY_FAILURES)
            if new_count == 1:
                r.expire(_KEY_FAILURES, CB_WINDOW_SECONDS)
            elif r.ttl(_KEY_FAILURES) == -1:
                r.expire(_KEY_FAILURES, CB_WINDOW_SECONDS)
        except Exception as e:
            log.warning(f"notary_cb._redis_record_failure error (fail-open local): {e}")
            new_count = _local_record_failure()
    else:
        new_count = _local_record_failure()

    if new_count >= CB_FAILURE_THRESHOLD:
        current_state, _ = _redis_get_state()
        if current_state == _STATE_CLOSED:
            _redis_open_breaker()

    return new_count


async def _probe_notary_health() -> bool:
    notary_url = os.getenv("NOTARY_URL", "")
    notary_key = os.getenv("NOTARY_API_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{notary_url}/health",
                headers={"x-api-key": notary_key},
            )
        ok = resp.status_code == 200

        extra: dict = {"http_status": resp.status_code, "ok": ok}
        try:
            body = resp.json()
            extra["notary_status"] = body.get("status")
            extra["tsa_breaker"]   = body.get("tsa_breaker")
            extra["can_sign_bb"]   = body.get("can_sign_bb")
        except Exception:
            pass

        log.info("notary_cb.probe_health", extra=extra)
        return ok
    except Exception as e:
        log.warning(f"notary_cb._probe_notary_health error: {e}")
        return False


def get_retry_after() -> int:
    _, opened_at = _redis_get_state()
    if opened_at is None:
        return CB_COOLDOWN_SECONDS
    elapsed   = time.time() - opened_at
    remaining = CB_COOLDOWN_SECONDS - elapsed
    return max(1, int(remaining))


async def check_breaker_before_call() -> None:
    from shared.exceptions import NotaryBreakerOpenError

    state, opened_at = _redis_get_state()

    if state == _STATE_CLOSED:
        return

    now     = time.time()
    elapsed = now - (opened_at or now)

    if state == _STATE_OPEN and elapsed < CB_COOLDOWN_SECONDS:
        raise NotaryBreakerOpenError(retry_after=get_retry_after())

    if not _try_acquire_half_open_lock():
        raise NotaryBreakerOpenError(retry_after=5)

    _redis_set_half_open()
    health_ok = await _probe_notary_health()

    if health_ok:
        _redis_close_breaker()
        return
    else:
        _redis_open_breaker()
        raise NotaryBreakerOpenError(retry_after=CB_COOLDOWN_SECONDS)


async def record_notary_failure(error: Exception) -> None:
    from shared.exceptions import NotaryUnavailableError

    if not isinstance(error, NotaryUnavailableError):
        return

    state, _ = _redis_get_state()

    if state in (_STATE_HALF_OPEN, _STATE_OPEN):
        log.warning("notary_cb.half_open_probe_failed")
        _redis_open_breaker()
        return

    _redis_record_failure()


async def record_notary_success() -> None:
    state, _ = _redis_get_state()
    if state == _STATE_HALF_OPEN:
        log.info("notary_cb.half_open_probe_success")
        _redis_close_breaker()


async def breaker_status() -> dict:
    state, opened_at = _redis_get_state()
    r = _get_redis()
    failure_count = 0

    if r is not None:
        try:
            raw = r.get(_KEY_FAILURES)
            failure_count = int(raw) if raw else 0
        except Exception:
            with _fallback_lock:
                failure_count = _fallback.get("failure_count", 0)
    else:
        with _fallback_lock:
            failure_count = _fallback.get("failure_count", 0)

    return {
        "state":         state,
        "opened_at":     opened_at,
        "failure_count": failure_count,
        "retry_after":   get_retry_after() if state != _STATE_CLOSED else 0,
    }
