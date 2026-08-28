from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from schemas.home_schemas import build_case_href


class _FakeConn:

    def __init__(self, fetch_results: list[list[dict]]):
        self._results = list(fetch_results)
        self.calls: list[tuple] = []

    async def fetch(self, query, *params):
        self.calls.append((query, params))
        return self._results.pop(0)


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
async def test_first_page_builds_expected_dict(monkeypatch):
    from services.home import service as home_service

    now = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)

    responsible_rows = [
        _row(
            movement_id="m-resp-1", case_id="c-1", case_number="EXP-2026-001",
            case_reference="Ref 1", case_type="EXP-ADM", reason="asignacion",
            created_at=now, actor_name="Juan Perez", actor_photo="https://x/juan.jpg",
        ),
    ]
    mention_rows = [
        _row(
            movement_id="m-ment-1", case_id="c-2", case_number="EXP-2026-002",
            case_reference="Ref 2", case_type="EXP-ADM", reason="comentario",
            created_at=now, actor_name="Ana Gomez", actor_photo=None,
        ),
        _row(
            movement_id="m-ment-2", case_id="c-3", case_number="EXP-2026-003",
            case_reference="Ref 3", case_type="EXP-ADM", reason="comentario",
            created_at=now, actor_name="Ana Gomez", actor_photo=None,
        ),
    ]
    viewable_rows = [_row(id="c-2")]
    movement_rows = [
        _row(
            case_id="c-4", case_number="EXP-2026-004", case_reference="Ref 4",
            case_type="EXP-ADM", short_ai_summary="resumen", new_count=2,
            last_move_at=now,
        ),
    ]

    fake_conn = _FakeConn([responsible_rows, mention_rows, viewable_rows, [], movement_rows])
    monkeypatch.setattr(home_service, "get_conn", _FakeGetConn(fake_conn))

    result = await home_service.get_home_cases(
        "user-1", "mine", limit=10, cursor=None, schema_name="100_test"
    )

    assert result == {
        "scope": "mine",
        "failed_signatures": [],
        "responsible": [
            {
                "key": "responsible:m-resp-1",
                "movement_id": "m-resp-1",
                "case_id": "c-1",
                "case_number": "EXP-2026-001",
                "case_reference": "Ref 1",
                "case_type": "EXP-ADM",
                "reason": "asignacion",
                "created_at": now,
                "actor": {"name": "Juan Perez", "photo_url": "https://x/juan.jpg", "sector_label": None},
                "href": build_case_href("c-1"),
            },
        ],
        "mention": [
            {
                "key": "mention:m-ment-1",
                "movement_id": "m-ment-1",
                "case_id": "c-2",
                "case_number": "EXP-2026-002",
                "case_reference": "Ref 2",
                "case_type": "EXP-ADM",
                "reason": "comentario",
                "created_at": now,
                "actor": {"name": "Ana Gomez", "photo_url": None, "sector_label": None},
                "can_view": True,
                "href": build_case_href("c-2"),
            },
            {
                "key": "mention:m-ment-2",
                "movement_id": "m-ment-2",
                "case_id": "c-3",
                "case_number": "EXP-2026-003",
                "case_reference": "Ref 3",
                "case_type": "EXP-ADM",
                "reason": "comentario",
                "created_at": now,
                "actor": {"name": "Ana Gomez", "photo_url": None, "sector_label": None},
                "can_view": False,
                "href": None,
            },
        ],
        "case_movements": {
            "items": [
                {
                    "case_id": "c-4",
                    "case_number": "EXP-2026-004",
                    "case_reference": "Ref 4",
                    "case_type": "EXP-ADM",
                    "short_ai_summary": "resumen",
                    "new_count": 2,
                    "last_move_at": now,
                    "href": build_case_href("c-4"),
                },
            ],
            "next_cursor": None,
        },
    }

    assert fake_conn.calls[0][1] == ("user-1",)
    assert fake_conn.calls[1][1] == ("user-1",)
    assert fake_conn.calls[2][1] == ("user-1", "user-1")
    assert fake_conn.calls[3][1] == ("user-1", "100_test")
    assert fake_conn.calls[4][1] == ("user-1", "user-1", "user-1", "mine", None, None, 11)


@pytest.mark.asyncio
async def test_page_with_cursor_skips_side_lists_and_encodes_next_cursor(monkeypatch):
    from services.home import service as home_service

    t1 = datetime(2026, 8, 10, 9, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 9, 9, 0, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)

    movement_rows = [
        _row(case_id="c-1", case_number="N1", case_reference="R1", case_type="T", short_ai_summary=None, new_count=1, last_move_at=t1),
        _row(case_id="c-2", case_number="N2", case_reference="R2", case_type="T", short_ai_summary=None, new_count=1, last_move_at=t2),
        _row(case_id="c-3", case_number="N3", case_reference="R3", case_type="T", short_ai_summary=None, new_count=1, last_move_at=t3),
    ]

    fake_conn = _FakeConn([movement_rows])
    monkeypatch.setattr(home_service, "get_conn", _FakeGetConn(fake_conn))

    cursor = home_service._encode_cursor(datetime(2026, 8, 11, 9, 0, 0, tzinfo=timezone.utc), "c-0")
    result = await home_service.get_home_cases(
        "user-1", "all", limit=2, cursor=cursor, schema_name="100_test"
    )

    assert result["responsible"] == []
    assert result["failed_signatures"] == []
    assert result["mention"] == []
    assert len(fake_conn.calls) == 1

    items = result["case_movements"]["items"]
    assert [i["case_id"] for i in items] == ["c-1", "c-2"]

    expected_next_cursor = home_service._encode_cursor(t2, "c-2")
    assert result["case_movements"]["next_cursor"] == expected_next_cursor


@pytest.mark.asyncio
async def test_invalid_scope_raises_before_touching_db(monkeypatch):
    from services.home import service as home_service
    from shared.exceptions import ValidationError

    def _explode(*, schema_name):
        raise AssertionError("get_conn no deberia llamarse con scope invalido")

    monkeypatch.setattr(home_service, "get_conn", _explode)

    with pytest.raises(ValidationError):
        await home_service.get_home_cases(
            "user-1", "invalido", limit=10, cursor=None, schema_name="100_test"
        )
