import inspect
import pytest
from unittest.mock import AsyncMock, patch


TEST_SCHEMA = "100_test"
TEST_USER_ID = "a1000000-0000-0000-0000-000000000001"
TEST_CASE_ID = "ef102207-86c4-415a-883b-088b51ee5d45"
TEST_DOC_ID  = "doc12300-86c4-415a-883b-088b51ee5d45"
TEST_RESPONSIBLE_ID = "res12300-86c4-415a-883b-088b51ee5d45"


class TestRestApiExports:

    def test_subsanar_handler_exported(self):
        from api_gateway.rest_api import api_subsanar_document
        assert callable(api_subsanar_document)

    def test_get_case_movements_handler_exported(self):
        from api_gateway.rest_api import api_get_case_movements
        assert callable(api_get_case_movements)

    def test_subsanar_is_coroutine(self):
        from api_gateway.rest_api import api_subsanar_document
        assert inspect.iscoroutinefunction(api_subsanar_document)

    def test_movements_is_coroutine(self):
        from api_gateway.rest_api import api_get_case_movements
        assert inspect.iscoroutinefunction(api_get_case_movements)


class TestGatewayRouteRegistration:

    def _get_routes(self):
        from api_gateway.http_server import routes as gateway_routes
        result = {}
        for route in gateway_routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                for method in (route.methods or []):
                    result[(route.path, method)] = getattr(route, "endpoint", None)
        return result

    def test_subsanar_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/cases/{case_id}/subsanar", "POST") in routes, (
            "Ruta POST /api/v1/cases/{case_id}/subsanar no registrada en el Gateway"
        )

    def test_delete_document_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/documents/{document_id}", "DELETE") in routes, (
            "Ruta DELETE /api/v1/documents/{document_id} no registrada en el Gateway"
        )

    def test_get_case_responsibles_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/cases/{case_id}/responsibles", "GET") in routes

    def test_add_case_responsible_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/cases/{case_id}/responsibles", "POST") in routes

    def test_remove_case_responsible_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/cases/{case_id}/responsibles/{responsible_id}", "DELETE") in routes

    def test_get_case_movements_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/cases/{case_id}/movements", "GET") in routes, (
            "Ruta GET /api/v1/cases/{case_id}/movements no registrada en el Gateway"
        )

    def test_import_document_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/documents/import", "POST") in routes

    def test_replace_imported_pdf_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/documents/{document_id}/imported-pdf", "PUT") in routes


class TestMcpToolsDefinition:

    def _get_tool_names(self):
        import asyncio
        from api_gateway import http_server

        captured = {}

        async def _run():
            original = http_server.create_jsonrpc_response

            def _capture(request_id, result):
                captured["result"] = result
                return result

            with patch.object(http_server, "create_jsonrpc_response", side_effect=_capture):
                await http_server.handle_list_tools(request_id=1)

        asyncio.get_event_loop().run_until_complete(_run())
        tools = captured.get("result", {}).get("tools", [])
        return {t["name"] for t in tools}

    def test_get_case_responsibles_in_tool_list(self):
        names = self._get_tool_names()
        assert "get_case_responsibles" in names, (
            f"Tool 'get_case_responsibles' no encontrada. Tools disponibles: {names}"
        )

    def test_add_case_responsible_in_tool_list(self):
        names = self._get_tool_names()
        assert "add_case_responsible" in names

    def test_remove_case_responsible_in_tool_list(self):
        names = self._get_tool_names()
        assert "remove_case_responsible" in names

    def test_import_document_not_in_tool_list(self):
        names = self._get_tool_names()
        assert "import_document" not in names, (
            "Tool 'import_document' no debe estar en MCP — la importación es solo REST"
        )

    def test_get_case_has_include_movements_param(self):
        from api_gateway import http_server
        import asyncio

        captured = {}

        async def _run():
            def _capture(request_id, result):
                captured["result"] = result
                return result

            with patch.object(http_server, "create_jsonrpc_response", side_effect=_capture):
                await http_server.handle_list_tools(request_id=1)

        asyncio.get_event_loop().run_until_complete(_run())
        tools = captured.get("result", {}).get("tools", [])
        get_case_tool = next((t for t in tools if t["name"] == "get_case"), None)
        assert get_case_tool is not None
        props = get_case_tool["inputSchema"]["properties"]
        assert "include_movements" in props, (
            f"Parámetro 'include_movements' no encontrado en inputSchema de get_case. Props: {list(props.keys())}"
        )


