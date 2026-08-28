from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from schemas.home_schemas import build_case_href


class _FakeConn:

    def __init__(self, fetch_results: list, fetchrow_results: list):
        self._fetch_results = list(fetch_results)
        self._fetchrow_results = list(fetchrow_results)
        self.fetch_calls: list[tuple] = []
        self.fetchrow_calls: list[tuple] = []

    async def fetch(self, query, *params):
        self.fetch_calls.append((query, params))
        return self._fetch_results.pop(0)

    async def fetchrow(self, query, *params):
        self.fetchrow_calls.append((query, params))
        return self._fetchrow_results.pop(0)


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
async def test_home_unassigned_arma_unowned_y_tasks(monkeypatch):
    from services.home import service as home_service

    now = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)

    fake_conn = _FakeConn(
        fetch_results=[
            [_row(sector_id="s-1")],
            [_row(case_id="c-1", case_number="EXP-1", case_reference="Ref 1",
                  case_type="EXP-ADM", created_at=now, short_ai_summary="resumen")],
            [_row(task_id="t-1", case_id="c-2", case_number="EXP-2", case_reference="Ref 2",
                  case_type="EXP-ADM", reason="asignacion", created_at=now, short_ai_summary="resumen2")],
        ],
        fetchrow_results=[
            _row(total=4),
            _row(total=2),
        ],
    )
    monkeypatch.setattr(home_service, "get_conn", _FakeGetConn(fake_conn))

    result = await home_service.get_home_unassigned("user-1", limit=10, schema_name="100_test")

    assert result == {
        "unowned": {
            "items": [
                {
                    "case_id": "c-1",
                    "case_number": "EXP-1",
                    "case_reference": "Ref 1",
                    "case_type": "EXP-ADM",
                    "created_at": now,
                    "ai_summary": "resumen",
                    "href": build_case_href("c-1"),
                },
            ],
            "total": 4,
        },
        "tasks": {
            "items": [
                {
                    "task_id": "t-1",
                    "case_id": "c-2",
                    "case_number": "EXP-2",
                    "case_reference": "Ref 2",
                    "case_type": "EXP-ADM",
                    "reason": "asignacion",
                    "created_at": now,
                    "ai_summary": "resumen2",
                    "href": build_case_href("c-2"),
                },
            ],
            "total": 2,
        },
    }

    assert fake_conn.fetch_calls[0][1] == ("user-1", "user-1")
    assert fake_conn.fetch_calls[1][1] == ("user-1", "user-1", 10)
    assert fake_conn.fetchrow_calls[0][1] == ("user-1", "user-1")
    assert fake_conn.fetch_calls[2][1] == (["s-1"], 10)
    assert fake_conn.fetchrow_calls[1][1] == (["s-1"],)


@pytest.mark.asyncio
async def test_home_unassigned_sin_sectores_tasks_vacio_sin_consultar(monkeypatch):
    from services.home import service as home_service

    fake_conn = _FakeConn(
        fetch_results=[
            [],
            [],
        ],
        fetchrow_results=[
            _row(total=0),
        ],
    )
    monkeypatch.setattr(home_service, "get_conn", _FakeGetConn(fake_conn))

    result = await home_service.get_home_unassigned("user-1", limit=10, schema_name="100_test")

    assert result == {
        "unowned": {"items": [], "total": 0},
        "tasks": {"items": [], "total": 0},
    }
    assert len(fake_conn.fetch_calls) == 2, "no deberia haber consultado tasks sin sectores"
    assert len(fake_conn.fetchrow_calls) == 1, "no deberia haber consultado el count de tasks sin sectores"
