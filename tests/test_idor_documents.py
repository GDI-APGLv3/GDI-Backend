
import pytest
from unittest.mock import patch, AsyncMock


DOC_ID = "dddddddd-0000-0000-0000-000000000001"
USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
CASE_ID = "cccccccc-0000-0000-0000-000000000001"
SCHEMA = "100_test"


class TestCanUserViewDocumentViaCase:

    @pytest.mark.asyncio
    async def test_returns_true_via_case_when_direct_access_false(self):
        from services.documents.permissions import can_user_view_document

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv, \
             patch("services.documents.permissions.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:

            mock_fv.return_value = False
            mock_fa.return_value = [{"case_id": CASE_ID}]
            mock_case.return_value = True

            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is True, (
            "Debe retornar True: usuario sin acceso directo pero con "
            "acceso al expediente vinculado."
        )

    @pytest.mark.asyncio
    async def test_returns_false_when_no_case_access(self):
        from services.documents.permissions import can_user_view_document

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv, \
             patch("services.documents.permissions.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:

            mock_fv.return_value = False
            mock_fa.return_value = [{"case_id": CASE_ID}]
            mock_case.return_value = False

            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_doc_not_linked_to_any_case(self):
        from services.documents.permissions import can_user_view_document

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv, \
             patch("services.documents.permissions.fetch_all", new_callable=AsyncMock) as mock_fa:

            mock_fv.return_value = False
            mock_fa.return_value = []

            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is False

    @pytest.mark.asyncio
    async def test_direct_access_shortcircuits_case_lookup(self):
        from services.documents.permissions import can_user_view_document

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv, \
             patch("services.documents.permissions.fetch_all", new_callable=AsyncMock) as mock_fa:

            mock_fv.return_value = True
            mock_fa.return_value = []

            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is True
        mock_fa.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_cases_first_accessible_short_circuits(self):
        from services.documents.permissions import can_user_view_document

        CASE_ID_2 = "cccccccc-0000-0000-0000-000000000002"

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv, \
             patch("services.documents.permissions.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:

            mock_fv.return_value = False
            mock_fa.return_value = [{"case_id": CASE_ID}, {"case_id": CASE_ID_2}]
            mock_case.side_effect = [True, False]

            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is True
        assert mock_case.call_count == 1

    @pytest.mark.asyncio
    async def test_query_includes_via_record_criterion(self):
        from services.documents.permissions import can_user_view_document

        captured = {}

        async def capture(sql, *params, schema_name):
            captured["sql"] = sql
            return True

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv, \
             patch("services.documents.permissions.fetch_all", new_callable=AsyncMock) as mock_fa:
            mock_fv.side_effect = capture
            mock_fa.return_value = []

            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is True
        sql = captured["sql"]
        assert "record_document_links" in sql, "Falta el criterio via legajo en el query"
        assert "registry_family_permissions" in sql
        assert "rfp.can_view = true" in sql
        assert "official_documents od_link" in sql
        assert "od_link.signed_at IS NOT NULL" in sql
        assert "s.is_active = true" in sql
        assert "usp.can_view = true" in sql
        assert "rf.is_active = true" in sql, \
            "Caso 6 debe filtrar por registry_families.is_active = true"
        assert "r.state = 'Activo'" in sql, \
            "Caso 6 debe filtrar por records.state = 'Activo' (excluye archivados/inactivos)"


class TestUnifiedDetailsSignedIDOR:

    @pytest.mark.asyncio
    async def test_signed_doc_without_permission_raises_authorization_error(self):
        from services.documents.retrieval.unified_details import get_unified_document_details
        from shared.exceptions import AuthorizationError

        with patch("services.documents.retrieval.unified_details._get_document_status",
                   new_callable=AsyncMock) as mock_status, \
             patch("services.documents.permissions.can_user_view_document",
                   new_callable=AsyncMock) as mock_perm:

            mock_status.return_value = "signed"
            mock_perm.return_value = False

            with pytest.raises(AuthorizationError):
                await get_unified_document_details(DOC_ID, USER_ID, schema_name=SCHEMA)

    @pytest.mark.asyncio
    async def test_signed_doc_with_permission_proceeds_to_details(self):
        from services.documents.retrieval.unified_details import get_unified_document_details

        mock_details = {"signer_info": "ok", "official_number": "IF-2025-0000001"}

        with patch("services.documents.retrieval.unified_details._get_document_status",
                   new_callable=AsyncMock) as mock_status, \
             patch("services.documents.permissions.can_user_view_document",
                   new_callable=AsyncMock) as mock_perm, \
             patch("services.documents.retrieval.unified_details.build_signature_details_response",
                   new_callable=AsyncMock) as mock_builder:

            mock_status.return_value = "signed"
            mock_perm.return_value = True
            mock_builder.return_value = mock_details

            result = await get_unified_document_details(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result["status"] == "signed"
        assert result["details"] == mock_details

    @pytest.mark.asyncio
    async def test_sent_to_sign_doc_without_permission_raises_authorization_error(self):
        from services.documents.retrieval.unified_details import get_unified_document_details
        from shared.exceptions import AuthorizationError

        with patch("services.documents.retrieval.unified_details._get_document_status",
                   new_callable=AsyncMock) as mock_status, \
             patch("services.documents.permissions.can_user_view_document",
                   new_callable=AsyncMock) as mock_perm:

            mock_status.return_value = "sent_to_sign"
            mock_perm.return_value = False

            with pytest.raises(AuthorizationError):
                await get_unified_document_details(DOC_ID, USER_ID, schema_name=SCHEMA)

    @pytest.mark.asyncio
    async def test_editing_draft_without_permission_denied(self):
        from services.documents.retrieval.unified_details import get_unified_document_details
        from shared.exceptions import AuthorizationError

        with patch("services.documents.retrieval.unified_details._get_document_status",
                   new_callable=AsyncMock) as mock_status, \
             patch("services.documents.permissions.can_user_view_document",
                   new_callable=AsyncMock) as mock_perm, \
             patch("services.documents.retrieval.unified_details.get_document_details_for_editing",
                   new_callable=AsyncMock) as mock_edit:

            mock_status.return_value = "draft"
            mock_perm.return_value = False

            with pytest.raises(AuthorizationError):
                await get_unified_document_details(DOC_ID, USER_ID, schema_name=SCHEMA)

        mock_edit.assert_not_called()

    @pytest.mark.asyncio
    async def test_editing_rejected_without_permission_denied(self):
        from services.documents.retrieval.unified_details import get_unified_document_details
        from shared.exceptions import AuthorizationError

        with patch("services.documents.retrieval.unified_details._get_document_status",
                   new_callable=AsyncMock) as mock_status, \
             patch("services.documents.permissions.can_user_view_document",
                   new_callable=AsyncMock) as mock_perm, \
             patch("services.documents.retrieval.unified_details.get_document_details_for_editing",
                   new_callable=AsyncMock) as mock_edit:

            mock_status.return_value = "rejected"
            mock_perm.return_value = False

            with pytest.raises(AuthorizationError):
                await get_unified_document_details(DOC_ID, USER_ID, schema_name=SCHEMA)

        mock_edit.assert_not_called()

    @pytest.mark.asyncio
    async def test_editing_draft_with_permission_proceeds(self):
        from services.documents.retrieval.unified_details import get_unified_document_details

        mock_details = {"content": "borrador"}

        with patch("services.documents.retrieval.unified_details._get_document_status",
                   new_callable=AsyncMock) as mock_status, \
             patch("services.documents.permissions.can_user_view_document",
                   new_callable=AsyncMock) as mock_perm, \
             patch("services.documents.retrieval.unified_details.get_document_details_for_editing",
                   new_callable=AsyncMock) as mock_edit:

            mock_status.return_value = "draft"
            mock_perm.return_value = True
            mock_edit.return_value = mock_details

            result = await get_unified_document_details(DOC_ID, USER_ID, schema_name=SCHEMA)

        mock_perm.assert_called_once()
        assert result["status"] == "draft"
        assert result["details"] == mock_details

    @pytest.mark.asyncio
    async def test_editing_draft_requires_user_id(self):
        from services.documents.retrieval.unified_details import get_unified_document_details
        from shared.exceptions import ValidationError

        with patch("services.documents.retrieval.unified_details._get_document_status",
                   new_callable=AsyncMock) as mock_status:
            mock_status.return_value = "draft"

            with pytest.raises(ValidationError):
                await get_unified_document_details(DOC_ID, user_id=None, schema_name=SCHEMA)


class TestMisDocumentosNoIncludesCaseDocuments:

    def test_get_user_documents_query_has_no_case_official_documents(self):
        import inspect
        from services import document_service

        source = inspect.getsource(document_service.get_user_documents)

        assert "cm.assigned_sector_id" not in source, (
            "get_user_documents NO debe filtrar inclusion de documentos "
            "por sector asignado de movimientos de expediente. "
            "Ese patron es de semantic search / can_user_view_document."
        )

    def test_semantic_search_query_includes_case_access_conditions(self):
        from services.search.queries import SEMANTIC_SEARCH_SQL

        assert "cm.assigned_sector_id" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL debe incluir via expediente por sector asignado (caso 3a)"
        assert "cm.admin_sector_id" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL debe incluir via expediente por admin-transfer (caso 3b)"
        assert "cm.type = 'creation'" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL debe incluir via expediente por admin-creation (caso 3c)"
        assert "c.created_by_user_id" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL debe incluir via expediente por creador del expediente (caso 2)"
        assert "can_global_search_cases" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL debe incluir via expediente por can_global_search_cases (caso 1)"

    def test_lookup_search_query_includes_case_access_conditions(self):
        from services.search.queries import LOOKUP_DOCUMENT_SQL

        assert "cm.assigned_sector_id" in LOOKUP_DOCUMENT_SQL
        assert "cm.admin_sector_id" in LOOKUP_DOCUMENT_SQL
        assert "cm.type = 'creation'" in LOOKUP_DOCUMENT_SQL
        assert "c.created_by_user_id" in LOOKUP_DOCUMENT_SQL
        assert "can_global_search_cases" in LOOKUP_DOCUMENT_SQL

    def test_semantic_search_via_legajo_filters_active_family_and_record(self):
        from services.search.queries import SEMANTIC_SEARCH_SQL

        assert "rf.is_active = true" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL via-legajo debe filtrar rf.is_active = true"
        assert "r.state = 'Activo'" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL via-legajo debe filtrar r.state = 'Activo'"

    def test_lookup_search_via_legajo_filters_active_family_and_record(self):
        from services.search.queries import LOOKUP_DOCUMENT_SQL

        assert "rf.is_active = true" in LOOKUP_DOCUMENT_SQL, \
            "LOOKUP_DOCUMENT_SQL via-legajo debe filtrar rf.is_active = true"
        assert "r.state = 'Activo'" in LOOKUP_DOCUMENT_SQL, \
            "LOOKUP_DOCUMENT_SQL via-legajo debe filtrar r.state = 'Activo'"

    def test_mis_documentos_query_does_not_have_case_inclusion_via_global_search(self):
        import inspect
        from services import document_service

        source = inspect.getsource(document_service.get_user_documents)

        assert "can_global_search_cases" not in source, (
            "get_user_documents no debe usar can_global_search_cases: "
            "ese flag es de expedientes, no del listado de 'Mis Documentos'."
        )


class TestSemanticSearchCaseAccess:

    def test_hybrid_bm25_rrf_structure_intact(self):
        from services.search.queries import SEMANTIC_SEARCH_SQL

        required_ctes = [
            "vector_cands",
            "bm25_cands",
            "best_vector",
            "ranked_vector",
            "best_bm25",
            "ranked_bm25",
            "fused",
            "permitted",
        ]
        for cte in required_ctes:
            assert cte in SEMANTIC_SEARCH_SQL, \
                f"CTE '{cte}' del hibrido BM25+RRF no encontrado en SEMANTIC_SEARCH_SQL"

    def test_rrf_formula_intact(self):
        from services.search.queries import SEMANTIC_SEARCH_SQL

        assert "1.0 / (60.0 + rv.vec_rank)" in SEMANTIC_SEARCH_SQL, \
            "Formula RRF de vector no encontrada"
        assert "1.0 / (60.0 + rb.bm25_rank)" in SEMANTIC_SEARCH_SQL, \
            "Formula RRF de BM25 no encontrada"

    @pytest.mark.asyncio
    async def test_semantic_search_passes_user_id_to_sql(self):
        from services.search.semantic_search import semantic_search

        captured_params = {}

        async def mock_fetch_all(sql, *params, schema_name):
            captured_params["params"] = params
            captured_params["schema_name"] = schema_name
            return []

        mock_embedding_result = {
            "embedding": [0.1] * 768,
            "rewritten_text": "query test",
        }

        with patch("services.search.semantic_search.fetch_all",
                   side_effect=mock_fetch_all), \
             patch("services.search.semantic_search.get_embedding",
                   new_callable=AsyncMock,
                   return_value=mock_embedding_result), \
             patch("services.search.semantic_search._fire_and_forget_log"):

            await semantic_search(
                "informe de obras",
                USER_ID,
                schema_name=SCHEMA,
                limit=5,
            )

        assert captured_params.get("params") is not None, \
            "fetch_all no fue llamado"
        assert captured_params["params"][0] == USER_ID, \
            "El primer parametro de SEMANTIC_SEARCH_SQL debe ser user_id"
        assert captured_params["schema_name"] == SCHEMA

    @pytest.mark.asyncio
    async def test_lookup_search_passes_user_id_to_sql(self):
        from services.search.semantic_search import semantic_search

        captured_params = {}

        async def mock_fetch_all(sql, *params, schema_name):
            captured_params["params"] = params
            return []

        with patch("services.search.semantic_search.fetch_all",
                   side_effect=mock_fetch_all), \
             patch("services.search.semantic_search._fire_and_forget_log"):

            await semantic_search(
                "Ordenanza 6057",
                USER_ID,
                schema_name=SCHEMA,
                limit=5,
            )

        assert captured_params.get("params") is not None
        assert captured_params["params"][0] == USER_ID, \
            "El primer parametro de LOOKUP_DOCUMENT_SQL debe ser user_id"


class TestCloudflareUrlExpirationConstant:

    def test_expiration_default_is_1_minuto(self):
        import os
        original = os.environ.pop("CF_R2_SIGN_EXPIRATION", None)
        try:
            import importlib
            import config.constants as const_module
            importlib.reload(const_module)
            assert const_module.CLOUDFLARE_URL_EXPIRATION_SECONDS == 60
            assert const_module.CLOUDFLARE_URL_EXPIRATION == "1 minuto"
        finally:
            if original is not None:
                os.environ["CF_R2_SIGN_EXPIRATION"] = original
            importlib.reload(const_module)

    def test_expiration_text_is_not_15_minutes(self):
        from config import constants
        assert constants.CLOUDFLARE_URL_EXPIRATION != "15 minutes", (
            "El texto '15 minutes' es incorrecto: la expiracion real es "
            "60s = 1 minuto por default (GDI-229)."
        )
