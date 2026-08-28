
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from shared.exceptions import DatabaseBusyError, TransientLookupError


TRANSITORIOS = [
    DatabaseBusyError("Pool de conexiones saturado (acquire timeout)"),
    asyncio.TimeoutError(),
]


@pytest.mark.parametrize("exc", TRANSITORIOS)
@pytest.mark.asyncio
async def test_permisos_sector_no_devuelve_vacio_ante_error_transitorio(exc):
    from services import user_service

    with patch("services.user_service.fetch_all", new=AsyncMock(side_effect=exc)):
        with pytest.raises(TransientLookupError):
            await user_service.get_user_sector_permissions(
                "11111111-1111-1111-1111-111111111111", schema_name="100_test"
            )


@pytest.mark.asyncio
async def test_permisos_sector_si_devuelve_vacio_cuando_la_query_corrio_sana():
    from services import user_service

    with patch("services.user_service.fetch_all", new=AsyncMock(return_value=[])):
        assert await user_service.get_user_sector_permissions(
            "11111111-1111-1111-1111-111111111111", schema_name="100_test"
        ) == []


@pytest.mark.asyncio
async def test_auth_no_tapa_el_error_con_una_segunda_red():
    import auth

    with patch.object(
        auth.user_service,
        "get_user_sector_permissions",
        new=AsyncMock(side_effect=TransientLookupError("BD saturada")),
    ):
        with pytest.raises(TransientLookupError):
            await auth.load_user_permissions(
                "11111111-1111-1111-1111-111111111111", schema_name="100_test"
            )


@pytest.mark.parametrize("exc", TRANSITORIOS)
@pytest.mark.asyncio
async def test_can_view_case_no_dice_False_ante_error_transitorio(exc):
    from services.cases import permissions as case_perms

    with patch("services.cases.permissions.fetch_all", new=AsyncMock(side_effect=exc)):
        with pytest.raises(TransientLookupError):
            await case_perms.can_user_view_case(
                "22222222-2222-2222-2222-222222222222",
                "11111111-1111-1111-1111-111111111111",
                schema_name="100_test",
            )


@pytest.mark.parametrize("exc", TRANSITORIOS)
@pytest.mark.asyncio
async def test_can_edit_case_no_dice_False_ante_error_transitorio(exc):
    from services.cases import permissions as case_perms

    with patch.object(
        case_perms, "get_user_case_permissions", new=AsyncMock(side_effect=exc)
    ):
        with pytest.raises(TransientLookupError):
            await case_perms.can_user_edit_case(
                "22222222-2222-2222-2222-222222222222",
                "11111111-1111-1111-1111-111111111111",
                schema_name="100_test",
            )


@pytest.mark.asyncio
async def test_can_edit_case_sigue_diciendo_False_si_el_caso_no_existe():
    from shared.exceptions import NotFoundError
    from services.cases import permissions as case_perms

    with patch.object(
        case_perms,
        "get_user_case_permissions",
        new=AsyncMock(side_effect=NotFoundError("no existe")),
    ):
        assert await case_perms.can_user_edit_case(
            "22222222-2222-2222-2222-222222222222",
            "11111111-1111-1111-1111-111111111111",
            schema_name="100_test",
        ) is False


@pytest.mark.asyncio
async def test_el_total_cero_no_se_guarda_en_cache():
    from services import cache as cache_module

    guardado = {}

    class FakeRedis:
        def get(self, k):
            return None

        def setex(self, k, ttl, v):
            guardado[k] = v
            return True

    with patch.object(cache_module, "get_redis", lambda: FakeRedis()):
        total = await cache_module.get_cached(
            "cases:count:test", lambda: 0, ttl=30, cache_if=lambda t: t != 0
        )

    assert total == 0
    assert guardado == {}


@pytest.mark.asyncio
async def test_un_total_distinto_de_cero_si_se_cachea():
    from services import cache as cache_module

    guardado = {}

    class FakeRedis:
        def get(self, k):
            return None

        def setex(self, k, ttl, v):
            guardado[k] = v
            return True

    with patch.object(cache_module, "get_redis", lambda: FakeRedis()):
        total = await cache_module.get_cached(
            "cases:count:test", lambda: 7, ttl=30, cache_if=lambda t: t != 0
        )

    assert total == 7
    assert guardado == {"cases:count:test": "7"}


