
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


SCHEMA = "100_test"


def _request(ip="1.2.3.4", body=b"op=get&id=DATA1"):
    req = MagicMock()
    req.headers = {"fly-client-ip": ip}
    req.client.host = ip

    async def _body():
        return body

    req.body = _body
    return req


class _RedisFalso:

    def __init__(self):
        self.contadores = {}
        self.expires = {}
        self.scans = 0

    def incr(self, key):
        self.contadores[key] = self.contadores.get(key, 0) + 1
        return self.contadores[key]

    def expire(self, key, ttl):
        self.expires[key] = ttl

    def scan(self, cursor, match=None, count=100):
        self.scans += 1
        return 0, []

    def get(self, key):
        return None

    def setex(self, key, ttl, val):
        return True


@pytest.fixture
def redis_falso():
    return _RedisFalso()


class TestElFrenoGeneral:

    @pytest.mark.asyncio
    async def test_deja_pasar_dentro_del_limite(self, redis_falso):
        from endpoints.digital_signature import storage as st

        with patch.object(st, "redis_client", redis_falso), \
             patch("shared.ip_rate_limit.get_redis", return_value=redis_falso), \
             patch.object(st, "_resolve_schema_for_id", return_value=SCHEMA):
            resp = await st.storage_handler(_request())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_corta_al_pasarse(self, redis_falso):
        from endpoints.digital_signature import storage as st

        with patch.object(st, "redis_client", redis_falso), \
             patch("shared.ip_rate_limit.get_redis", return_value=redis_falso), \
             patch.object(st, "DIGITAL_SIGNATURE_STORAGE_MAX_PER_MINUTE", 3), \
             patch.object(st, "_resolve_schema_for_id", return_value=SCHEMA):
            for _ in range(3):
                await st.storage_handler(_request())
            with pytest.raises(HTTPException) as exc:
                await st.storage_handler(_request())
        assert exc.value.status_code == 429
        assert exc.value.headers["Retry-After"] == "60"

    @pytest.mark.asyncio
    async def test_el_limite_es_por_ip_y_no_global(self, redis_falso):
        from endpoints.digital_signature import storage as st

        with patch.object(st, "redis_client", redis_falso), \
             patch("shared.ip_rate_limit.get_redis", return_value=redis_falso), \
             patch.object(st, "DIGITAL_SIGNATURE_STORAGE_MAX_PER_MINUTE", 2), \
             patch.object(st, "_resolve_schema_for_id", return_value=SCHEMA):
            for _ in range(2):
                await st.storage_handler(_request(ip="9.9.9.9"))
            with pytest.raises(HTTPException):
                await st.storage_handler(_request(ip="9.9.9.9"))
            resp = await st.storage_handler(_request(ip="5.5.5.5"))
        assert resp.status_code == 200