class TestToolsFunctionSignatures:

    def test_get_case_accepts_include_movements(self):
        from api_gateway.tools.cases import get_case
        sig = inspect.signature(get_case)
        assert "include_movements" in sig.parameters, (
            "get_case no acepta include_movements"
        )
        assert sig.parameters["include_movements"].default is False

    def test_get_case_responsibles_list_signature(self):
        from api_gateway.tools.cases import get_case_responsibles_list
        sig = inspect.signature(get_case_responsibles_list)
        assert "case_id" in sig.parameters
        assert "user_id" in sig.parameters
        assert "ctx" in sig.parameters

    def test_add_case_responsible_signature(self):
        from api_gateway.tools.cases import add_case_responsible
        sig = inspect.signature(add_case_responsible)
        params = set(sig.parameters.keys())
        required = {"ctx", "case_id", "user_id", "responsible_user_id", "responsible_type", "sector_id"}
        assert required.issubset(params)

    def test_remove_case_responsible_signature(self):
        from api_gateway.tools.cases import remove_case_responsible
        sig = inspect.signature(remove_case_responsible)
        params = set(sig.parameters.keys())
        required = {"ctx", "case_id", "responsible_id", "user_id"}
        assert required.issubset(params)


class TestRestHandlersStructure:

    @pytest.mark.asyncio
    async def test_subsanar_returns_401_without_api_key(self):
        from api_gateway.rest_api import api_subsanar_document
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route

        app = Starlette(routes=[
            Route("/cases/{case_id}/subsanar", api_subsanar_document, methods=["POST"]),
        ])
        client = TestClient(app, raise_server_exceptions=False)

        with patch("api_gateway.auth_rest.validate_rest_api_key",
                   side_effect=ValueError("X-API-Key header requerido")):
            resp = client.post(
                f"/cases/{TEST_CASE_ID}/subsanar",
                json={
                    "official_document_id_erroneo": "a" * 36,
                    "official_document_id_justifica": "b" * 36,
                },
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_movements_returns_401_without_api_key(self):
        from api_gateway.rest_api import api_get_case_movements
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route

        app = Starlette(routes=[
            Route("/cases/{case_id}/movements", api_get_case_movements, methods=["GET"]),
        ])
        client = TestClient(app, raise_server_exceptions=False)

        with patch("api_gateway.auth_rest.validate_rest_api_key",
                   side_effect=ValueError("X-API-Key header requerido")):
            resp = client.get(f"/cases/{TEST_CASE_ID}/movements")

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_subsanar_validates_same_document(self):
        from api_gateway.rest_api import api_subsanar_document
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from api_gateway.context import MCPContext

        app = Starlette(routes=[
            Route("/cases/{case_id}/subsanar", api_subsanar_document, methods=["POST"]),
        ])
        client = TestClient(app, raise_server_exceptions=False)

        mock_ctx = MCPContext(
            api_key="sk-test",
            municipality_id="m1",
            schema_name=TEST_SCHEMA,
            user_id=TEST_USER_ID,
        )
        with patch("api_gateway.rest_common.validate_rest_api_key",
                   new=AsyncMock(return_value=mock_ctx)):
            same_id = "aabb1122-0000-0000-0000-000000000001"
            resp = client.post(
                f"/cases/{TEST_CASE_ID}/subsanar",
                json={
                    "official_document_id_erroneo": same_id,
                    "official_document_id_justifica": same_id,
                },
            )
        assert resp.status_code == 400
        assert "iguales" in resp.json()["error"].lower()


class TestMovementsValidationError:

    @pytest.mark.asyncio
    async def test_movements_returns_401_on_validation_error(self):
        from api_gateway.rest_api import api_get_case_movements
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from api_gateway.context import MCPContext
        from shared.exceptions import ValidationError as GDIValidationError

        app = Starlette(routes=[
            Route("/cases/{case_id}/movements", api_get_case_movements, methods=["GET"]),
        ])
        client = TestClient(app, raise_server_exceptions=False)

        mock_ctx = MCPContext(
            api_key="sk-test",
            municipality_id="m1",
            schema_name=TEST_SCHEMA,
            user_id=TEST_USER_ID,
        )

        with patch("api_gateway.rest_common.validate_rest_api_key", new=AsyncMock(return_value=mock_ctx)):
            with patch(
                "shared.utils.get_authenticated_user",
                new=AsyncMock(side_effect=GDIValidationError("Usuario no existe en este schema")),
            ):
                resp = client.get(
                    f"/cases/{TEST_CASE_ID}/movements",
                    headers={"X-API-Key": "sk-test", "X-User-ID": TEST_USER_ID},
                )

        assert resp.status_code == 401, (
            f"Esperado 401 por ValidationError, obtenido {resp.status_code}: {resp.json()}"
        )
        assert "Usuario" in resp.json()["error"] or resp.status_code == 401


class TestAddCaseResponsibleSectorValidation:

    def _make_ctx(self):
        from api_gateway.context import MCPContext
        return MCPContext(
            api_key="sk-test",
            municipality_id="m1",
            schema_name=TEST_SCHEMA,
            user_id=TEST_USER_ID,
        )

    @pytest.mark.asyncio
    async def test_rejects_sector_not_in_case(self):
        from api_gateway.tools.cases import add_case_responsible

        ctx = self._make_ctx()
        VALID_SECTOR = "sect0001-0000-0000-0000-000000000001"
        INVALID_SECTOR = "sect9999-0000-0000-0000-000000000099"

        with patch("services.cases.permissions.can_user_view_case", new=AsyncMock(return_value=True)), \
             patch("services.cases.permissions.can_user_edit_case", new=AsyncMock(return_value=True)), \
             patch("api_gateway.tools.cases.fetch_all") as mock_fetch_all:

            mock_fetch_all.side_effect = [
                [{"sector_id": VALID_SECTOR}],
                [],
            ]

            with pytest.raises(ValueError, match="[Ss]ector|no participa"):
                await add_case_responsible(
                    ctx=ctx,
                    case_id=TEST_CASE_ID,
                    user_id=TEST_USER_ID,
                    responsible_user_id="user9999-0000-0000-0000-000000000001",
                    responsible_type="ADDITIONAL",
                    sector_id=INVALID_SECTOR,
                )

    @pytest.mark.asyncio
    async def test_accepts_admin_sector(self):
        from api_gateway.tools.cases import add_case_responsible

        ctx = self._make_ctx()
        ADMIN_SECTOR = "sect0001-0000-0000-0000-000000000001"
        mock_add_result = {"responsible_id": "resp0001", "user_id": TEST_USER_ID}

        with patch("services.cases.permissions.can_user_view_case", new=AsyncMock(return_value=True)), \
             patch("services.cases.permissions.can_user_edit_case", new=AsyncMock(return_value=True)), \
             patch("api_gateway.tools.cases.fetch_all") as mock_fetch_all, \
             patch("services.cases.responsibles.add_responsible", new=AsyncMock(return_value=mock_add_result)):

            mock_fetch_all.side_effect = [
                [{"sector_id": ADMIN_SECTOR}],
                [],
            ]

            result = await add_case_responsible(
                ctx=ctx,
                case_id=TEST_CASE_ID,
                user_id=TEST_USER_ID,
                responsible_user_id="user0001-0000-0000-0000-000000000001",
                responsible_type="ADDITIONAL",
                sector_id=ADMIN_SECTOR,
            )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_accepts_assigned_sector(self):
        from api_gateway.tools.cases import add_case_responsible

        ctx = self._make_ctx()
        ADMIN_SECTOR = "sect0001-0000-0000-0000-000000000001"
        ASSIGNED_SECTOR = "sect0002-0000-0000-0000-000000000002"
        mock_add_result = {"responsible_id": "resp0002", "user_id": TEST_USER_ID}

        with patch("services.cases.permissions.can_user_view_case", new=AsyncMock(return_value=True)), \
             patch("services.cases.permissions.can_user_edit_case", new=AsyncMock(return_value=True)), \
             patch("api_gateway.tools.cases.fetch_all") as mock_fetch_all, \
             patch("services.cases.responsibles.add_responsible", new=AsyncMock(return_value=mock_add_result)):

            mock_fetch_all.side_effect = [
                [{"sector_id": ADMIN_SECTOR}],
                [{"sector_id": ASSIGNED_SECTOR}],
            ]

            result = await add_case_responsible(
                ctx=ctx,
                case_id=TEST_CASE_ID,
                user_id=TEST_USER_ID,
                responsible_user_id="user0002-0000-0000-0000-000000000002",
                responsible_type="ADDITIONAL",
                sector_id=ASSIGNED_SECTOR,
            )

        assert result["success"] is True
