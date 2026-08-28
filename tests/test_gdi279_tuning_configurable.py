
import importlib
import os
from unittest.mock import patch

import pytest

import database as db


def _recargar_con(**env):
    with patch.dict(os.environ, env, clear=False):
        return importlib.reload(db)


@pytest.fixture(autouse=True)
def _restaurar_modulo():
    yield
    importlib.reload(db)


def test_healthcheck_timeout_se_lee_del_entorno():
    mod = _recargar_con(CONN_HEALTHCHECK_TIMEOUT="0.5")
    assert mod._CONN_HEALTHCHECK_TIMEOUT == 0.5


def test_max_inactive_lifetime_se_lee_del_entorno():
    mod = _recargar_con(ASYNCPG_MAX_INACTIVE_LIFETIME="60")
    assert mod.ASYNCPG_MAX_INACTIVE_LIFETIME == 60.0


def test_defaults_son_los_valores_medidos_como_correctos():
    entorno_sin_las_dos = {
        k: v for k, v in os.environ.items()
        if k not in ("CONN_HEALTHCHECK_TIMEOUT", "ASYNCPG_MAX_INACTIVE_LIFETIME")
    }
    with patch.dict(os.environ, entorno_sin_las_dos, clear=True):
        mod = importlib.reload(db)
        assert mod._CONN_HEALTHCHECK_TIMEOUT == 0.5
        assert mod.ASYNCPG_MAX_INACTIVE_LIFETIME == 600.0


@pytest.mark.asyncio
async def test_el_pool_usa_el_valor_configurado_no_uno_hardcodeado():
    from unittest.mock import AsyncMock, patch as _patch

    mod = _recargar_con(ASYNCPG_MAX_INACTIVE_LIFETIME="60")

    mod.pool = None
    with _patch.object(mod.asyncpg, "create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = object()
        await mod.init_pool()

    kwargs = mock_create.await_args.kwargs
    assert kwargs["max_inactive_connection_lifetime"] == 60.0, (
        f"create_pool recibio max_inactive_connection_lifetime="
        f"{kwargs['max_inactive_connection_lifetime']!r}, no el 60.0 del env."
    )
