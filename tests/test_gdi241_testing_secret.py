
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_gdi288_tenant_failopen import _make_request


SECRET = "secreto-compartido-de-prueba"


def _patches(testing_mode=True):
    return [
        patch("middleware.tenant_middleware.TESTING_MODE", testing_mode),
        patch("database.TESTING_SHARED_SECRET", SECRET),
        patch(
            "middleware.tenant_middleware.is_valid_schema",
            AsyncMock(return_value=True),
        ),
        patch(
            "middleware.tenant_middleware.find_user_by_any_identifier",
            AsyncMock(return_value={"id": "u1", "email": "e@x", "estado": 1, "auth_id": "a"}),
        ),
    ]


def _enter(stack, extra=()):
    for ctx in [*_patches(), *extra]:
        stack.enter_context(ctx)


class TestSecretoObligatorio:

    @pytest.mark.asyncio
    async def test_sin_header_no_entra_a_la_rama_testing(self):
        from middleware.tenant_middleware import TenantMiddleware

        call_next = AsyncMock()
        with ExitStack() as stack:
            _enter(stack, [patch("middleware.auth_router.resolve_auth", AsyncMock(return_value=None))])
            mw = TenantMiddleware(app=MagicMock())
            req = _make_request({
                "X-Tenant-Schema": "100_test",
                "X-User-ID": "a1000000-0000-0000-0000-000000000001",
            })
            resp = await mw.dispatch(req, call_next)

        call_next.assert_not_called()
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_header_equivocado_no_entra(self):
        from middleware.tenant_middleware import TenantMiddleware

        call_next = AsyncMock()
        with ExitStack() as stack:
            _enter(stack, [patch("middleware.auth_router.resolve_auth", AsyncMock(return_value=None))])
            mw = TenantMiddleware(app=MagicMock())
            req = _make_request({
                "X-Tenant-Schema": "100_test",
                "X-User-ID": "a1000000-0000-0000-0000-000000000001",
                "X-Testing-Secret": SECRET + "-mal",
            })
            resp = await mw.dispatch(req, call_next)

        call_next.assert_not_called()
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_con_el_secreto_correcto_sigue_funcionando(self):
        from middleware.tenant_middleware import TenantMiddleware

        expected = MagicMock(status_code=200, headers={})
        call_next = AsyncMock(return_value=expected)

        with ExitStack() as stack:
            _enter(stack)
            mw = TenantMiddleware(app=MagicMock())
            req = _make_request({
                "X-Tenant-Schema": "100_test",
                "X-User-ID": "a1000000-0000-0000-0000-000000000001",
                "X-Testing-Secret": SECRET,
            })
            resp = await mw.dispatch(req, call_next)

        call_next.assert_awaited_once()
        assert resp is expected


class TestFailClosedDeLaConfiguracion:

    def test_sin_secreto_configurado_no_hay_modo_testing(self):
        import database

        with patch("database.TESTING_SHARED_SECRET", ""):
            assert database.testing_secret_matches("") is False
            assert database.testing_secret_matches("cualquier-cosa") is False

    def test_valor_vacio_nunca_matchea(self):
        import database

        with patch("database.TESTING_SHARED_SECRET", SECRET):
            assert database.testing_secret_matches(None) is False
            assert database.testing_secret_matches("") is False
            assert database.testing_secret_matches(SECRET) is True
