from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeConn:

    def __init__(self, fetchrow_results: list, fetch_results: list):
        self._fetchrow_results = list(fetchrow_results)
        self._fetch_results = list(fetch_results)
        self.fetchrow_calls: list[tuple] = []
        self.fetch_calls: list[tuple] = []

    async def fetchrow(self, query, *params):
        self.fetchrow_calls.append((query, params))
        return self._fetchrow_results.pop(0)

    async def fetch(self, query, *params):
        self.fetch_calls.append((query, params))
        return self._fetch_results.pop(0)


class _FakeGetConn:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def __call__(self, *, schema_name):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _row(**kwargs) -> dict:
    return kwargs


@pytest.mark.asyncio
async def test_home_count_suma_las_tres_fuentes(monkeypatch):
    from services.home import service as home_service

    fake_conn = _FakeConn(
        fetchrow_results=[
            _row(total=3),
            _row(total=2),
            _row(total=5),
        ],
        fetch_results=[
            [_row(sector_id="s-1"), _row(sector_id="s-2")],
        ],
    )
    monkeypatch.setattr(home_service, "get_conn", _FakeGetConn(fake_conn))

    result = await home_service.get_home_count("user-1", schema_name="100_test")

    assert result == {
        "actionable_total": 10,
        "by_source": {"sign": 3, "memo": 2, "note": 5},
    }
    assert len(fake_conn.fetchrow_calls) == 3
    assert len(fake_conn.fetch_calls) == 1
    assert fake_conn.fetchrow_calls[0][1] == ("user-1",)
    assert fake_conn.fetchrow_calls[1][1] == ("user-1",)
    assert fake_conn.fetch_calls[0][1] == ("user-1", "user-1")
    assert fake_conn.fetchrow_calls[2][1] == (["s-1", "s-2"],)


@pytest.mark.asyncio
async def test_home_count_sin_sectores_no_consulta_notas(monkeypatch):
    from services.home import service as home_service

    fake_conn = _FakeConn(
        fetchrow_results=[
            _row(total=0),
            _row(total=0),
        ],
        fetch_results=[
            [],
        ],
    )
    monkeypatch.setattr(home_service, "get_conn", _FakeGetConn(fake_conn))

    result = await home_service.get_home_count("user-1", schema_name="100_test")

    assert result == {
        "actionable_total": 0,
        "by_source": {"sign": 0, "memo": 0, "note": 0},
    }
    assert len(fake_conn.fetchrow_calls) == 2, "no deberia haber consultado el count de notas"


@pytest.mark.asyncio
async def test_home_count_fila_nula_cuenta_como_cero(monkeypatch):
    from services.home import service as home_service

    fake_conn = _FakeConn(
        fetchrow_results=[None, None, None],
        fetch_results=[[_row(sector_id="s-1")]],
    )
    monkeypatch.setattr(home_service, "get_conn", _FakeGetConn(fake_conn))

    result = await home_service.get_home_count("user-1", schema_name="100_test")

    assert result == {
        "actionable_total": 0,
        "by_source": {"sign": 0, "memo": 0, "note": 0},
    }
