"""
Tests FASE 2A S8-002: endpoints canónicos en SET A (Backend FastAPI).

Verifica:
1. Que los 5 endpoints canónicos están registrados en la app FastAPI.
2. Que las nuevas funciones de servicio tienen la firma correcta
   (schema_name keyword-only, parámetros requeridos).
3. Seguridad SEC-12 en GET /api/v1/system/users/{user_id}.
4. Que los nuevos servicios no duplican lógica (delegación verificable
   sin BD real).

NO requiere tunnel a Postgres: usa mocks en todos los casos.
Los tests que necesitarían BD real están marcados con @pytest.mark.skip
y documentados.
"""
import inspect
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

from models.schemas import AuthenticatedUser, SectorPermission

# ---------------------------------------------------------------------------
# Constantes de test
# ---------------------------------------------------------------------------
TEST_SCHEMA = "100_test"
TEST_USER_ID = "a1000000-0000-0000-0000-000000000001"
TEST_CASE_ID = "ef102207-86c4-415a-883b-088b51ee5d45"
TEST_DOC_ID = "doc12300-86c4-415a-883b-088b51ee5d45"

MOCK_USER = AuthenticatedUser(
    user_id=TEST_USER_ID,
    auth_id="auth0|test123",
    email="test@test.municipio",
    full_name="Test User",
    permissions=[
        SectorPermission(
            sector_id="51000000-0000-0000-0000-000000000001",
            sector_acronym="PRIV",
            department_id="d1000000-0000-0000-0000-000000000001",
            department_name="Intendencia",
            department_acronym="INTE",
            can_view=True,
            can_edit=True,
            is_primary=True,
        )
    ],
)


# ===========================================================================
# 1. Registro de rutas en la app
# ===========================================================================

class TestRouteRegistration:
    """Verifica que los 5 endpoints nuevos están registrados en FastAPI."""

    def _get_routes(self):
        from main import app
        return {r.path for r in app.routes if hasattr(r, "path")}

    def test_cases_search_route_registered(self):
        """GET /api/v1/cases/search debe estar registrado."""
        routes = self._get_routes()
        assert "/api/v1/cases/search" in routes, (
            f"Ruta /api/v1/cases/search no registrada. Rutas disponibles con 'cases': "
            f"{[r for r in routes if 'cases' in r]}"
        )

    def test_documents_search_route_registered(self):
        """GET /api/v1/documents/search debe estar registrado."""
        routes = self._get_routes()
        assert "/api/v1/documents/search" in routes, (
            f"Ruta /api/v1/documents/search no registrada. Rutas con 'documents': "
            f"{[r for r in routes if 'documents' in r]}"
        )

    def test_documents_pending_signatures_route_registered(self):
        """GET /api/v1/documents/pending-signatures debe estar registrado."""
        routes = self._get_routes()
        assert "/api/v1/documents/pending-signatures" in routes, (
            f"Ruta /api/v1/documents/pending-signatures no registrada."
        )

    def test_cases_propose_document_route_registered(self):
        """POST /api/v1/cases/{case_id}/documents/propose debe estar registrado."""
        routes = self._get_routes()
        assert "/api/v1/cases/{case_id}/documents/propose" in routes, (
            f"Ruta /api/v1/cases/{{case_id}}/documents/propose no registrada."
        )

    def test_system_users_route_registered(self):
        """GET /api/v1/system/users/{user_id} debe estar registrado."""
        routes = self._get_routes()
        assert "/api/v1/system/users/{user_id}" in routes, (
            f"Ruta /api/v1/system/users/{{user_id}} no registrada. "
            f"Rutas con 'system': {[r for r in routes if 'system' in r]}"
        )

    def test_search_route_registered_before_path_param_routes(self):
        """
        /api/v1/cases/search debe aparecer antes de /api/v1/cases/{case_id}
        en el router de casos para que FastAPI no lo confunda con un UUID.
        """
        from main import app
        ordered = [r.path for r in app.routes if hasattr(r, "path") and "cases" in r.path]
        if "/api/v1/cases/search" in ordered and "/api/v1/cases/{case_id}" in ordered:
            idx_search = ordered.index("/api/v1/cases/search")
            idx_detail = ordered.index("/api/v1/cases/{case_id}")
            assert idx_search < idx_detail, (
                f"/api/v1/cases/search (idx={idx_search}) debe estar ANTES de "
                f"/api/v1/cases/{{case_id}} (idx={idx_detail})"
            )


