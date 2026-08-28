from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeConn:

    def __init__(self, fetchrow_results: list):
        self._results = list(fetchrow_results)
        self.fetchrow_calls: list[tuple] = []

    async def fetchrow(self, query, *params):
        self.fetchrow_calls.append((query, params))
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
async def test_get_display_state_name_usa_conn_si_se_pasa(monkeypatch):
    from services.documents.catalog import states as states_module

    fake_conn = _FakeConn([_row(display_state_name="Firmado")])
    fetch_one_mock = AsyncMock()
    monkeypatch.setattr(states_module, "fetch_one", fetch_one_mock)

    result = await states_module.get_display_state_name(
        "signed", schema_name="100_test", conn=fake_conn,
    )

    assert result == "Firmado"
    assert len(fake_conn.fetchrow_calls) == 1, "deberia haber usado la conn pasada"
    fetch_one_mock.assert_not_called()


@pytest.mark.asyncio
async def test_get_display_state_name_sin_conn_usa_fetch_one_como_siempre(monkeypatch):
    from services.documents.catalog import states as states_module

    fetch_one_mock = AsyncMock(return_value={"display_state_name": "Firmado"})
    monkeypatch.setattr(states_module, "fetch_one", fetch_one_mock)

    result = await states_module.get_display_state_name("signed", schema_name="100_test")

    assert result == "Firmado"
    fetch_one_mock.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_document_basic_info_pasa_su_propia_conn_a_get_display_state_name(monkeypatch):
    import database as db_module
    from services.documents.preview import data_fetcher as data_fetcher_module

    fake_conn = _FakeConn([
        _row(
            document_id="doc-1", reference="Informe", content={"html": "<p>hola</p>"},
            created_by="user-1", document_generate_id="doc-1", document_type_id=1,
            status="draft", type_acronym="IF", type_name="Informe", source_type="HTML",
            has_fields=False, type_visibility="privado",
        ),
        _row(field_definitions=None),
        _row(logo_url="https://logo"),
    ])
    monkeypatch.setattr(db_module, "get_conn", _FakeGetConn(fake_conn))

    display_state_spy = AsyncMock(return_value="En edición")
    monkeypatch.setattr(data_fetcher_module, "get_display_state_name", display_state_spy)

    fetcher = data_fetcher_module.PreviewDataFetcher(schema_name="100_test")
    result = await fetcher._fetch_document_basic_info("doc-1")

    assert result["display_status"] == "En edición"
    display_state_spy.assert_awaited_once_with("draft", schema_name="100_test", conn=fake_conn)


@pytest.mark.asyncio
async def test_get_complete_document_data_reusa_document_info_sin_refetch(monkeypatch):
    from services.documents.preview import data_fetcher as data_fetcher_module

    fetch_basic_spy = AsyncMock()
    monkeypatch.setattr(
        data_fetcher_module.PreviewDataFetcher, "_fetch_document_basic_info", fetch_basic_spy,
    )
    monkeypatch.setattr(
        data_fetcher_module, "get_user_complete_data",
        AsyncMock(return_value={"name": "Juan"}),
    )
    monkeypatch.setattr(
        data_fetcher_module, "get_document_signers_for_preview",
        AsyncMock(return_value=[]),
    )
    fake_builder = MagicMock()
    fake_builder.build_preview_response.return_value = {"preview": "ok"}
    monkeypatch.setattr(
        data_fetcher_module, "DocumentResponseBuilder",
        MagicMock(return_value=fake_builder),
    )

    fetcher = data_fetcher_module.PreviewDataFetcher(schema_name="100_test")
    raw = {"created_by": "user-1", "status": "draft"}

    result = await fetcher.get_complete_document_data("doc-1", document_info=raw)

    assert result == {"preview": "ok"}
    fetch_basic_spy.assert_not_called()


@pytest.mark.asyncio
async def test_get_complete_document_data_sin_document_info_fetchea_como_siempre(monkeypatch):
    from services.documents.preview import data_fetcher as data_fetcher_module

    fetch_basic_spy = AsyncMock(return_value={"created_by": "user-1", "status": "draft"})
    monkeypatch.setattr(
        data_fetcher_module.PreviewDataFetcher, "_fetch_document_basic_info", fetch_basic_spy,
    )
    monkeypatch.setattr(
        data_fetcher_module, "get_user_complete_data",
        AsyncMock(return_value={"name": "Juan"}),
    )
    monkeypatch.setattr(
        data_fetcher_module, "get_document_signers_for_preview",
        AsyncMock(return_value=[]),
    )
    fake_builder = MagicMock()
    fake_builder.build_preview_response.return_value = {"preview": "ok"}
    monkeypatch.setattr(
        data_fetcher_module, "DocumentResponseBuilder",
        MagicMock(return_value=fake_builder),
    )

    fetcher = data_fetcher_module.PreviewDataFetcher(schema_name="100_test")
    result = await fetcher.get_complete_document_data("doc-1")

    assert result == {"preview": "ok"}
    fetch_basic_spy.assert_called_once_with("doc-1")
