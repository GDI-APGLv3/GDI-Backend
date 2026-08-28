from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from schemas.home_schemas import build_sign_href, build_memo_href, build_note_href


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
async def test_home_actionable_arma_las_tres_cajas(monkeypatch):
    from services.home import service as home_service

    now = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)

    sign_rows = [
        _row(
            document_id="doc-1", reference="Informe", document_number="IF-1",
            document_type_acronym="IF", document_type_name="Informe",
            signer_role="signer", signing_order=1, sent_to_sign_at=now,
            short_resume="resumen corto", creator_name="Juan", creator_photo="https://x/juan.jpg",
        ),
    ]
    memo_rows = [
        _row(
            document_id="memo-1", official_number="ME-1", reference="Memo urgente",
            ai_summary="resumen largo", short_ai_summary="resumen corto",
            signed_at=now, creator_name="Ana", creator_photo=None,
        ),
    ]
    sector_rows = [_row(sector_id="s-1")]
    note_rows = [
        _row(
            document_id="nota-1", official_number="NOTA-1", reference="Nota importante",
            ai_summary="resumen largo", short_ai_summary="resumen corto",
            signed_at=now, department_acronym="OBRA", sector_acronym="PRIV",
        ),
    ]

    fake_conn = _FakeConn([sign_rows, memo_rows, sector_rows, note_rows])
    monkeypatch.setattr(home_service, "get_conn", _FakeGetConn(fake_conn))

    result = await home_service.get_home_actionable("user-1", limit=5, schema_name="100_test")

    assert result == {
        "sign": [
            {
                "key": "sign:doc-1",
                "document_id": "doc-1",
                "reference": "Informe",
                "document_number": "IF-1",
                "document_type_acronym": "IF",
                "document_type_name": "Informe",
                "signer_role": "signer",
                "sent_to_sign_at": str(now),
                "short_ai_summary": "resumen corto",
                "creator": {"name": "Juan", "photo_url": "https://x/juan.jpg", "sector_label": None},
                "href": build_sign_href("doc-1"),
            },
        ],
        "memo": [
            {
                "key": "memo:memo-1",
                "document_id": "memo-1",
                "official_number": "ME-1",
                "reference": "Memo urgente",
                "ai_summary": "resumen largo",
                "short_ai_summary": "resumen corto",
                "signed_at": now,
                "creator": {"name": "Ana", "photo_url": None, "sector_label": None},
                "href": build_memo_href("memo-1"),
            },
        ],
        "note": [
            {
                "key": "note:nota-1",
                "document_id": "nota-1",
                "official_number": "NOTA-1",
                "reference": "Nota importante",
                "ai_summary": "resumen largo",
                "short_ai_summary": "resumen corto",
                "signed_at": now,
                "sender": {"name": None, "photo_url": None, "sector_label": "OBRA#PRIV"},
                "href": build_note_href("nota-1"),
            },
        ],
    }

    assert len(fake_conn.calls) == 4
    assert fake_conn.calls[0][1] == ("user-1", 5)
    assert fake_conn.calls[1][1] == ("user-1", 5)
    assert fake_conn.calls[2][1] == ("user-1", "user-1")
    assert fake_conn.calls[3][1] == (["s-1"], 5)


@pytest.mark.asyncio
async def test_home_actionable_sin_sectores_no_consulta_notas(monkeypatch):
    from services.home import service as home_service

    fake_conn = _FakeConn([
        [],
        [],
        [],
    ])
    monkeypatch.setattr(home_service, "get_conn", _FakeGetConn(fake_conn))

    result = await home_service.get_home_actionable("user-1", limit=5, schema_name="100_test")

    assert result == {"sign": [], "memo": [], "note": []}
    assert len(fake_conn.calls) == 3, "no deberia haber consultado notas sin sectores"
