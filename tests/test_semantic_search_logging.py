import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


MOCK_EMBEDDING = [0.1] * 768

MOCK_EMBEDDING_RESULT_OK = {
    "embedding": MOCK_EMBEDDING,
    "rewritten_text": "query reescrita por AgenteLANG",
}

MOCK_EMBEDDING_RESULT_EMPTY = {
    "embedding": None,
    "rewritten_text": None,
}

MOCK_ROWS = [
    {
        "document_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "official_number": "IF-2025-0000001-TEST",
        "document_type": "Informe",
        "reference": "Referencia del documento",
        "short_resume": "Resumen corto",
        "resume": "Resumen largo",
        "similarity": 0.9123,
        "rrf_score": 0.030,
        "chunk_text": "Texto del chunk",
        "linked_cases": [],
        "linked_records": [],
    },
    {
        "document_id": "bbbbbbbb-0000-0000-0000-000000000002",
        "official_number": "DICT-2025-0000002-TEST",
        "document_type": "Dictamen",
        "reference": "Referencia 2",
        "short_resume": "Resumen 2",
        "resume": "Resumen largo 2",
        "similarity": 0.7654,
        "rrf_score": 0.025,
        "chunk_text": "Texto del chunk 2",
        "linked_cases": None,
        "linked_records": None,
    },
]


async def _run_semantic_search(source: str = "api", rows=None, embedding_result=None):
    if embedding_result is None:
        embedding_result = MOCK_EMBEDDING_RESULT_OK
    if rows is None:
        rows = MOCK_ROWS

    with patch("services.search.semantic_search.get_embedding", new_callable=AsyncMock, return_value=embedding_result), \
         patch("services.search.semantic_search.fetch_all", new_callable=AsyncMock, return_value=rows), \
         patch("services.search.semantic_search.execute", new_callable=AsyncMock) as mock_execute:

        from services.search.semantic_search import semantic_search
        result = await semantic_search(
            "consulta de prueba",
            "user-uuid-001",
            schema_name="100_test",
            limit=20,
            source=source,
        )
        await asyncio.sleep(0)
        return result, mock_execute


