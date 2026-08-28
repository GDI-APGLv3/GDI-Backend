
import time
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


import services.shared.notary_breaker as _cb_module

def _reset_fallback():
    with _cb_module._fallback_lock:
        _cb_module._fallback["state"]            = "CLOSED"
        _cb_module._fallback["failure_count"]    = 0
        _cb_module._fallback["first_failure_at"] = None
        _cb_module._fallback["opened_at"]        = None


def _make_redis_none_module():
    return patch.object(_cb_module, "_get_redis", return_value=None)


def _make_redis_mock():
    r = MagicMock()
    r.pipeline.return_value.__enter__ = MagicMock(return_value=r.pipeline.return_value)
    r.pipeline.return_value.__exit__  = MagicMock(return_value=False)
    r.pipeline.return_value.execute   = MagicMock(return_value=[])
    r.pipeline.return_value.set       = MagicMock()
    r.pipeline.return_value.delete    = MagicMock()

    r.get.return_value = None
    r.incr.return_value = 1
    r.ttl.return_value  = -1
    r.expire.return_value = True
    r.set.return_value  = True
    r.delete.return_value = 1

    return r


class TestBreakerOpening:

    def setup_method(self):
        _reset_fallback()

    @pytest.mark.asyncio
    async def test_5_unavailable_errors_open_breaker(self):
        from shared.exceptions import NotaryUnavailableError
        from services.shared.notary_breaker import record_notary_failure

        r = _make_redis_mock()
        call_count = 0

        def fake_incr(key):
            nonlocal call_count
            call_count += 1
            return call_count

        r.incr.side_effect  = fake_incr
        r.ttl.return_value  = 25

        with patch.object(_cb_module, "_get_redis", return_value=r):
            for i in range(4):
                await record_notary_failure(NotaryUnavailableError(f"fallo {i}"))
                state_val = r.get.return_value

            await record_notary_failure(NotaryUnavailableError("quinto fallo"))

        with _cb_module._fallback_lock:
            assert _cb_module._fallback["state"] == "OPEN"

    @pytest.mark.asyncio
    async def test_business_error_does_not_feed_breaker(self):
        from shared.exceptions import NotaryBusinessError
        from services.shared.notary_breaker import record_notary_failure

        r = _make_redis_mock()
        with patch.object(_cb_module, "_get_redis", return_value=r):
            for _ in range(10):
                await record_notary_failure(NotaryBusinessError("FULLPAGE"))

        r.incr.assert_not_called()
        with _cb_module._fallback_lock:
            assert _cb_module._fallback["state"] == "CLOSED"


class TestBreakerOpen:

    def setup_method(self):
        _reset_fallback()

    @pytest.mark.asyncio
    async def test_breaker_open_raises_without_notary_call(self):
        from shared.exceptions import NotaryBreakerOpenError
        from services.shared.notary_breaker import check_breaker_before_call

        r = _make_redis_mock()
        now_str = str(time.time())
        r.get.side_effect = lambda key: (
            "OPEN"    if key == _cb_module._KEY_STATE     else
            now_str   if key == _cb_module._KEY_OPENED_AT else
            None
        )

        with patch.object(_cb_module, "_get_redis", return_value=r):
            with patch.object(_cb_module, "_probe_notary_health") as mock_probe:
                with pytest.raises(NotaryBreakerOpenError) as exc_info:
                    await check_breaker_before_call()

                mock_probe.assert_not_called()

        assert exc_info.value.retry_after > 0

    @pytest.mark.asyncio
    async def test_breaker_open_includes_retry_after(self):
        from shared.exceptions import NotaryBreakerOpenError
        from services.shared.notary_breaker import check_breaker_before_call

        r = _make_redis_mock()
        opened_10s_ago = str(time.time() - 10)
        r.get.side_effect = lambda key: (
            "OPEN"         if key == _cb_module._KEY_STATE     else
            opened_10s_ago if key == _cb_module._KEY_OPENED_AT else
            None
        )

        with patch.object(_cb_module, "_get_redis", return_value=r):
            with pytest.raises(NotaryBreakerOpenError) as exc_info:
                await check_breaker_before_call()

        assert 15 <= exc_info.value.retry_after <= 25


class TestHalfOpen:

    def setup_method(self):
        _reset_fallback()

    @pytest.mark.asyncio
    async def test_half_open_health_ok_closes_breaker(self):
        from services.shared.notary_breaker import check_breaker_before_call

        r = _make_redis_mock()
        opened_31s_ago = str(time.time() - 31)
        r.get.side_effect = lambda key: (
            "OPEN"          if key == _cb_module._KEY_STATE     else
            opened_31s_ago  if key == _cb_module._KEY_OPENED_AT else
            None
        )
        r.set.return_value = True

        with patch.object(_cb_module, "_get_redis", return_value=r):
            with patch.object(
                _cb_module, "_probe_notary_health", new_callable=AsyncMock, return_value=True
            ):
                await check_breaker_before_call()

        with _cb_module._fallback_lock:
            assert _cb_module._fallback["state"] == "CLOSED"

    @pytest.mark.asyncio
    async def test_half_open_health_fail_reopens_breaker(self):
        from shared.exceptions import NotaryBreakerOpenError
        from services.shared.notary_breaker import check_breaker_before_call

        r = _make_redis_mock()
        opened_31s_ago = str(time.time() - 31)
        r.get.side_effect = lambda key: (
            "OPEN"          if key == _cb_module._KEY_STATE     else
            opened_31s_ago  if key == _cb_module._KEY_OPENED_AT else
            None
        )
        r.set.return_value = True

        with patch.object(_cb_module, "_get_redis", return_value=r):
            with patch.object(
                _cb_module, "_probe_notary_health", new_callable=AsyncMock, return_value=False
            ):
                with pytest.raises(NotaryBreakerOpenError):
                    await check_breaker_before_call()

        with _cb_module._fallback_lock:
            assert _cb_module._fallback["state"] == "OPEN"


class TestRedisFailOpen:

    def setup_method(self):
        _reset_fallback()

    @pytest.mark.asyncio
    async def test_redis_down_fail_open_local_state(self):
        from shared.exceptions import NotaryUnavailableError
        from services.shared.notary_breaker import record_notary_failure, check_breaker_before_call
        from config.constants import CB_FAILURE_THRESHOLD

        with _make_redis_none_module():
            await check_breaker_before_call()

            for i in range(CB_FAILURE_THRESHOLD - 1):
                await record_notary_failure(NotaryUnavailableError(f"fallo {i}"))

            with _cb_module._fallback_lock:
                assert _cb_module._fallback["state"] == "CLOSED"
                assert _cb_module._fallback["failure_count"] == CB_FAILURE_THRESHOLD - 1

            await record_notary_failure(NotaryUnavailableError("último fallo"))

        with _cb_module._fallback_lock:
            assert _cb_module._fallback["state"] == "OPEN"

    @pytest.mark.asyncio
    async def test_redis_down_success_resets_local(self):
        from services.shared.notary_breaker import record_notary_success

        with _cb_module._fallback_lock:
            _cb_module._fallback["state"] = "HALF_OPEN"

        with _make_redis_none_module():
            await record_notary_success()

        with _cb_module._fallback_lock:
            assert _cb_module._fallback["state"] == "CLOSED"
