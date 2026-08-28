from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

TEST_SCHEMA = "100_test"


def _key_row(**overrides):
    row = {
        "id": "apikey-1",
        "api_key_prefix": "sk_test",
        "municipality_id": "muni-1",
        "key_name": "Test Key",
        "expires_at": None,
        "rate_limit_per_minute": None,
        "key_active": True,
        "schema_name": TEST_SCHEMA,
        "municipality_name": "Test Muni",
        "muni_active": True,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _clear_caches():
    import api_gateway.auth_rest as rest_mod
    rest_mod._api_key_row_cache.clear()
    rest_mod._api_key_user_cache.clear()
    yield
    rest_mod._api_key_row_cache.clear()
    rest_mod._api_key_user_cache.clear()


async def _validate(api_key="sk-abc", user_id="user-1"):
    from api_gateway.auth_rest import validate_rest_api_key
    return await validate_rest_api_key(api_key, user_id)


class TestCacheHitAvoidsQueries:
    @pytest.mark.asyncio
    async def test_segundo_llamado_no_ejecuta_fetch_one(self):
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check") as mock_check, \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [_key_row(rate_limit_per_minute=100), {"user_id": "user-1"}]
            ctx1 = await _validate()
            assert ctx1.schema_name == TEST_SCHEMA
            assert mock_fetch.call_count == 2

            mock_fetch.side_effect = []
            ctx2 = await _validate()
            assert ctx2.schema_name == TEST_SCHEMA
            assert mock_fetch.call_count == 2

            assert mock_check.call_count == 2

    @pytest.mark.asyncio
    async def test_misma_key_distinto_usuario_reconsulta_solo_api_key_users(self):
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check"), \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [_key_row(), {"user_id": "user-1"}]
            await _validate(user_id="user-1")
            assert mock_fetch.call_count == 2

            mock_fetch.side_effect = [{"user_id": "user-2"}]
            await _validate(user_id="user-2")
            assert mock_fetch.call_count == 3


class TestTTLExpiration:
    @pytest.mark.asyncio
    async def test_ttl_vencido_vuelve_a_consultar_bd(self):
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check"), \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch, \
             patch("api_gateway.auth_rest.API_KEY_CACHE_TTL_SECONDS", 60):
            mock_fetch.side_effect = [_key_row(), {"user_id": "user-1"}]
            await _validate()
            assert mock_fetch.call_count == 2

            import api_gateway.auth_rest as rest_mod
            import time as time_mod
            real_monotonic = time_mod.monotonic
            with patch.object(time_mod, "monotonic", return_value=real_monotonic() + 61):
                mock_fetch.side_effect = [_key_row(), {"user_id": "user-1"}]
                await _validate()
                assert mock_fetch.call_count == 4

    @pytest.mark.asyncio
    async def test_ttl_cero_deshabilita_cache_siempre_consulta(self):
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check"), \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch, \
             patch("api_gateway.auth_rest.API_KEY_CACHE_TTL_SECONDS", 0):
            mock_fetch.side_effect = [
                _key_row(), {"user_id": "user-1"},
                _key_row(), {"user_id": "user-1"},
            ]
            await _validate()
            await _validate()
            assert mock_fetch.call_count == 4


class TestRevocationStaleness:
    @pytest.mark.asyncio
    async def test_revocacion_queda_stale_hasta_limpiar_cache(self):
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check"), \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [_key_row(key_active=True), {"user_id": "user-1"}]
            ctx = await _validate()
            assert ctx.schema_name == TEST_SCHEMA

            mock_fetch.side_effect = []
            ctx_stale = await _validate()
            assert ctx_stale.schema_name == TEST_SCHEMA

            import api_gateway.auth_rest as rest_mod
            rest_mod._api_key_row_cache.clear()
            mock_fetch.side_effect = [_key_row(key_active=False)]
            with pytest.raises(ValueError, match="API Key inactiva"):
                await _validate()


class TestExpiresAtSiempreValidado:
    @pytest.mark.asyncio
    async def test_key_expirada_se_rechaza_aunque_este_cacheada(self):
        expired = datetime.now(timezone.utc) - timedelta(days=1)
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check"), \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:
            row = _key_row(expires_at=expired)
            import api_gateway.auth_rest as rest_mod
            import time as time_mod
            rest_mod._api_key_row_cache["dummyhash"] = (row, time_mod.monotonic())

            api_key_hash_target = __import__("hashlib").sha256(b"sk-abc").hexdigest()
            rest_mod._api_key_row_cache[api_key_hash_target] = (row, time_mod.monotonic())

            mock_fetch.side_effect = []
            with pytest.raises(ValueError, match="API Key expirada"):
                await _validate()


class TestNegativeResultsNotCached:
    @pytest.mark.asyncio
    async def test_key_no_encontrada_no_se_cachea(self):
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check"), \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [None]
            with pytest.raises(ValueError, match="API Key inválida"):
                await _validate()

            mock_fetch.side_effect = [None]
            with pytest.raises(ValueError, match="API Key inválida"):
                await _validate()
            assert mock_fetch.call_count == 2

    @pytest.mark.asyncio
    async def test_usuario_no_autorizado_no_se_cachea(self):
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check"), \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [_key_row(), None]
            with pytest.raises(ValueError, match="no autorizado"):
                await _validate()

            mock_fetch.side_effect = [None]
            with pytest.raises(ValueError, match="no autorizado"):
                await _validate()
            assert mock_fetch.call_count == 3


class TestRateLimitSiempreEjecuta:
    @pytest.mark.asyncio
    async def test_rate_limiter_se_llama_en_cada_hit(self):
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check") as mock_check, \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [_key_row(rate_limit_per_minute=100), {"user_id": "user-1"}]
            await _validate()
            await _validate()
            await _validate()
            assert mock_check.call_count == 3
