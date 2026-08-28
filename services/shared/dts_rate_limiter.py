
import time
import threading

from shared.logging import get_logger
from config.constants import (
    DTS_MAX_PER_MINUTE,
    DTS_DEGRADED_DIVISOR,
    SPECIAL_TSA_RESERVED_PCT,
)

log = get_logger(__name__)

_KEY_PREFIX = "escri_dts_rl:"
_REDIS_KEY_TTL_SECONDS = 120

_fallback_lock = threading.Lock()
_fallback_window: dict = {"minute": None, "count": 0}

_INCR_CHECK_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
if count > tonumber(ARGV[1]) then
    redis.call('DECR', KEYS[1])
    return 0
end
return count
"""


def _get_redis():
    try:
        from services.cache import get_redis
        return get_redis()
    except Exception:
        return None


def _current_minute() -> int:
    return int(time.time() // 60)


def _effective_cap(base_max: int, priority: bool) -> int:
    if priority:
        return base_max
    return int(base_max * (1 - SPECIAL_TSA_RESERVED_PCT))


def _local_try_consume(priority: bool = False) -> bool:
    minute = _current_minute()
    degraded_cap = max(1, DTS_MAX_PER_MINUTE // DTS_DEGRADED_DIVISOR)
    effective_cap = _effective_cap(degraded_cap, priority)
    with _fallback_lock:
        if _fallback_window["minute"] != minute:
            _fallback_window["minute"] = minute
            _fallback_window["count"] = 0
        if _fallback_window["count"] >= effective_cap:
            return False
        _fallback_window["count"] += 1
        return True


def try_consume_dts_slot(priority: bool = False) -> bool:
    r = _get_redis()
    if r is None:
        return _local_try_consume(priority=priority)

    key = f"{_KEY_PREFIX}{_current_minute()}"
    cap = _effective_cap(DTS_MAX_PER_MINUTE, priority)
    try:
        result = r.eval(_INCR_CHECK_LUA, 1, key, str(cap), str(_REDIS_KEY_TTL_SECONDS))
        return int(result) > 0
    except Exception as e:
        log.warning(f"dts_rate_limiter.redis_error (fail-open local): {e}")
        return _local_try_consume(priority=priority)


def seconds_until_next_window() -> int:
    return max(1, 60 - int(time.time() % 60))


def current_minute_count() -> int:
    r = _get_redis()
    minute = _current_minute()
    if r is None:
        with _fallback_lock:
            return _fallback_window["count"] if _fallback_window["minute"] == minute else 0
    try:
        raw = r.get(f"{_KEY_PREFIX}{minute}")
        return int(raw) if raw else 0
    except Exception:
        return 0
