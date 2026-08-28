import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TEST_SCHEMA = "100_test"
TEST_CITIZEN_ID = "c1000000-0000-0000-0000-000000000001"


class TestTadRoutesRegistered:
    def _get_routes(self):
        from api_gateway.http_server import routes as gateway_routes
        result = {}
        for route in gateway_routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                for method in (route.methods or []):
                    result[(route.path, method)] = getattr(route, "endpoint", None)
        return result

    def test_create_citizen_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/tad/citizens", "POST") in routes

    def test_get_citizen_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/tad/citizens/{id_or_cuil}", "GET") in routes

    def test_patch_citizen_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/tad/citizens/{id}", "PATCH") in routes

    def test_document_types_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/tad/document-types", "GET") in routes

    def test_private_and_public_routes_unaffected(self):
        routes = self._get_routes()
        assert ("/api/v1/documents/search", "GET") in routes
        assert ("/api/v1/public/{muni}/search", "GET") in routes

    def test_handlers_exported_and_async(self):
        from api_gateway.rest_api_tad import (
            api_tad_create_citizen,
            api_tad_get_citizen,
            api_tad_patch_citizen,
            api_tad_get_document_types,
        )
        for fn in (
            api_tad_create_citizen,
            api_tad_get_citizen,
            api_tad_patch_citizen,
            api_tad_get_document_types,
        ):
            assert callable(fn)
            assert inspect.iscoroutinefunction(fn)


def _tad_key_row(**overrides):
    row = {
        "id": "tadkey-1",
        "key_active": True,
        "expires_at": None,
        "rate_limit_per_minute": None,
        "key_type": "tad",
        "schema_name": TEST_SCHEMA,
        "muni_active": True,
    }
    row.update(overrides)
    return row


