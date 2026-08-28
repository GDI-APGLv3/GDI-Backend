
import importlib
from unittest.mock import AsyncMock, patch

import pytest

import database as db


@pytest.fixture(autouse=True)
def _restaurar_modulo():
    yield
    importlib.reload(db)


@pytest.mark.asyncio
async def test_el_pool_abre_las_conexiones_con_jit_off():
    mod = importlib.reload(db)
    mod.pool = None

    with patch.object(mod.asyncpg, "create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = object()
        await mod.init_pool()

    kwargs = mock_create.await_args.kwargs
    assert "server_settings" in kwargs, (
        "create_pool no recibio server_settings: las conexiones del pool salen "
        "con el jit del servidor, que en los ambientes gestionados es 'on' con umbral 100000."
    )
    assert kwargs["server_settings"].get("jit") == "off", (
        f"create_pool recibio server_settings={kwargs['server_settings']!r}; "
        "se esperaba jit='off'."
    )


@pytest.mark.parametrize("modulo, clase", [
    ("workers.escri", "EscriWorker"),
    ("workers.tad_webhook_worker", "TadWebhookWorker"),
])
@pytest.mark.asyncio
async def test_los_workers_conectan_con_jit_off(modulo, clase):
    mod = importlib.import_module(modulo)
    worker = getattr(mod, clase)()

    conn_mock = AsyncMock()
    conn_mock.add_listener.side_effect = RuntimeError("corte deliberado del test")

    with patch.object(mod.asyncpg, "connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = conn_mock
        with pytest.raises(RuntimeError, match="corte deliberado"):
            await worker._listen_loop()

    kwargs = mock_connect.await_args.kwargs
    assert kwargs.get("server_settings", {}).get("jit") == "off", (
        f"{modulo} abrio la conexion directa con server_settings="
        f"{kwargs.get('server_settings')!r}. El pool no cubre esta conexion: "
        "sin jit=off queda expuesta al umbral del servidor."
    )