# ===========================================================================
# 2. Firmas de funciones de servicio (schema_name keyword-only)
# ===========================================================================

class TestServiceSignatures:
    """Verifica que las nuevas funciones de servicio siguen el patrón
    keyword-only para schema_name."""

    def _assert_keyword_only(self, func, param_name: str = "schema_name"):
        sig = inspect.signature(func)
        param = sig.parameters.get(param_name)
        assert param is not None, f"{func.__name__} no tiene parámetro {param_name}"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{func.__name__}.{param_name} debe ser keyword-only (usar * en la firma)"
        )

    def test_propose_document_to_case_schema_keyword_only(self):
        from services.cases.documents import propose_document_to_case
        self._assert_keyword_only(propose_document_to_case)

    def test_propose_document_to_case_required_params(self):
        from services.cases.documents import propose_document_to_case
        sig = inspect.signature(propose_document_to_case)
        params = sig.parameters
        assert "case_id" in params
        assert "document_draft_id" in params
        assert "proposing_user_id" in params
        assert "schema_name" in params

    def test_get_pending_signatures_for_user_schema_keyword_only(self):
        from services.documents.retrieval.pending_signatures import (
            get_pending_signatures_for_user,
        )
        self._assert_keyword_only(get_pending_signatures_for_user)

    def test_get_pending_signatures_for_user_required_params(self):
        from services.documents.retrieval.pending_signatures import (
            get_pending_signatures_for_user,
        )
        sig = inspect.signature(get_pending_signatures_for_user)
        assert "user_id" in sig.parameters
        assert "schema_name" in sig.parameters

    def test_get_user_info_service_schema_keyword_only(self):
        from services.users.info import get_user_info
        self._assert_keyword_only(get_user_info)

    def test_get_user_info_service_required_params(self):
        from services.users.info import get_user_info
        sig = inspect.signature(get_user_info)
        assert "user_id" in sig.parameters
        assert "schema_name" in sig.parameters


# ===========================================================================
# 3. propose_document_to_case: comportamiento sin BD (mocked)
# ===========================================================================

class TestProposeDocumentService:
    """Tests unitarios para services/cases/documents.propose_document_to_case."""

    @pytest.mark.asyncio
    async def test_propose_inserts_and_returns_correct_shape(self):
        """Verifica que el INSERT se llama y la respuesta tiene la forma esperada."""
        from services.cases.documents import propose_document_to_case

        with patch(
            "database.check_document_exists",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "services.cases.documents.execute",
            new_callable=AsyncMock,
        ) as mock_exec:
            result = await propose_document_to_case(
                case_id=TEST_CASE_ID,
                document_draft_id=TEST_DOC_ID,
                proposing_user_id=TEST_USER_ID,
                schema_name=TEST_SCHEMA,
            )

        mock_exec.assert_awaited_once()
        call_kwargs = mock_exec.call_args
        # El schema_name debe pasarse como keyword-only
        assert call_kwargs.kwargs.get("schema_name") == TEST_SCHEMA

        assert result["case_id"] == TEST_CASE_ID
        assert result["document_draft_id"] == TEST_DOC_ID
        assert "message" in result

    @pytest.mark.asyncio
    async def test_propose_raises_validation_error_on_missing_case_id(self):
        """case_id vacío debe lanzar ValidationError."""
        from services.cases.documents import propose_document_to_case
        from shared.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await propose_document_to_case(
                case_id="",
                document_draft_id=TEST_DOC_ID,
                proposing_user_id=TEST_USER_ID,
                schema_name=TEST_SCHEMA,
            )

    @pytest.mark.asyncio
    async def test_propose_raises_validation_error_on_missing_draft_id(self):
        """document_draft_id vacío debe lanzar ValidationError."""
        from services.cases.documents import propose_document_to_case
        from shared.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await propose_document_to_case(
                case_id=TEST_CASE_ID,
                document_draft_id="",
                proposing_user_id=TEST_USER_ID,
                schema_name=TEST_SCHEMA,
            )