class TestValidateTadApiKey:
    async def _run(self, api_key, citizen_ref=None, row=None, citizen_row=None, strict_rate_limit=None):
        fetch_results = [row]
        if citizen_ref is not None:
            fetch_results.append(citizen_row)

        async def _fetch_one_side_effect(*args, **kwargs):
            return fetch_results.pop(0)

        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check") as mock_check, \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = _fetch_one_side_effect
            from api_gateway.auth_rest import validate_tad_api_key
            result = await validate_tad_api_key(
                api_key, citizen_ref, strict_rate_limit=strict_rate_limit,
            )
            return result, mock_check

    @pytest.mark.asyncio
    async def test_sin_api_key_levanta_401(self):
        from api_gateway.auth_rest import validate_tad_api_key, TadAuthError
        with pytest.raises(TadAuthError) as exc_info:
            await validate_tad_api_key(None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_key_no_encontrada_levanta_401_generico(self):
        from api_gateway.auth_rest import TadAuthError
        with pytest.raises(TadAuthError) as exc_info:
            (result, _check) = await self._run("sk-x", row=None)
        assert exc_info.value.status_code == 401
        assert exc_info.value.message == "API Key invalida"

    @pytest.mark.asyncio
    async def test_key_type_no_tad_levanta_401(self):
        from api_gateway.auth_rest import TadAuthError
        with pytest.raises(TadAuthError) as exc_info:
            await self._run("sk-x", row=_tad_key_row(key_type="api"))
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_key_inactiva_levanta_401(self):
        from api_gateway.auth_rest import TadAuthError
        with pytest.raises(TadAuthError):
            await self._run("sk-x", row=_tad_key_row(key_active=False))

    @pytest.mark.asyncio
    async def test_muni_inactiva_levanta_401(self):
        from api_gateway.auth_rest import TadAuthError
        with pytest.raises(TadAuthError):
            await self._run("sk-x", row=_tad_key_row(muni_active=False))

    @pytest.mark.asyncio
    async def test_key_expirada_levanta_401(self):
        from api_gateway.auth_rest import TadAuthError
        expired = datetime.now(timezone.utc) - timedelta(days=1)
        with pytest.raises(TadAuthError):
            await self._run("sk-x", row=_tad_key_row(expires_at=expired))

    @pytest.mark.asyncio
    async def test_key_valida_sin_citizen_ref_devuelve_schema_y_none(self):
        (schema_name, citizen), mock_check = await self._run("sk-x", row=_tad_key_row())
        assert schema_name == TEST_SCHEMA
        assert citizen is None

    @pytest.mark.asyncio
    async def test_rate_limit_general_siempre_se_chequea(self):
        (_, _), mock_check = await self._run("sk-x", row=_tad_key_row())
        mock_check.assert_called_once()
        args = mock_check.call_args[0]
        assert args[0] == "tad_key:tadkey-1"

    @pytest.mark.asyncio
    async def test_rate_limit_estricto_es_un_balde_adicional(self):
        (_, _), mock_check = await self._run(
            "sk-x", row=_tad_key_row(), strict_rate_limit=10,
        )
        assert mock_check.call_count == 2
        calls = [c[0] for c in mock_check.call_args_list]
        assert calls[0][0] == "tad_key:tadkey-1"
        assert calls[1][0] == "tad_key:tadkey-1:strict"
        assert calls[1][1] == 10

    @pytest.mark.asyncio
    async def test_default_rate_limit_30_si_bd_no_trae_uno(self):
        (_, _), mock_check = await self._run(
            "sk-x", row=_tad_key_row(rate_limit_per_minute=None),
        )
        args = mock_check.call_args[0]
        assert args[1] == 30

    @pytest.mark.asyncio
    async def test_citizen_ref_uuid_resuelve_por_id(self):
        citizen_row = {
            "id": TEST_CITIZEN_ID, "full_name": "Juan Perez",
            "country_id": "20111111112", "estado": "validado",
        }
        (schema_name, citizen), _ = await self._run(
            "sk-x", citizen_ref=TEST_CITIZEN_ID, row=_tad_key_row(), citizen_row=citizen_row,
        )
        assert citizen["id"] == TEST_CITIZEN_ID
        assert citizen["estado"] == "validado"

    @pytest.mark.asyncio
    async def test_citizen_ref_cuil_resuelve_por_country_id(self):
        citizen_row = {
            "id": TEST_CITIZEN_ID, "full_name": "Juan Perez",
            "country_id": "20111111112", "estado": "pendiente",
        }
        (schema_name, citizen), _ = await self._run(
            "sk-x", citizen_ref="20111111112", row=_tad_key_row(), citizen_row=citizen_row,
        )
        assert citizen["country_id"] == "20111111112"

    @pytest.mark.asyncio
    async def test_citizen_ref_no_matchea_devuelve_none(self):
        (schema_name, citizen), _ = await self._run(
            "sk-x", citizen_ref="99999999999", row=_tad_key_row(), citizen_row=None,
        )
        assert citizen is None

    @pytest.mark.asyncio
    async def test_citizen_bloqueado_levanta_403(self):
        from api_gateway.auth_rest import TadAuthError
        citizen_row = {
            "id": TEST_CITIZEN_ID, "full_name": "Juan Perez",
            "country_id": "20111111112", "estado": "bloqueado",
        }
        with pytest.raises(TadAuthError) as exc_info:
            await self._run(
                "sk-x", citizen_ref=TEST_CITIZEN_ID, row=_tad_key_row(), citizen_row=citizen_row,
            )
        assert exc_info.value.status_code == 403


def _mock_get_conn(fetchrow_return):
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    @asynccontextmanager
    async def _fake_get_conn(*args, **kwargs):
        yield mock_conn

    return _fake_get_conn, mock_conn


class TestCitizensService:
    @pytest.mark.asyncio
    async def test_upsert_citizen_full_name_vacio_levanta_validation_error(self):
        from services.citizens.service import upsert_citizen
        from shared.exceptions import ValidationError
        with pytest.raises(ValidationError):
            await upsert_citizen("", "20111111112", "pendiente", schema_name=TEST_SCHEMA)

    @pytest.mark.asyncio
    async def test_upsert_citizen_country_id_vacio_levanta_validation_error(self):
        from services.citizens.service import upsert_citizen
        from shared.exceptions import ValidationError
        with pytest.raises(ValidationError):
            await upsert_citizen("Juan Perez", "", "pendiente", schema_name=TEST_SCHEMA)

    @pytest.mark.asyncio
    async def test_upsert_citizen_estado_invalido_levanta_validation_error(self):
        from services.citizens.service import upsert_citizen
        from shared.exceptions import ValidationError
        with pytest.raises(ValidationError):
            await upsert_citizen("Juan Perez", "20111111112", "no-existe", schema_name=TEST_SCHEMA)

    @pytest.mark.asyncio
    async def test_upsert_citizen_ok_llama_get_conn_con_auth_source_tad(self):
        from services.citizens import service as svc
        fake_get_conn, mock_conn = _mock_get_conn({
            "id": TEST_CITIZEN_ID, "full_name": "Juan Perez",
            "country_id": "20111111112", "estado": "pendiente",
            "created_via": "api", "validated_at": None, "validated_by": None,
            "created_at": None, "updated_at": None,
        })
        with patch.object(svc, "get_conn", fake_get_conn) as mock_get_conn:
            result = await svc.upsert_citizen(
                "Juan Perez", "20111111112", "pendiente", schema_name=TEST_SCHEMA,
            )
        assert result["country_id"] == "20111111112"
        mock_conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_citizen_uuid_usa_query_por_id(self):
        from services.citizens import service as svc
        with patch("services.citizens.service.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {"id": TEST_CITIZEN_ID, "country_id": "20111111112"}
            result = await svc.get_citizen(TEST_CITIZEN_ID, schema_name=TEST_SCHEMA)
            assert result["id"] == TEST_CITIZEN_ID
            sql_used = mock_fetch.call_args[0][0]
            assert "WHERE id = $1" in sql_used

    @pytest.mark.asyncio
    async def test_get_citizen_cuil_usa_query_por_country_id(self):
        from services.citizens import service as svc
        with patch("services.citizens.service.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {"id": TEST_CITIZEN_ID, "country_id": "20111111112"}
            result = await svc.get_citizen("20111111112", schema_name=TEST_SCHEMA)
            assert result["country_id"] == "20111111112"
            sql_used = mock_fetch.call_args[0][0]
            assert "WHERE country_id = $1" in sql_used

    @pytest.mark.asyncio
    async def test_get_citizen_no_encontrado_devuelve_none(self):
        from services.citizens import service as svc
        with patch("services.citizens.service.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            result = await svc.get_citizen(TEST_CITIZEN_ID, schema_name=TEST_SCHEMA)
            assert result is None

    @pytest.mark.asyncio
    async def test_set_citizen_estado_id_no_uuid_levanta_validation_error(self):
        from services.citizens.service import set_citizen_estado
        from shared.exceptions import ValidationError
        with pytest.raises(ValidationError):
            await set_citizen_estado("no-es-uuid", "validado", schema_name=TEST_SCHEMA)

    @pytest.mark.asyncio
    async def test_set_citizen_estado_invalido_levanta_validation_error(self):
        from services.citizens.service import set_citizen_estado
        from shared.exceptions import ValidationError
        with pytest.raises(ValidationError):
            await set_citizen_estado(TEST_CITIZEN_ID, "no-existe", schema_name=TEST_SCHEMA)

    @pytest.mark.asyncio
    async def test_set_citizen_estado_ok(self):
        from services.citizens import service as svc
        fake_get_conn, mock_conn = _mock_get_conn({
            "id": TEST_CITIZEN_ID, "full_name": "Juan Perez",
            "country_id": "20111111112", "estado": "bloqueado",
        })
        with patch.object(svc, "get_conn", fake_get_conn):
            result = await svc.set_citizen_estado(TEST_CITIZEN_ID, "bloqueado", schema_name=TEST_SCHEMA)
        assert result["estado"] == "bloqueado"

    @pytest.mark.asyncio
    async def test_set_citizen_estado_id_inexistente_devuelve_none(self):
        from services.citizens import service as svc
        fake_get_conn, mock_conn = _mock_get_conn(None)
        with patch.object(svc, "get_conn", fake_get_conn):
            result = await svc.set_citizen_estado(TEST_CITIZEN_ID, "validado", schema_name=TEST_SCHEMA)
        assert result is None