class TestElFrenoDeIdsDesconocidos:

    @pytest.mark.asyncio
    async def test_los_misses_cortan_antes_que_el_limite_general(self, redis_falso):
        from endpoints.digital_signature import storage as st

        with patch.object(st, "redis_client", redis_falso), \
             patch("shared.ip_rate_limit.get_redis", return_value=redis_falso), \
             patch.object(st, "DIGITAL_SIGNATURE_STORAGE_MAX_PER_MINUTE", 1000), \
             patch.object(st, "DIGITAL_SIGNATURE_STORAGE_MAX_MISSES_PER_MINUTE", 2), \
             patch.object(st, "_resolve_schema_for_id", return_value=None):
            codigos = []
            for _ in range(4):
                with pytest.raises(HTTPException) as exc:
                    await st.storage_handler(_request())
                codigos.append(exc.value.status_code)

        assert codigos[:2] == [404, 404]
        assert codigos[2:] == [429, 429], (
            "tantear identificadores sigue siendo gratis: el freno de misses no corta"
        )

    @pytest.mark.asyncio
    async def test_un_miss_no_gasta_el_cupo_de_quien_firma_bien(self, redis_falso):
        from endpoints.digital_signature import storage as st

        with patch.object(st, "redis_client", redis_falso), \
             patch("shared.ip_rate_limit.get_redis", return_value=redis_falso), \
             patch.object(st, "DIGITAL_SIGNATURE_STORAGE_MAX_MISSES_PER_MINUTE", 1), \
             patch.object(st, "_resolve_schema_for_id", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await st.storage_handler(_request())
            assert exc.value.status_code == 404

        with patch.object(st, "redis_client", redis_falso), \
             patch("shared.ip_rate_limit.get_redis", return_value=redis_falso), \
             patch.object(st, "_resolve_schema_for_id", return_value=SCHEMA):
            resp = await st.storage_handler(_request())
        assert resp.status_code == 200


class TestElFrenoVaAntesDelScan:

    @pytest.mark.asyncio
    async def test_una_request_frenada_no_toca_redis_para_buscar(self, redis_falso):
        from endpoints.digital_signature import storage as st

        resolve_spy = MagicMock(return_value=SCHEMA)
        with patch.object(st, "redis_client", redis_falso), \
             patch("shared.ip_rate_limit.get_redis", return_value=redis_falso), \
             patch.object(st, "DIGITAL_SIGNATURE_STORAGE_MAX_PER_MINUTE", 1), \
             patch.object(st, "_resolve_schema_for_id", resolve_spy):
            await st.storage_handler(_request())
            with pytest.raises(HTTPException):
                await st.storage_handler(_request())

        assert resolve_spy.call_count == 1, (
            "la request frenada igual disparó la búsqueda en Redis"
        )


class TestSinRedisNoSeDejaPasar:

    @pytest.mark.asyncio
    async def test_fail_closed(self, redis_falso):
        from endpoints.digital_signature import storage as st

        with patch.object(st, "redis_client", None), \
             patch("shared.ip_rate_limit.get_redis", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await st.storage_handler(_request())
        assert exc.value.status_code == 429


class TestLaIpNoSePuedeFalsear:

    def test_ignora_x_forwarded_for(self):
        from shared.ip_rate_limit import get_client_ip

        req = MagicMock()
        req.headers = {"x-forwarded-for": "6.6.6.6", "fly-client-ip": "1.2.3.4"}
        req.client.host = "10.0.0.1"
        assert get_client_ip(req) == "1.2.3.4"

    def test_sin_header_de_fly_usa_el_socket(self):
        from shared.ip_rate_limit import get_client_ip

        req = MagicMock()
        req.headers = {"x-forwarded-for": "6.6.6.6"}
        req.client.host = "10.0.0.1"
        assert get_client_ip(req) == "10.0.0.1"


class TestElCruceDeMunicipiosYaEstabaTapado:

    @pytest.mark.asyncio
    async def test_un_id_sin_schema_no_ejecuta_la_operacion(self, redis_falso):
        from endpoints.digital_signature import storage as st

        with patch.object(st, "redis_client", redis_falso), \
             patch("shared.ip_rate_limit.get_redis", return_value=redis_falso), \
             patch.object(st, "_resolve_schema_for_id", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await st.storage_handler(_request(body=b"op=put&id=DATA1&dat=xx"))
        assert exc.value.status_code == 404
        assert redis_falso.contadores.get(f"{st.REDIS_KEY_PREFIX}:{SCHEMA}:DATA1") is None

    @pytest.mark.asyncio
    async def test_la_key_se_arma_con_el_schema_resuelto(self, redis_falso):
        from endpoints.digital_signature import storage as st

        setex_spy = MagicMock()
        redis_falso.setex = setex_spy
        with patch.object(st, "redis_client", redis_falso), \
             patch("shared.ip_rate_limit.get_redis", return_value=redis_falso), \
             patch.object(st, "_resolve_schema_for_id", return_value="200_otro"):
            await st.storage_handler(_request(body=b"op=put&id=DATA1&dat=xx"))

        key_usada = setex_spy.call_args[0][0]
        assert key_usada == "firma:storage:200_otro:DATA1"
