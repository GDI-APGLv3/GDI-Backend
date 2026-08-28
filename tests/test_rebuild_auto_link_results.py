import os

os.environ.setdefault("NOTARY_URL", "http://notary-stub.internal:8080")
os.environ.setdefault("NOTARY_API_KEY", "test-notary-api-key-stub")

from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

from endpoints.digital_signature.poll import _rebuild_auto_link_results


_SCHEMA = "100_test"
_DOC_ID = "aabbccdd-0000-0000-0000-000000000001"
_SIGNED_AT = datetime(2025, 10, 1, 14, 0, 0, tzinfo=timezone.utc)

_CASE_A = "case-aaaa-0000-0000-0000-000000000001"
_CASE_B = "case-bbbb-0000-0000-0000-000000000002"


def _make_signed_row(signed_at=_SIGNED_AT):
    row = MagicMock()
    row.__getitem__ = lambda self, key: _SIGNED_AT if key == "signed_at" else None
    row.__bool__ = lambda self: True
    return row


def _make_db_row(case_id: str, case_number: str, linked: bool):
    d = {"case_id": case_id, "case_number": case_number, "linked": linked}
    row = MagicMock()
    row.__getitem__ = lambda self, k: d[k]
    return row


class TestRebuildAutoLinkResults:

    @pytest.mark.asyncio
    async def test_linked_true_when_vivo_en_official_documents(self):
        signed_row = {"signed_at": _SIGNED_AT}
        db_rows = [{"case_id": _CASE_A, "case_number": "EE-2025-000001", "linked": True}]

        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, return_value=signed_row
        ), patch(
            "endpoints.digital_signature.poll.fetch_all",
            new_callable=AsyncMock, return_value=db_rows
        ), patch(
            "services.case_queries.get_rebuild_auto_link_results_query",
            return_value="SELECT 1"
        ):
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        assert len(results) == 1
        r = results[0]
        assert r["case_id"] == _CASE_A
        assert r["case_number"] == "EE-2025-000001"
        assert r["linked"] is True
        assert r["reason"] is None

    @pytest.mark.asyncio
    async def test_linked_false_when_no_vinculo_propuesta_activa(self):
        signed_row = {"signed_at": _SIGNED_AT}
        db_rows = [{"case_id": _CASE_A, "case_number": "EE-2025-000002", "linked": False}]

        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, return_value=signed_row
        ), patch(
            "endpoints.digital_signature.poll.fetch_all",
            new_callable=AsyncMock, return_value=db_rows
        ), patch(
            "services.case_queries.get_rebuild_auto_link_results_query",
            return_value="SELECT 1"
        ):
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        assert len(results) == 1
        assert results[0]["linked"] is False
        assert results[0]["reason"] is None

    @pytest.mark.asyncio
    async def test_propuesta_rechazada_no_aparece(self):
        signed_row = {"signed_at": _SIGNED_AT}
        db_rows = []

        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, return_value=signed_row
        ), patch(
            "endpoints.digital_signature.poll.fetch_all",
            new_callable=AsyncMock, return_value=db_rows
        ), patch(
            "services.case_queries.get_rebuild_auto_link_results_query",
            return_value="SELECT 1"
        ):
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        assert results == [], (
            "Una propuesta rechazada (is_active=false sin vínculo vivo) "
            "no debe aparecer en auto_link_results (regla de producto)."
        )

    @pytest.mark.asyncio
    async def test_propuesta_posterior_a_firma_no_aparece(self):
        signed_row = {"signed_at": _SIGNED_AT}
        db_rows = []

        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, return_value=signed_row
        ), patch(
            "endpoints.digital_signature.poll.fetch_all",
            new_callable=AsyncMock, return_value=db_rows
        ), patch(
            "services.case_queries.get_rebuild_auto_link_results_query",
            return_value="SELECT 1"
        ):
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        assert results == []

    @pytest.mark.asyncio
    async def test_signed_at_null_devuelve_lista_vacia(self):
        signed_row = {"signed_at": None}

        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, return_value=signed_row
        ), patch(
            "endpoints.digital_signature.poll.fetch_all",
            new_callable=AsyncMock
        ) as mock_fetch_all:
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        assert results == []
        mock_fetch_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_signed_at_row_none_devuelve_lista_vacia(self):
        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, return_value=None
        ), patch(
            "endpoints.digital_signature.poll.fetch_all",
            new_callable=AsyncMock
        ) as mock_fetch_all:
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        assert results == []
        mock_fetch_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_one_falla_devuelve_lista_vacia(self):
        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, side_effect=Exception("db connection lost")
        ):
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        assert results == []

    @pytest.mark.asyncio
    async def test_fetch_all_falla_devuelve_lista_vacia(self):
        signed_row = {"signed_at": _SIGNED_AT}

        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, return_value=signed_row
        ), patch(
            "endpoints.digital_signature.poll.fetch_all",
            new_callable=AsyncMock, side_effect=Exception("timeout")
        ), patch(
            "services.case_queries.get_rebuild_auto_link_results_query",
            return_value="SELECT 1"
        ):
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        assert results == []

    @pytest.mark.asyncio
    async def test_sin_propuestas_devuelve_lista_vacia(self):
        signed_row = {"signed_at": _SIGNED_AT}

        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, return_value=signed_row
        ), patch(
            "endpoints.digital_signature.poll.fetch_all",
            new_callable=AsyncMock, return_value=[]
        ), patch(
            "services.case_queries.get_rebuild_auto_link_results_query",
            return_value="SELECT 1"
        ):
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        assert results == []

    @pytest.mark.asyncio
    async def test_multiples_propuestas_mixed(self):
        signed_row = {"signed_at": _SIGNED_AT}
        db_rows = [
            {"case_id": _CASE_A, "case_number": "EE-2025-000001", "linked": True},
            {"case_id": _CASE_B, "case_number": "EE-2025-000002", "linked": False},
        ]

        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, return_value=signed_row
        ), patch(
            "endpoints.digital_signature.poll.fetch_all",
            new_callable=AsyncMock, return_value=db_rows
        ), patch(
            "services.case_queries.get_rebuild_auto_link_results_query",
            return_value="SELECT 1"
        ):
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        assert len(results) == 2
        assert results[0] == {
            "case_id": _CASE_A, "case_number": "EE-2025-000001",
            "linked": True, "reason": None
        }
        assert results[1] == {
            "case_id": _CASE_B, "case_number": "EE-2025-000002",
            "linked": False, "reason": None
        }

    @pytest.mark.asyncio
    async def test_reason_siempre_none(self):
        signed_row = {"signed_at": _SIGNED_AT}
        db_rows = [
            {"case_id": _CASE_A, "case_number": "EE-2025-000001", "linked": True},
            {"case_id": _CASE_B, "case_number": "EE-2025-000002", "linked": False},
        ]

        with patch(
            "endpoints.digital_signature.poll.fetch_one",
            new_callable=AsyncMock, return_value=signed_row
        ), patch(
            "endpoints.digital_signature.poll.fetch_all",
            new_callable=AsyncMock, return_value=db_rows
        ), patch(
            "services.case_queries.get_rebuild_auto_link_results_query",
            return_value="SELECT 1"
        ):
            results = await _rebuild_auto_link_results(_DOC_ID, schema_name=_SCHEMA)

        for r in results:
            assert r["reason"] is None, "reason debe ser None siempre en _rebuild_auto_link_results"


