"""
Tests unitarios para el logging fire-and-forget de semantic_search.

Verifica:
1. Si el INSERT falla, semantic_search NO rompe la response al cliente.
2. Si todo va bien, _log_query se llama con los campos correctos.
3. source="mcp" llega correctamente desde el MCP tool.

Estos tests NO requieren BD ni AgenteLANG; mockean todas las dependencias externas.
"""
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ---------------------------------------------------------------------------
# Fixtures de embedding mock
# ---------------------------------------------------------------------------

MOCK_EMBEDDING = [0.1] * 1536  # Dimensión típica de embeddings

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


# ---------------------------------------------------------------------------
# Helper async
# ---------------------------------------------------------------------------

async def _run_semantic_search(source: str = "api", rows=None, embedding_result=None):
    """Ejecuta semantic_search con todos los deps mockeados."""
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
        # Yield al event loop para que los background tasks se completen
        await asyncio.sleep(0)
        return result, mock_execute


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSemanticSearchLogging:

    @pytest.mark.asyncio
    async def test_log_failure_does_not_break_response(self):
        """Si execute lanza Exception, semantic_search igual devuelve resultados."""
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

        # La respuesta al cliente debe ser correcta a pesar del error de log
        assert result["success"] is True
        assert len(result["results"]) == 2
        assert result["total"] == 2
        assert result["query"] == "consulta de prueba"
        assert result["rewritten_query"] == "query reescrita por AgenteLANG"

    @pytest.mark.asyncio
    async def test_log_called_with_correct_fields(self):
        """_log_query se llama con los campos correctos cuando todo va bien."""
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

        # Verificar que execute fue llamado (para el log)
        assert mock_execute.called, "execute debería haberse llamado para el log"

        # asyncpg: execute(sql, p1, p2, ..., schema_name=...)
        args, kwargs = mock_execute.call_args
        sql = args[0]

        assert "rag_query_log" in sql
        assert "INSERT INTO public.rag_query_log" in sql

        # Verificar schema_name="public" en el kwarg
        assert kwargs.get("schema_name") == "public", \
            f"El log debe ir a public, no a schema del tenant. Got: {kwargs.get('schema_name')}"

        # Params positionales: (sql, schema_name, user_id, source, intent, query, rewritten_query,
        #                        candidates, final, top_sim, bottom_sim, threshold, latency, doc_ids)
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
        """Cuando embedding está vacío (early return), también se loggea con candidates=0, final=0."""
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

        # fetch_all NO debe haberse llamado (early return antes de la query)
        mock_fetch.assert_not_called()

        # Pero el log SÍ debe haberse llamado
        assert mock_execute.called

        args, kwargs = mock_execute.call_args
        # args: (sql, schema_name, user_id, source, intent, query, rewritten, candidates, final, ...)
        candidates_val = args[7]
        final_val = args[8]

        assert candidates_val == 0
        assert final_val == 0
        assert kwargs.get("schema_name") == "public"

        # Response al cliente también debe ser la correcta
        assert result["success"] is True
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_source_mcp_passed_correctly(self):
        """source='mcp' se propaga correctamente al log."""
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
        # args[3] = source (after sql, schema_name, user_id)
        source_val = args[3]
        assert source_val == "mcp"

    @pytest.mark.asyncio
    async def test_result_fields_are_correct(self):
        """Verifica que la response al cliente no se vea alterada por el logging."""
        result, _ = await _run_semantic_search(source="api")

        assert result["success"] is True
        assert result["total"] == 2
        assert result["intent"] == "rag"
        assert result["results"][0]["similarity"] == round(0.9123, 4)
        assert result["results"][0]["rrf_score"] == round(0.030, 6)
        assert result["results"][1]["linked_cases"] == []   # None → []
        assert result["results"][1]["linked_records"] == []

    @pytest.mark.asyncio
    async def test_semantic_search_tool_default_source_mcp(self):
        """semantic_search_tool sin source explicito debe pasar source='mcp' al service."""
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
        """semantic_search_tool con source='api' debe pasar source='api' al service."""
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
        """classify_intent detecta correctamente queries de tipo lookup."""
        from services.search.semantic_search import classify_intent
        assert classify_intent("Ordenanza 6057") == "lookup"
        assert classify_intent("ordenanza hcd 14918") == "lookup"
        assert classify_intent("Resolución 14918") == "lookup"
        assert classify_intent("decreto 3926") == "lookup"
        assert classify_intent("PLORD-2026-00001523-ESCO-DIGE") == "lookup"

    def test_classify_intent_rag(self):
        """classify_intent enruta a RAG queries semánticas aunque tengan número."""
        from services.search.semantic_search import classify_intent
        assert classify_intent("donaciones municipales") == "rag"
        assert classify_intent("habilitaciones comerciales") == "rag"
        assert classify_intent("qué dice la ordenanza 6057 sobre habilitaciones") == "rag"
        assert classify_intent("que trata la resolución 14918") == "rag"
