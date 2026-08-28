
import asyncio
import time
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


def _pool_con(conns):
    pool = MagicMock()
    entregadas = iter(conns)
    pool.acquire = AsyncMock(side_effect=lambda **_kw: next(entregadas))
    pool.release = AsyncMock()
    return pool


@pytest.mark.asyncio
async def test_presupuesto_total_acota_el_tiempo_no_lo_multiplica():
    conns = [_conn_mock(f"c{i}", preflight=_lento) for i in range(5)]
    pool = _pool_con(conns)

    with patch.object(db, "get_pool", return_value=pool), \
         patch.object(db, "_CONN_HEALTHCHECK_TIMEOUT", 0.05), \
         patch.object(db, "_POOL_ACQUIRE_TIMEOUT", 0.01), \
         patch.object(db, "_ACQUIRE_TOTAL_BUDGET", 0.06), \
         patch.object(db, "_CONN_RETRY_DELAY", 0):
        from shared.exceptions import DatabaseBusyError

        start = time.monotonic()
        with pytest.raises(DatabaseBusyError):
            await db._acquire_healthy_conn()
        elapsed = time.monotonic() - start

    assert elapsed < 0.12, (
        f"tardo {elapsed:.3f}s — el presupuesto compartido deberia haber "
        f"cortado los reintentos mucho antes de sumar 3 pre-flights completos"
    )


@pytest.mark.asyncio
async def test_presupuesto_agotado_no_pide_mas_acquires_al_pool():
    conns = [_conn_mock(f"c{i}", preflight=_lento) for i in range(5)]
    pool = _pool_con(conns)

    with patch.object(db, "get_pool", return_value=pool), \
         patch.object(db, "_CONN_HEALTHCHECK_TIMEOUT", 0.03), \
         patch.object(db, "_POOL_ACQUIRE_TIMEOUT", 0.01), \
         patch.object(db, "_ACQUIRE_TOTAL_BUDGET", 0.03), \
         patch.object(db, "_CONN_RETRY_DELAY", 0):
        from shared.exceptions import DatabaseBusyError

        with pytest.raises(DatabaseBusyError):
            await db._acquire_healthy_conn()

    assert pool.acquire.await_count == 1, (
        f"pidio {pool.acquire.await_count} conexiones con el presupuesto ya agotado"
    )


@pytest.mark.asyncio
async def test_reintento_usa_lo_que_queda_del_presupuesto_no_el_timeout_completo():
    import time as _time_module

    reloj_falso = {"t": 0.0}

    def monotonic_falso():
        return reloj_falso["t"]

    timeouts_usados = []

    async def wait_for_espia(coro, timeout):
        timeouts_usados.append(timeout)
        coro.close()
        reloj_falso["t"] += timeout
        raise asyncio.TimeoutError()

    conns = [_conn_mock(f"c{i}", preflight=_lento) for i in range(3)]
    pool = _pool_con(conns)

    with patch.object(db, "get_pool", return_value=pool), \
         patch.object(db, "_CONN_HEALTHCHECK_TIMEOUT", 0.05), \
         patch.object(db, "_POOL_ACQUIRE_TIMEOUT", 0.01), \
         patch.object(db, "_ACQUIRE_TOTAL_BUDGET", 0.06), \
         patch.object(db, "_CONN_RETRY_DELAY", 0), \
         patch.object(_time_module, "monotonic", monotonic_falso), \
         patch("asyncio.wait_for", wait_for_espia):
        from shared.exceptions import DatabaseBusyError

        with pytest.raises(DatabaseBusyError):
            await db._acquire_healthy_conn()

    assert len(timeouts_usados) >= 2, "no llego a un segundo intento para comparar"
    assert timeouts_usados[1] < timeouts_usados[0], (
        f"timeout del reintento ({timeouts_usados[1]:.4f}s) deberia ser menor "
        f"al del primer intento ({timeouts_usados[0]:.4f}s) — el presupuesto "
        f"restante, no uno nuevo"
    )


@pytest.mark.asyncio
async def test_conn_sana_de_entrada_no_se_ve_afectada_por_el_presupuesto():
    async def _sano(*_a, **_kw):
        return 1

    sana = _conn_mock("sana", preflight=_sano)
    pool = _pool_con([sana])

    with patch.object(db, "get_pool", return_value=pool):
        conn = await db._acquire_healthy_conn()

    assert conn is sana
    assert pool.acquire.await_count == 1


@pytest.mark.asyncio
async def test_preflight_lento_agotado_da_503_sin_descartar_conn():
    from shared.exceptions import DatabaseBusyError

    conns = [_conn_mock(f"c{i}", preflight=_lento) for i in range(5)]
    pool = _pool_con(conns)

    with patch.object(db, "get_pool", return_value=pool), \
         patch.object(db, "_CONN_HEALTHCHECK_TIMEOUT", 0.01), \
         patch.object(db, "_CONN_RETRY_DELAY", 0):
        with pytest.raises(DatabaseBusyError):
            await db._acquire_healthy_conn()

    for c in conns:
        assert not c.terminate.called, f"{c.nombre}: terminate() por un timeout"
