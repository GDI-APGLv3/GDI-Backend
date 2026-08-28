from unittest.mock import MagicMock, patch

import services.cache as cache


def _reset(monkeypatch, *, client=None, last_attempt=0.0):
    monkeypatch.setattr(cache, "redis_client", client)
    monkeypatch.setattr(cache, "_last_init_attempt", last_attempt)


class TestGetRedisLazyRetry:
    def test_con_cliente_vivo_no_reintenta(self, monkeypatch):
        client = MagicMock()
        _reset(monkeypatch, client=client)
        with patch.object(cache, "init_redis") as init_mock:
            assert cache.get_redis() is client
        init_mock.assert_not_called()

    def test_sin_cliente_y_cooldown_vencido_reintenta(self, monkeypatch):
        _reset(monkeypatch, client=None, last_attempt=0.0)
        recovered = MagicMock()

        def fake_init():
            cache.redis_client = recovered
            return True

        with patch.object(cache, "init_redis", side_effect=fake_init) as init_mock:
            with patch.object(cache.time, "monotonic", return_value=10_000.0):
                assert cache.get_redis() is recovered
        init_mock.assert_called_once()

    def test_sin_cliente_dentro_del_cooldown_no_reintenta(self, monkeypatch):
        now = 10_000.0
        _reset(monkeypatch, client=None, last_attempt=now - 5)
        with patch.object(cache, "init_redis") as init_mock:
            with patch.object(cache.time, "monotonic", return_value=now):
                assert cache.get_redis() is None
        init_mock.assert_not_called()

    def test_reintento_fallido_sigue_devolviendo_none(self, monkeypatch):
        _reset(monkeypatch, client=None, last_attempt=0.0)

        def fake_init_fail():
            cache._last_init_attempt = 10_000.0
            cache.redis_client = None
            return False

        with patch.object(cache, "init_redis", side_effect=fake_init_fail):
            with patch.object(cache.time, "monotonic", return_value=10_000.0):
                assert cache.get_redis() is None

    def test_init_redis_registra_el_intento_aunque_falle(self, monkeypatch):
        _reset(monkeypatch, client=None, last_attempt=0.0)
        monkeypatch.setenv("REDIS_URL", "redis://localhost:1/0")
        with patch.object(cache.redis, "from_url", side_effect=Exception("boom")):
            with patch.object(cache.time, "monotonic", return_value=10_000.0):
                assert cache.init_redis() is False
        assert cache._last_init_attempt == 10_000.0
        assert cache.redis_client is None
