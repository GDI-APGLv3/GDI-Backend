"""
Tests de seguridad para los IDOR de documentos (tickets SU-003/004/005).

Escenarios cubiertos:
  (a) Usuario con acceso a expediente puede ver/abrir un doc vinculado
      aunque el doc no sea de su sector ni lo haya creado.
  (b) GET /documents/{id}/details de doc signed sin acceso → 403.
  (c) "Mis Documentos" (get_user_documents) NO incluye docs-por-expediente.
  (d) Semantic search SÍ encuentra docs accesibles por expediente (presencia
      de la condicion en SEMANTIC_SEARCH_SQL).

Estos tests NO requieren conexion a BD real: mockean todas las
funciones asyncpg de database.py. Pueden correr sin tunnel.
"""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# ===========================================================================
# Constantes de prueba
# ===========================================================================

DOC_ID = "dddddddd-0000-0000-0000-000000000001"
USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
CASE_ID = "cccccccc-0000-0000-0000-000000000001"
SCHEMA = "100_test"


# ===========================================================================
# (a) can_user_view_document — via expediente
# ===========================================================================

class TestCanUserViewDocumentViaCase:
    """
    Verifica que can_user_view_document devuelva True cuando el usuario
    no tiene acceso directo (sector/creador/firmante) pero SI puede ver
    el expediente al que el documento esta vinculado.
    """

    @pytest.mark.asyncio
    async def test_returns_true_via_case_when_direct_access_false(self):
        """
        Usuario sin acceso directo al doc, pero puede ver el expediente
        vinculado → should return True.
        """
        from services.documents.permissions import can_user_view_document

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv, \
             patch("services.documents.permissions.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:

            # Sin acceso directo
            mock_fv.return_value = False
            # El doc esta vinculado al expediente CASE_ID
            mock_fa.return_value = [{"case_id": CASE_ID}]
            # El usuario puede ver ese expediente
            mock_case.return_value = True

            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is True, (
            "Debe retornar True: usuario sin acceso directo pero con "
            "acceso al expediente vinculado."
        )

    @pytest.mark.asyncio
    async def test_returns_false_when_no_case_access(self):
        """
        Usuario sin acceso directo Y sin acceso al expediente → False.
        """
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
        """
        Usuario sin acceso directo Y doc no vinculado a expediente → False.
        """
        from services.documents.permissions import can_user_view_document

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv, \
             patch("services.documents.permissions.fetch_all", new_callable=AsyncMock) as mock_fa:

            mock_fv.return_value = False
            mock_fa.return_value = []   # sin vínculo

            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is False

    @pytest.mark.asyncio
    async def test_direct_access_shortcircuits_case_lookup(self):
        """
        Si el usuario ya tiene acceso directo (fetch_val=True), no debe
        llamar a fetch_all (optimizacion de cortocircuito).
        """
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
        """
        Si el doc esta en 2 expedientes, basta con que el usuario
        pueda ver UNO para que la funcion retorne True sin consultar el segundo.
        """
        from services.documents.permissions import can_user_view_document

        CASE_ID_2 = "cccccccc-0000-0000-0000-000000000002"

        with patch("services.documents.permissions.fetch_val", new_callable=AsyncMock) as mock_fv, \
             patch("services.documents.permissions.fetch_all", new_callable=AsyncMock) as mock_fa, \
             patch("services.cases.permissions.can_user_view_case", new_callable=AsyncMock) as mock_case:

            mock_fv.return_value = False
            mock_fa.return_value = [{"case_id": CASE_ID}, {"case_id": CASE_ID_2}]
            # Primero True: cortocircuito
            mock_case.side_effect = [True, False]

            result = await can_user_view_document(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert result is True
        # Solo debio llamar can_user_view_case UNA vez (cortocircuito)
        assert mock_case.call_count == 1

    @pytest.mark.asyncio
    async def test_query_includes_via_record_criterion(self):
        """
        Via legajo/RLM: el query principal de can_user_view_document debe
        contemplar documentos vinculados via record_document_links a un
        registro cuya registry_family el usuario puede ver (can_view). Espeja
        el criterio de la busqueda semantica para que lo que aparece en la
        busqueda por legajo se pueda abrir.
        """
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
        # Paridad estricta con la busqueda: solo oficiales firmados via legajo
        # (los borradores NO se reabren por este camino).
        assert "official_documents od_link" in sql
        assert "od_link.signed_at IS NOT NULL" in sql
        # Mismos filtros de sector que casos 4a/4b y user_sectors de la busqueda.
        assert "s.is_active = true" in sql
        assert "usp.can_view = true" in sql
        # Nuevos filtros O2: familia activa y record activo en el bloque via-legajo.
        assert "rf.is_active = true" in sql, \
            "Caso 6 debe filtrar por registry_families.is_active = true"
        assert "r.state = 'Activo'" in sql, \
            "Caso 6 debe filtrar por records.state = 'Activo' (excluye archivados/inactivos)"


# ===========================================================================
# (b) GET /documents/{id}/details — doc signed sin acceso → 403
# ===========================================================================

class TestUnifiedDetailsSignedIDOR:
    """
    Verifica que get_unified_document_details aplique can_user_view_document
    TAMBIEN para documentos en estado 'signed' (antes saltaba el chequeo).
    """

    @pytest.mark.asyncio
    async def test_signed_doc_without_permission_raises_authorization_error(self):
        """
        Estado='signed', can_user_view_document=False → AuthorizationError.
        """
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
        """
        Estado='signed', can_user_view_document=True → no lanza error de auth,
        llama al builder de firma.
        """
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
        """
        Estado='sent_to_sign' sin acceso → AuthorizationError (comportamiento previo
        que debe mantenerse).
        """
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
        """
        IDOR borradores: estado='draft' (categoria='editing') sin acceso →
        AuthorizationError. Antes 'editing' saltaba el chequeo y cualquier
        usuario del tenant podia leer un borrador ajeno por document_id.
        """
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

        # Sin permiso no se entrega el borrador
        mock_edit.assert_not_called()

    @pytest.mark.asyncio
    async def test_editing_rejected_without_permission_denied(self):
        """
        IDOR borradores: estado='rejected' (categoria='editing') sin acceso →
        AuthorizationError. Espeja el caso 'draft' para blindar la matriz si
        alguien tocara STATE_CATEGORY_MAP.
        """
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
        """
        Estado='draft' con acceso (creador, mismo sector, firmante o global) →
        se entrega el borrador. Preserva la edicion colaborativa por sector.
        """
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
        """
        Estado='draft' sin user_id → ValidationError (ya no se entrega sin
        identidad). Los callers reales (endpoint JWT, gateway MCP) siempre
        pasan user_id.
        """
        from services.documents.retrieval.unified_details import get_unified_document_details
        from shared.exceptions import ValidationError

        with patch("services.documents.retrieval.unified_details._get_document_status",
                   new_callable=AsyncMock) as mock_status:
            mock_status.return_value = "draft"

            with pytest.raises(ValidationError):
                await get_unified_document_details(DOC_ID, user_id=None, schema_name=SCHEMA)


# ===========================================================================
# (c) "Mis Documentos" NO incluye docs-por-expediente
# ===========================================================================

class TestMisDocumentosNoIncludesCaseDocuments:
    """
    Verifica que get_user_documents filtre solo por SET propio del usuario
    (sector/autor/firmante), sin incluir la via expediente.
    """

    def test_get_user_documents_query_has_no_case_official_documents(self):
        """
        La query SQL de get_user_documents NO debe referenciar
        case_official_documents como fuente de inclusion de documentos.
        Solo puede referenciarla para enriquecer linked_cases (JOIN lateral, no WHERE).
        """
        import inspect
        from services import document_service

        source = inspect.getsource(document_service.get_user_documents)

        # La funcion puede tener case_official_documents en un SELECT para linked_cases
        # pero NO debe haber un OR EXISTS referenciando case_official_documents en el
        # filtro de acceso del WHERE principal.
        # Verificamos que el patron de inclusion via expediente no este como condicion
        # de acceso (OR ... case_official_documents ... cm.assigned_sector_id).
        assert "cm.assigned_sector_id" not in source, (
            "get_user_documents NO debe filtrar inclusion de documentos "
            "por sector asignado de movimientos de expediente. "
            "Ese patron es de semantic search / can_user_view_document."
        )

    def test_semantic_search_query_includes_case_access_conditions(self):
        """
        SEMANTIC_SEARCH_SQL si debe tener la via expediente para los 5 casos
        definidos en can_user_view_case:
        - assigned_sector (3a), admin-transfer (3b), admin-creation (3c),
          case creator (2), global_search_cases flag (1).
        """
        from services.search.queries import SEMANTIC_SEARCH_SQL

        # 3a: sector asignado activo
        assert "cm.assigned_sector_id" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL debe incluir via expediente por sector asignado (caso 3a)"
        # 3b: admin por ultima transferencia
        assert "cm.admin_sector_id" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL debe incluir via expediente por admin-transfer (caso 3b)"
        # 3c: admin por creacion sin transfers
        assert "cm.type = 'creation'" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL debe incluir via expediente por admin-creation (caso 3c)"
        # caso 2: creador del expediente
        assert "c.created_by_user_id" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL debe incluir via expediente por creador del expediente (caso 2)"
        # caso 1: flag global de cases
        assert "can_global_search_cases" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL debe incluir via expediente por can_global_search_cases (caso 1)"

    def test_lookup_search_query_includes_case_access_conditions(self):
        """
        LOOKUP_DOCUMENT_SQL debe tener los mismos 5 casos que SEMANTIC_SEARCH_SQL.
        """
        from services.search.queries import LOOKUP_DOCUMENT_SQL

        assert "cm.assigned_sector_id" in LOOKUP_DOCUMENT_SQL
        assert "cm.admin_sector_id" in LOOKUP_DOCUMENT_SQL
        assert "cm.type = 'creation'" in LOOKUP_DOCUMENT_SQL
        assert "c.created_by_user_id" in LOOKUP_DOCUMENT_SQL
        assert "can_global_search_cases" in LOOKUP_DOCUMENT_SQL

    def test_semantic_search_via_legajo_filters_active_family_and_record(self):
        """
        O2: el bloque OR EXISTS via-legajo de SEMANTIC_SEARCH_SQL debe filtrar por
        registry_families.is_active = true y records.state = 'Activo', para que
        familias desactivadas o records archivados/inactivos no otorguen acceso.
        """
        from services.search.queries import SEMANTIC_SEARCH_SQL

        assert "rf.is_active = true" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL via-legajo debe filtrar rf.is_active = true"
        assert "r.state = 'Activo'" in SEMANTIC_SEARCH_SQL, \
            "SEMANTIC_SEARCH_SQL via-legajo debe filtrar r.state = 'Activo'"

    def test_lookup_search_via_legajo_filters_active_family_and_record(self):
        """
        O2: el bloque OR EXISTS via-legajo de LOOKUP_DOCUMENT_SQL debe filtrar por
        registry_families.is_active = true y records.state = 'Activo'. Paridad
        obligatoria con SEMANTIC_SEARCH_SQL y con can_user_view_document caso 6.
        """
        from services.search.queries import LOOKUP_DOCUMENT_SQL

        assert "rf.is_active = true" in LOOKUP_DOCUMENT_SQL, \
            "LOOKUP_DOCUMENT_SQL via-legajo debe filtrar rf.is_active = true"
        assert "r.state = 'Activo'" in LOOKUP_DOCUMENT_SQL, \
            "LOOKUP_DOCUMENT_SQL via-legajo debe filtrar r.state = 'Activo'"

    def test_mis_documentos_query_does_not_have_case_inclusion_via_global_search(self):
        """
        get_user_documents NO debe tener can_global_search_cases en su query
        (esa superficie solo usa can_global_search_documents).
        """
        import inspect
        from services import document_service

        source = inspect.getsource(document_service.get_user_documents)

        assert "can_global_search_cases" not in source, (
            "get_user_documents no debe usar can_global_search_cases: "
            "ese flag es de expedientes, no del listado de 'Mis Documentos'."
        )


# ===========================================================================
# (d) Semantic search — via expediente presente en la query SQL
# ===========================================================================

class TestSemanticSearchCaseAccess:
    """
    Verifica que el path de acceso via expediente este correctamente incluido
    en SEMANTIC_SEARCH_SQL sin romper el hibrido BM25+RRF.
    """

    def test_hybrid_bm25_rrf_structure_intact(self):
        """
        El hibrido BM25+RRF debe mantener sus CTEs: vector_cands, bm25_cands,
        best_vector, ranked_vector, best_bm25, ranked_bm25, fused, permitted.
        """
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
        """La formula RRF (1/(60+rank)) debe estar presente."""
        from services.search.queries import SEMANTIC_SEARCH_SQL

        assert "1.0 / (60.0 + rv.vec_rank)" in SEMANTIC_SEARCH_SQL, \
            "Formula RRF de vector no encontrada"
        assert "1.0 / (60.0 + rb.bm25_rank)" in SEMANTIC_SEARCH_SQL, \
            "Formula RRF de BM25 no encontrada"

    @pytest.mark.asyncio
    async def test_semantic_search_passes_user_id_to_sql(self):
        """
        semantic_search debe pasar user_id como primer parametro a
        SEMANTIC_SEARCH_SQL (necesario para el filtro de permisos).
        """
        from services.search.semantic_search import semantic_search

        captured_params = {}

        async def mock_fetch_all(sql, *params, schema_name):
            captured_params["params"] = params
            captured_params["schema_name"] = schema_name
            return []

        mock_embedding_result = {
            "embedding": [0.1] * 1536,
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
        """
        El path 'lookup' de semantic_search debe pasar user_id como
        primer parametro a LOOKUP_DOCUMENT_SQL.
        """
        from services.search.semantic_search import semantic_search

        captured_params = {}

        async def mock_fetch_all(sql, *params, schema_name):
            captured_params["params"] = params
            return []

        with patch("services.search.semantic_search.fetch_all",
                   side_effect=mock_fetch_all), \
             patch("services.search.semantic_search._fire_and_forget_log"):

            # "Ordenanza 6057" es un query tipo lookup
            await semantic_search(
                "Ordenanza 6057",
                USER_ID,
                schema_name=SCHEMA,
                limit=5,
            )

        assert captured_params.get("params") is not None
        assert captured_params["params"][0] == USER_ID, \
            "El primer parametro de LOOKUP_DOCUMENT_SQL debe ser user_id"


# ===========================================================================
# Extras: constante de expiracion URL alineada
# ===========================================================================

class TestCloudflareUrlExpirationConstant:
    """
    Verifica que CLOUDFLARE_URL_EXPIRATION refleje CF_R2_SIGN_EXPIRATION
    (no el hardcoded "15 minutes" incorrecto).
    """

    def test_expiration_default_is_10_minutos(self):
        """Sin env var, el default 600s debe producir '10 minutos'."""
        import os
        # Remover la variable si estuviera seteada en el entorno de test
        original = os.environ.pop("CF_R2_SIGN_EXPIRATION", None)
        try:
            # Reimportar el modulo para que recalcule con el entorno limpio
            import importlib
            import config.constants as const_module
            importlib.reload(const_module)
            assert const_module.CLOUDFLARE_URL_EXPIRATION_SECONDS == 600
            assert const_module.CLOUDFLARE_URL_EXPIRATION == "10 minutos"
        finally:
            if original is not None:
                os.environ["CF_R2_SIGN_EXPIRATION"] = original
            # Restaurar el modulo original
            importlib.reload(const_module)

    def test_expiration_text_is_not_15_minutes(self):
        """El texto '15 minutes' (el valor incorrecto anterior) no debe aparecer."""
        from config import constants
        assert constants.CLOUDFLARE_URL_EXPIRATION != "15 minutes", (
            "El texto '15 minutes' es incorrecto: la expiracion real es "
            "600s = 10 minutos por default."
        )
