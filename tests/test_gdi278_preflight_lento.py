
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database as db


def _conn_mock(name: str, *, preflight):
    conn = MagicMock(name=name)
    conn.nombre = name
    conn.fetchval = AsyncMock(side_effect=preflight)
    conn.terminate = MagicMock()
    return conn


async def _lento(*_a, **_kw):
    await asyncio.sleep(3600)


async def _sano(*_a, **_kw):
    return 1


def _pool_con(conns):
    pool = MagicMock()
    entregadas = iter(conns)
    pool.acquire = AsyncMock(side_effect=lambda **_kw: next(entregadas))
    pool.release = AsyncMock()
    return pool


@pytest.mark.asyncio
async def test_preflight_lento_reintenta_en_vez_de_morir_al_primer_tropiezo():
    conns = [_conn_mock(f"c{i}", preflight=_lento) for i in range(2)]
    pool = _pool_con(conns)

    with patch.object(db, "get_pool", return_value=pool), \
         patch.object(db, "_CONN_HEALTHCHECK_TIMEOUT", 0.01), \
         patch.object(db, "_CONN_RETRY_DELAY", 0):
        with pytest.raises(Exception):
            await db._acquire_healthy_conn()

    assert pool.acquire.await_count > 1, "no reintento tras el primer timeout"


@pytest.mark.asyncio
async def test_preflight_lento_reintenta_con_otra_conn():
    lenta = _conn_mock("lenta", preflight=_lento)
    sana = _conn_mock("sana", preflight=_sano)
    pool = _pool_con([lenta, sana])

    with patch.object(db, "get_pool", return_value=pool), \
         patch.object(db, "_CONN_HEALTHCHECK_TIMEOUT", 0.01), \
         patch.object(db, "_CONN_RETRY_DELAY", 0):
        conn = await db._acquire_healthy_conn()

    assert conn is sana, "deberia haber reintentado y entregado la conn sana"
    assert pool.acquire.await_count == 2, "no reintento"
    assert not lenta.terminate.called


@pytest.mark.asyncio
async def test_conn_realmente_muerta_si_se_descarta():
    async def _rota(*_a, **_kw):
        raise ConnectionResetError("TCP cortado")

    muerta = _conn_mock("muerta", preflight=_rota)
    sana = _conn_mock("sana", preflight=_sano)
    pool = _pool_con([muerta, sana])

    with patch.object(db, "get_pool", return_value=pool), \
         patch.object(db, "_CONN_RETRY_DELAY", 0):
        conn = await db._acquire_healthy_conn()

    assert conn is sana
    assert muerta.terminate.called, "una conn con TCP roto SI debe descartarse"


@pytest.mark.asyncio
async def test_pool_saturado_sigue_dando_503():
    from shared.exceptions import DatabaseBusyError

    pool = MagicMock()
    pool.acquire = AsyncMock(side_effect=asyncio.TimeoutError())
    pool.release = AsyncMock()

    with patch.object(db, "get_pool", return_value=pool):
        with pytest.raises(DatabaseBusyError):
            await db._acquire_healthy_conn()