class TestSemanticSearchLogging:

    @pytest.mark.asyncio
    async def test_log_failure_does_not_break_response(self):
        with patch("services.search.semantic_search.get_embedding", new_callable=AsyncMock, return_value=MOCK_EMBEDDING_RESULT_OK), \
             patch("services.search.semantic_search.fetch_all", new_callable=AsyncMock, return_value=MOCK_ROWS), \
             patch("services.search.semantic_search.execute", new_callable=AsyncMock, side_effect=Exception("BD unavailable")):

            from services.search.semantic_search import semantic_search
            result = await semantic_search(
                "consulta de prueba",
                "user-uuid-001",
                schema_name="100_test",
                limit=20,
                source="api",
            )
            await asyncio.sleep(0)

        assert result["success"] is True
        assert len(result["results"]) == 2
        assert result["total"] == 2
        assert result["query"] == "consulta de prueba"
        assert result["rewritten_query"] == "query reescrita por AgenteLANG"

    @pytest.mark.asyncio
    async def test_log_called_with_correct_fields(self):
        with patch("services.search.semantic_search.get_embedding", new_callable=AsyncMock, return_value=MOCK_EMBEDDING_RESULT_OK), \
             patch("services.search.semantic_search.fetch_all", new_callable=AsyncMock, return_value=MOCK_ROWS), \
             patch("services.search.semantic_search.execute", new_callable=AsyncMock) as mock_execute:

            from services.search.semantic_search import semantic_search
            result = await semantic_search(
                "consulta de prueba",
                "user-uuid-001",
                schema_name="100_test",
                limit=20,
                source="api",
            )
            await asyncio.sleep(0)

        assert mock_execute.called, "execute debería haberse llamado para el log"

        args, kwargs = mock_execute.call_args
        sql = args[0]

        assert "rag_query_log" in sql
        assert "INSERT INTO public.rag_query_log" in sql

        assert kwargs.get("schema_name") == "public", \
            f"El log debe ir a public, no a schema del tenant. Got: {kwargs.get('schema_name')}"

        (
            _sql,
            schema_name_val, user_id_val, source_val, intent_val, query_val, rewritten_val,
            candidates_val, final_val, top_sim, bottom_sim, threshold_val,
            latency_val, doc_ids_val
        ) = args

        assert schema_name_val == "100_test"
        assert user_id_val == "user-uuid-001"
        assert source_val == "api"
        assert intent_val == "rag"
        assert query_val == "consulta de prueba"
        assert rewritten_val == "query reescrita por AgenteLANG"
        assert candidates_val == 2
        assert final_val == 2
        assert top_sim == round(0.9123, 4)
        assert bottom_sim == round(0.7654, 4)
        assert threshold_val == 0.30
        assert isinstance(latency_val, int) and latency_val >= 0
        assert "aaaaaaaa-0000-0000-0000-000000000001" in doc_ids_val
        assert "bbbbbbbb-0000-0000-0000-000000000002" in doc_ids_val

    @pytest.mark.asyncio
    async def test_log_called_on_empty_embedding(self):
        with patch("services.search.semantic_search.get_embedding", new_callable=AsyncMock, return_value=MOCK_EMBEDDING_RESULT_EMPTY), \
             patch("services.search.semantic_search.fetch_all", new_callable=AsyncMock) as mock_fetch, \
             patch("services.search.semantic_search.execute", new_callable=AsyncMock) as mock_execute:

            from services.search.semantic_search import semantic_search
            result = await semantic_search(
                "consulta vacía",
                "user-uuid-002",
                schema_name="100_test",
                limit=20,
                source="api",
            )
            await asyncio.sleep(0)

        mock_fetch.assert_not_called()

        assert mock_execute.called

        args, kwargs = mock_execute.call_args
        candidates_val = args[7]
        final_val = args[8]

        assert candidates_val == 0
        assert final_val == 0
        assert kwargs.get("schema_name") == "public"

        assert result["success"] is True
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_source_mcp_passed_correctly(self):
        with patch("services.search.semantic_search.get_embedding", new_callable=AsyncMock, return_value=MOCK_EMBEDDING_RESULT_OK), \
             patch("services.search.semantic_search.fetch_all", new_callable=AsyncMock, return_value=[]), \
             patch("services.search.semantic_search.execute", new_callable=AsyncMock) as mock_execute:

            from services.search.semantic_search import semantic_search
            await semantic_search(
                "query mcp",
                "user-uuid-003",
                schema_name="100_test",
                limit=20,
                source="mcp",
            )
            await asyncio.sleep(0)

        assert mock_execute.called
        args, _ = mock_execute.call_args
        source_val = args[3]
        assert source_val == "mcp"

    @pytest.mark.asyncio
    async def test_result_fields_are_correct(self):
        result, _ = await _run_semantic_search(source="api")

        assert result["success"] is True
        assert result["total"] == 2
        assert result["intent"] == "rag"
        assert result["results"][0]["similarity"] == round(0.9123, 4)
        assert result["results"][0]["rrf_score"] == round(0.030, 6)
        assert result["results"][1]["linked_cases"] == []
        assert result["results"][1]["linked_records"] == []

    @pytest.mark.asyncio
    async def test_semantic_search_tool_default_source_mcp(self):
        mock_ctx = MagicMock()
        mock_ctx.user_id = "user-uuid-tool-001"
        mock_ctx.schema_name = "100_test"

        with patch("services.search.semantic_search.semantic_search", new_callable=AsyncMock) as mock_ss:
            mock_ss.return_value = {"success": True, "results": [], "total": 0}
            from api_gateway.tools.search import semantic_search_tool
            await semantic_search_tool(mock_ctx, query="test query")

        mock_ss.assert_called_once()
        _, kwargs = mock_ss.call_args
        assert kwargs.get("source") == "mcp", \
            f"Default source debe ser 'mcp', got: {kwargs.get('source')}"

    @pytest.mark.asyncio
    async def test_semantic_search_tool_explicit_source_api(self):
        mock_ctx = MagicMock()
        mock_ctx.user_id = "user-uuid-tool-002"
        mock_ctx.schema_name = "100_test"

        with patch("services.search.semantic_search.semantic_search", new_callable=AsyncMock) as mock_ss:
            mock_ss.return_value = {"success": True, "results": [], "total": 0}
            from api_gateway.tools.search import semantic_search_tool
            await semantic_search_tool(mock_ctx, query="test query api", source="api")

        mock_ss.assert_called_once()
        _, kwargs = mock_ss.call_args
        assert kwargs.get("source") == "api", \
            f"Source explicito 'api' debe propagarse, got: {kwargs.get('source')}"

    def test_classify_intent_lookup(self):
        from services.search.semantic_search import classify_intent
        assert classify_intent("Ordenanza 6057") == "lookup"
        assert classify_intent("ordenanza hcd 14918") == "lookup"
        assert classify_intent("Resolución 14918") == "lookup"
        assert classify_intent("decreto 3926") == "lookup"
        assert classify_intent("PLORD-2026-00001523-ESCO-DIGE") == "lookup"

    def test_classify_intent_rag(self):
        from services.search.semantic_search import classify_intent
        assert classify_intent("donaciones municipales") == "rag"
        assert classify_intent("habilitaciones comerciales") == "rag"
        assert classify_intent("qué dice la ordenanza 6057 sobre habilitaciones") == "rag"
        assert classify_intent("que trata la resolución 14918") == "rag"