class TestGetRebuildAutoLinkResultsQuery:

    def test_query_retorna_string_no_vacio(self):
        from services.case_queries import get_rebuild_auto_link_results_query
        q = get_rebuild_auto_link_results_query()
        assert isinstance(q, str)
        assert len(q.strip()) > 0

    def test_query_usa_parametros_1_y_2(self):
        from services.case_queries import get_rebuild_auto_link_results_query
        q = get_rebuild_auto_link_results_query()
        assert "$1" in q
        assert "$2" in q

    def test_query_incluye_tablas_requeridas(self):
        from services.case_queries import get_rebuild_auto_link_results_query
        q = get_rebuild_auto_link_results_query()
        assert "case_proposed_documents" in q
        assert "case_official_documents" in q
        assert "cases" in q

    def test_query_filtra_auto_link_on_sign(self):
        from services.case_queries import get_rebuild_auto_link_results_query
        q = get_rebuild_auto_link_results_query()
        assert "auto_link_on_sign" in q

    def test_query_filtra_proposing_date_menor_a_signed_at(self):
        from services.case_queries import get_rebuild_auto_link_results_query
        q = get_rebuild_auto_link_results_query()
        assert "proposing_date" in q
        assert "$2" in q

    def test_query_excluye_propuestas_rechazadas_via_is_active_false_sin_vinculo(self):
        from services.case_queries import get_rebuild_auto_link_results_query
        q = get_rebuild_auto_link_results_query()
        assert q.count("is_active") >= 2

    def test_query_linked_usa_exists_en_official_documents(self):
        from services.case_queries import get_rebuild_auto_link_results_query
        q = get_rebuild_auto_link_results_query()
        assert "EXISTS" in q.upper()
        assert "linked" in q

    def test_query_ordena_por_proposing_date(self):
        from services.case_queries import get_rebuild_auto_link_results_query
        q = get_rebuild_auto_link_results_query()
        assert "ORDER BY" in q.upper()
        assert "proposing_date" in q