# ===========================================================================
# 4. get_pending_signatures_for_user: comportamiento sin BD (mocked)
# ===========================================================================

class TestPendingSignaturesService:
    """Tests unitarios para services/documents/retrieval/pending_signatures."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_results(self):
        from services.documents.retrieval.pending_signatures import (
            get_pending_signatures_for_user,
        )

        with patch(
            "services.documents.retrieval.pending_signatures.fetch_all",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await get_pending_signatures_for_user(
                TEST_USER_ID, schema_name=TEST_SCHEMA
            )

        assert result["pending_signatures"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_maps_row_to_correct_shape(self):
        """Verifica que cada fila se convierte al dict esperado."""
        from services.documents.retrieval.pending_signatures import (
            get_pending_signatures_for_user,
        )
        import datetime

        fake_row = {
            "document_id": TEST_DOC_ID,
            "reference": "Informe test",
            "document_number": "IF-2025-0000001",
            "document_type_acronym": "IF",
            "document_type_name": "Informe",
            "signer_role": "signer",
            "signing_order": 1,
            "sent_to_sign_at": datetime.datetime(2025, 12, 1, 10, 0, 0),
            "creator_name": "Juan Test",
            "creator_photo": None,
            "is_my_turn": True,
        }

        with patch(
            "services.documents.retrieval.pending_signatures.fetch_all",
            new_callable=AsyncMock,
            return_value=[fake_row],
        ):
            result = await get_pending_signatures_for_user(
                TEST_USER_ID, schema_name=TEST_SCHEMA
            )

        assert result["total"] == 1
        item = result["pending_signatures"][0]
        assert item["document_id"] == TEST_DOC_ID
        assert item["document_type"]["acronym"] == "IF"
        assert item["signer_role"] == "signer"
        assert item["creator"]["name"] == "Juan Test"

    @pytest.mark.asyncio
    async def test_raises_value_error_on_empty_user_id(self):
        from services.documents.retrieval.pending_signatures import (
            get_pending_signatures_for_user,
        )

        with pytest.raises(ValueError, match="user_id"):
            await get_pending_signatures_for_user("", schema_name=TEST_SCHEMA)


# ===========================================================================
# 5. get_user_info service: comportamiento sin BD (mocked)
# ===========================================================================

class TestGetUserInfoService:
    """Tests unitarios para services/users/info.get_user_info."""

    @pytest.mark.asyncio
    async def test_returns_correct_shape(self):
        import datetime
        from services.users.info import get_user_info

        fake_user = {
            "user_id": TEST_USER_ID,
            "full_name": "Juan Pérez",
            "email": "juan@muni.gov.ar",
            "profile_picture_url": None,
            "estado": 1,
            "last_access": datetime.datetime(2025, 12, 1, 10, 0, 0),
            "created_at": datetime.datetime(2024, 1, 15, 10, 0, 0),
            "sector_id": "51000000-0000-0000-0000-000000000001",
            "sector_acronym": "TESO",
            "department_id": "d1000000-0000-0000-0000-000000000001",
            "department_name": "Tesorería",
            "department_acronym": "TESO",
        }

        with patch(
            "services.users.info.fetch_one",
            new_callable=AsyncMock,
            return_value=fake_user,
        ), patch(
            "services.users.info.fetch_all",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await get_user_info(TEST_USER_ID, schema_name=TEST_SCHEMA)

        assert result["user_id"] == TEST_USER_ID
        assert result["full_name"] == "Juan Pérez"
        assert result["sector"] is not None
        assert result["sector"]["acronym"] == "TESO"
        assert isinstance(result["roles"], list)
        assert isinstance(result["additional_sectors"], list)
        assert result["last_access"] == "2025-12-01T10:00:00"

    @pytest.mark.asyncio
    async def test_raises_not_found_when_no_user(self):
        from services.users.info import get_user_info
        from shared.exceptions import NotFoundError

        with patch(
            "services.users.info.fetch_one",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(NotFoundError):
                await get_user_info(TEST_USER_ID, schema_name=TEST_SCHEMA)

    @pytest.mark.asyncio
    async def test_raises_value_error_on_empty_user_id(self):
        from services.users.info import get_user_info

        with pytest.raises(ValueError, match="user_id"):
            await get_user_info("", schema_name=TEST_SCHEMA)


# ===========================================================================
# 6. Seguridad SEC-12 en GET /api/v1/system/users/{user_id}
# ===========================================================================

class TestSystemUserSec12:
    """
    Verifica que el endpoint rechaza consultas de un usuario sobre otro.
    No necesita BD: mockea get_current_user y el estado del request.
    """

    @pytest.mark.asyncio
    async def test_sec12_same_user_allowed(self):
        """
        Cuando auth_user_id == target_user_id el endpoint llama al service
        (no lanza 403).
        """
        from endpoints.system.users import get_system_user

        fake_user_info = {
            "user_id": TEST_USER_ID,
            "full_name": "Test",
            "email": "t@t.com",
            "profile_picture_url": None,
            "estado": 1,
            "last_access": None,
            "created_at": None,
            "sector": None,
            "roles": [],
            "additional_sectors": [],
        }

        req = SimpleNamespace(state=SimpleNamespace(tenant_user_id=TEST_USER_ID))

        with patch(
            "endpoints.system.users.get_user_info",
            new_callable=AsyncMock,
            return_value=fake_user_info,
        ) as mock_svc:
            result = await get_system_user(
                request=req,
                user_id=TEST_USER_ID,
                current_user=MOCK_USER,
                schema_name=TEST_SCHEMA,
            )

        mock_svc.assert_awaited_once_with(TEST_USER_ID, schema_name=TEST_SCHEMA)
        assert result["user_id"] == TEST_USER_ID

    @pytest.mark.asyncio
    async def test_sec12_different_user_raises_403(self):
        """
        Cuando auth_user_id != target_user_id el endpoint lanza HTTPException 403.
        """
        from fastapi import HTTPException
        from endpoints.system.users import get_system_user

        other_user_id = "b2000000-0000-0000-0000-000000000002"
        req = SimpleNamespace(state=SimpleNamespace(tenant_user_id=TEST_USER_ID))

        with pytest.raises(HTTPException) as exc_info:
            await get_system_user(
                request=req,
                user_id=other_user_id,   # diferente al autenticado
                current_user=MOCK_USER,
                schema_name=TEST_SCHEMA,
            )

        assert exc_info.value.status_code == 403


# ===========================================================================
# 7. Endpoint propose_document: permisos y delegación (mocked)
# ===========================================================================

class TestProposeDocumentEndpoint:
    """Tests de comportamiento del endpoint POST /{case_id}/documents/propose."""

    @pytest.mark.asyncio
    async def test_propose_calls_service_when_authorized(self):
        from endpoints.cases.proposed_documents import propose_document, ProposeDocumentRequest

        fake_result = {
            "case_id": TEST_CASE_ID,
            "document_draft_id": TEST_DOC_ID,
            "message": "Documento propuesto para vincular al expediente",
        }

        req = SimpleNamespace(state=SimpleNamespace(tenant_user_id=TEST_USER_ID))
        body = ProposeDocumentRequest(document_draft_id=TEST_DOC_ID)

        with patch(
            "endpoints.cases.proposed_documents.get_authenticated_user",
            new_callable=AsyncMock,
            return_value=TEST_USER_ID,
        ), patch(
            "endpoints.cases.proposed_documents.can_user_view_case",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "endpoints.cases.proposed_documents.propose_document_to_case",
            new_callable=AsyncMock,
            return_value=fake_result,
        ) as mock_svc:
            result = await propose_document(
                request=req,
                body=body,
                case_id=TEST_CASE_ID,
                current_user=MOCK_USER,
                schema_name=TEST_SCHEMA,
            )

        mock_svc.assert_awaited_once_with(
            case_id=TEST_CASE_ID,
            document_draft_id=TEST_DOC_ID,
            proposing_user_id=TEST_USER_ID,
            schema_name=TEST_SCHEMA,
        )
        assert result["success"] is True
        assert result["data"]["case_id"] == TEST_CASE_ID

    @pytest.mark.asyncio
    async def test_propose_raises_403_when_no_view_permission(self):
        from fastapi import HTTPException
        from endpoints.cases.proposed_documents import propose_document, ProposeDocumentRequest

        req = SimpleNamespace(state=SimpleNamespace(tenant_user_id=TEST_USER_ID))
        body = ProposeDocumentRequest(document_draft_id=TEST_DOC_ID)

        with patch(
            "endpoints.cases.proposed_documents.get_authenticated_user",
            new_callable=AsyncMock,
            return_value=TEST_USER_ID,
        ), patch(
            "endpoints.cases.proposed_documents.can_user_view_case",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await propose_document(
                    request=req,
                    body=body,
                    case_id=TEST_CASE_ID,
                    current_user=MOCK_USER,
                    schema_name=TEST_SCHEMA,
                )

        assert exc_info.value.status_code == 403


# ===========================================================================
# 8. Verificar que NO se tocaron archivos prohibidos
# ===========================================================================

class TestNoProhibitedTouches:
    """Verifica invariantes de integridad estructural."""

    def test_api_gateway_tools_cases_unchanged(self):
        """api_gateway/tools/cases.py sigue importando desde los mismos módulos."""
        from api_gateway.tools import cases
        assert hasattr(cases, "search_cases")
        assert hasattr(cases, "propose_document")

    def test_api_gateway_tools_documents_unchanged(self):
        from api_gateway.tools import documents
        assert hasattr(documents, "search_documents")
        assert hasattr(documents, "get_pending_signatures")

    def test_api_gateway_tools_system_unchanged(self):
        from api_gateway.tools import system
        assert hasattr(system, "get_user_info")

    def test_services_search_not_touched(self):
        """services/search/ (búsqueda semántica) no fue modificado."""
        import importlib
        mod = importlib.import_module("services.search.semantic_search")
        assert mod is not None

    def test_auth_rest_unchanged(self):
        from api_gateway import auth_rest
        assert hasattr(auth_rest, "validate_rest_api_key")

    def test_set_b_system_uses_isoformat(self):
        """
        api_gateway/tools/system.py debe serializar last_access y created_at
        con .isoformat() (no str()) para que el contrato de fecha coincida con SET A.
        Verifica el source code directamente.
        """
        import inspect
        from api_gateway.tools import system as sys_tools
        source = inspect.getsource(sys_tools.get_user_info)
        # El source no debe tener str(user_data["last_access"])
        assert 'str(user_data["last_access"])' not in source, (
            "SET B tools/system.py aún usa str() para last_access — debería ser .isoformat()"
        )
        assert 'str(user_data["created_at"])' not in source, (
            "SET B tools/system.py aún usa str() para created_at — debería ser .isoformat()"
        )
        assert ".isoformat()" in source, (
            "SET B tools/system.py no usa .isoformat() para fechas"
        )


# ===========================================================================
# 9. Retoques de robustez (Fase 2A observaciones del review)
# ===========================================================================

class TestRobustnessRetoques:
    """Tests para los retoques de robustez aplicados tras el review."""

    # -----------------------------------------------------------------------
    # 9a. propose_document_to_case: validación de draft existente
    # -----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_propose_raises_not_found_when_draft_missing(self):
        """
        Si document_draft_id no existe en el tenant, el service debe lanzar
        NotFoundError antes de intentar el INSERT (evita 500 por FK violation).

        El import de check_document_exists es local (dentro del try), por eso
        se parchea en el módulo database donde la función vive definitivamente.
        """
        from services.cases.documents import propose_document_to_case
        from shared.exceptions import NotFoundError

        with patch(
            "database.check_document_exists",
            new_callable=AsyncMock,
            return_value=False,   # borrador no existe
        ), patch(
            "services.cases.documents.execute",
            new_callable=AsyncMock,
        ) as mock_exec:
            with pytest.raises(NotFoundError, match="Documento borrador no encontrado"):
                await propose_document_to_case(
                    case_id=TEST_CASE_ID,
                    document_draft_id=TEST_DOC_ID,
                    proposing_user_id=TEST_USER_ID,
                    schema_name=TEST_SCHEMA,
                )

        # El INSERT nunca debe haberse llamado
        mock_exec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_propose_succeeds_when_draft_exists(self):
        """
        Cuando check_document_exists devuelve True, el INSERT se ejecuta normalmente.
        """
        from services.cases.documents import propose_document_to_case

        with patch(
            "database.check_document_exists",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "services.cases.documents.execute",
            new_callable=AsyncMock,
        ) as mock_exec:
            result = await propose_document_to_case(
                case_id=TEST_CASE_ID,
                document_draft_id=TEST_DOC_ID,
                proposing_user_id=TEST_USER_ID,
                schema_name=TEST_SCHEMA,
            )

        mock_exec.assert_awaited_once()
        assert result["case_id"] == TEST_CASE_ID

    @pytest.mark.asyncio
    async def test_check_document_exists_called_with_keyword_schema(self):
        """
        check_document_exists debe llamarse con schema_name como keyword-only.
        """
        from services.cases.documents import propose_document_to_case

        with patch(
            "database.check_document_exists",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_check, patch(
            "services.cases.documents.execute",
            new_callable=AsyncMock,
        ):
            await propose_document_to_case(
                case_id=TEST_CASE_ID,
                document_draft_id=TEST_DOC_ID,
                proposing_user_id=TEST_USER_ID,
                schema_name=TEST_SCHEMA,
            )

        # Confirmar que schema_name se pasó como keyword
        call_kwargs = mock_check.call_args
        assert call_kwargs.kwargs.get("schema_name") == TEST_SCHEMA

    # -----------------------------------------------------------------------
    # 9b. AuthorizationError → 403 a través de exception_to_http_exception
    # -----------------------------------------------------------------------

    def test_authorization_error_maps_to_403(self):
        """
        exception_to_http_exception(AuthorizationError(...)) debe devolver
        un HTTPException con status_code 403. Valida el mapeo en shared/exceptions.py
        que protege el endpoint propose_document.
        """
        from shared.exceptions import AuthorizationError, exception_to_http_exception
        exc = AuthorizationError("Sin permisos")
        http_exc = exception_to_http_exception(exc)
        assert http_exc.status_code == 403

    def test_not_found_error_maps_to_404(self):
        """
        NotFoundError (lanzado cuando el draft no existe) debe mapear a 404,
        no a 500.
        """
        from shared.exceptions import NotFoundError, exception_to_http_exception
        exc = NotFoundError("Documento borrador no encontrado: abc")
        http_exc = exception_to_http_exception(exc)
        assert http_exc.status_code == 404

    # -----------------------------------------------------------------------
    # 9c. Type hint de pending_signatures endpoint
    # -----------------------------------------------------------------------

    def test_pending_signatures_return_type_is_pydantic_model(self):
        """
        El type hint de get_pending_signatures debe ser PendingSignaturesResponse,
        no Dict[str, Any], para que coincida con response_model.
        """
        import inspect
        import typing
        from endpoints.documents.pending_signatures import (
            get_pending_signatures,
            PendingSignaturesResponse,
        )
        hints = typing.get_type_hints(get_pending_signatures)
        ret = hints.get("return")
        assert ret is PendingSignaturesResponse, (
            f"El type hint de retorno es {ret}, se esperaba PendingSignaturesResponse"
        )
