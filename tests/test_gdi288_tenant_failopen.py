import os

os.environ.setdefault("NOTARY_URL", "http://notary-stub.internal:8080")
os.environ.setdefault("NOTARY_API_KEY", "test-notary-api-key-stub")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared import tenant_validation


@pytest.fixture(autouse=True)
def _reset_schemas_cache():
    tenant_validation.clear_all_cache()
    yield
    tenant_validation.clear_all_cache()


class TestGetValidSchemasFailClosed:

    @pytest.mark.asyncio
    async def test_bd_ok_no_incluye_public(self):
        rows = [{"schema_name": "101_test"}, {"schema_name": "100_test"}]
        with patch("shared.tenant_validation.fetch_all", AsyncMock(return_value=rows)):
            schemas = await tenant_validation.get_valid_schemas()

        assert "public" not in schemas
        assert set(schemas) == {"101_test", "100_test"}

    @pytest.mark.asyncio
    async def test_bd_falla_escala_runtimeerror(self):
        with patch(
            "shared.tenant_validation.fetch_all",
            AsyncMock(side_effect=ConnectionError("pool exhausted")),
        ):
            with pytest.raises(RuntimeError, match="tenants válidos"):
                await tenant_validation.get_valid_schemas()

    @pytest.mark.asyncio
    async def test_bd_falla_no_cachea_el_error(self):
        fetch_mock = AsyncMock(side_effect=ConnectionError("primera"))
        with patch("shared.tenant_validation.fetch_all", fetch_mock):
            with pytest.raises(RuntimeError):
                await tenant_validation.get_valid_schemas()

        fetch_mock.side_effect = None
        fetch_mock.return_value = [{"schema_name": "101_test"}]
        with patch("shared.tenant_validation.fetch_all", fetch_mock):
            schemas = await tenant_validation.get_valid_schemas()
        assert schemas == ["101_test"]


class TestIsValidSchemaFailClosed:

    @pytest.mark.asyncio
    async def test_public_rechazado_con_bd_sana(self):
        rows = [{"schema_name": "101_test"}, {"schema_name": "100_test"}]
        with (
            patch("shared.tenant_validation.fetch_all", AsyncMock(return_value=rows)),
            patch("database.TESTING_MODE", False),
            patch("database.pool", MagicMock()),
        ):
            assert await tenant_validation.is_valid_schema("public") is False

    @pytest.mark.asyncio
    async def test_tenant_real_sigue_pasando(self):
        rows = [{"schema_name": "101_test"}, {"schema_name": "100_test"}]
        with (
            patch("shared.tenant_validation.fetch_all", AsyncMock(return_value=rows)),
            patch("database.TESTING_MODE", False),
            patch("database.pool", MagicMock()),
        ):
            assert await tenant_validation.is_valid_schema("101_test") is True

    @pytest.mark.asyncio
    async def test_bd_caida_rechaza_cualquier_schema(self):
        with (
            patch(
                "shared.tenant_validation.fetch_all",
                AsyncMock(side_effect=ConnectionError("pool exhausted")),
            ),
            patch("database.TESTING_MODE", False),
            patch("database.pool", MagicMock()),
        ):
            assert await tenant_validation.is_valid_schema("101_test") is False
            assert await tenant_validation.is_valid_schema("public") is False


def _make_request(headers: dict, path: str = "/documents/x/details", method: str = "GET"):
    from starlette.requests import Request

    header_list = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": header_list,
        "scheme": "https",
        "server": ("test", 443),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
        "state": {},
    }
    return Request(scope)


class TestMiddlewareRechazaPublicExplicitamente:

    @pytest.mark.asyncio
    async def test_testing_mode_public_400_sin_tocar_bd(self):
        from middleware.tenant_middleware import TenantMiddleware

        call_next = AsyncMock()
        with (
            patch("middleware.tenant_middleware.TESTING_MODE", True),
            patch(
                "middleware.tenant_middleware.testing_secret_matches",
                MagicMock(return_value=True),
            ),
            patch(
                "middleware.tenant_middleware.is_valid_schema",
                AsyncMock(return_value=True),
            ),
            patch(
                "middleware.tenant_middleware.find_user_by_any_identifier",
                AsyncMock(return_value={"id": "x", "email": "x", "estado": 1, "auth_id": None}),
            ),
        ):
            mw = TenantMiddleware(app=MagicMock())
            req = _make_request({
                "X-Tenant-Schema": "public",
                "X-User-ID": "a1000000-0000-0000-0000-000000000001",
                "X-Testing-Secret": "secreto-de-prueba",
            })
            resp = await mw.dispatch(req, call_next)

        assert resp.status_code == 400
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_jwt_flow_public_400_sin_tocar_bd(self):
        from middleware.tenant_middleware import TenantMiddleware
        from middleware.auth_router import ResolvedAuth

        call_next = AsyncMock()
        with (
            patch("middleware.tenant_middleware.TESTING_MODE", False),
            patch(
                "middleware.tenant_middleware.decode_jwt_from_request",
                MagicMock(return_value={"sub": "auth0|xxx", "email": "x@y.com"}),
            ),
            patch(
                "middleware.auth_router.resolve_auth",
                AsyncMock(return_value=None),
            ),
            patch(
                "middleware.tenant_middleware.is_valid_schema",
                AsyncMock(return_value=True),
            ),
            patch(
                "middleware.tenant_middleware.find_user_by_any_identifier",
                AsyncMock(return_value={"id": "x", "email": "x@y.com", "estado": 1, "auth_id": None}),
            ),
            patch(
                "shared.tenant_validation.validate_tenant_access",
                AsyncMock(return_value=True),
            ),
        ):
            mw = TenantMiddleware(app=MagicMock())
            req = _make_request({
                "Authorization": "Bearer fake.jwt.token",
                "X-Tenant-Schema": "public",
            })
            resp = await mw.dispatch(req, call_next)

        assert resp.status_code == 400
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_tenant_valido_sigue_funcionando_testing_mode(self):
        from middleware.tenant_middleware import TenantMiddleware

        expected = MagicMock(status_code=200, headers={})
        call_next = AsyncMock(return_value=expected)

        with (
            patch("middleware.tenant_middleware.TESTING_MODE", True),
            patch(
                "middleware.tenant_middleware.testing_secret_matches",
                MagicMock(return_value=True),
            ),
            patch(
                "middleware.tenant_middleware.is_valid_schema",
                AsyncMock(return_value=True),
            ),
            patch(
                "middleware.tenant_middleware.find_user_by_any_identifier",
                AsyncMock(return_value={"id": "u1", "email": "e@x", "estado": 1, "auth_id": "a"}),
            ),
        ):
            mw = TenantMiddleware(app=MagicMock())
            req = _make_request({
                "X-Tenant-Schema": "100_test",
                "X-User-ID": "a1000000-0000-0000-0000-000000000001",
                "X-Testing-Secret": "secreto-de-prueba",
            })
            resp = await mw.dispatch(req, call_next)

        call_next.assert_awaited_once()
        assert resp is expected
