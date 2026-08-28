from unittest.mock import AsyncMock, patch

import pytest

TEST_SCHEMA = "100_test"
TEST_CITIZEN_ID = "c1000000-0000-0000-0000-000000000001"
TEST_CASE_ID = "ca000000-0000-0000-0000-000000000001"


class TestTadF2RoutesRegistered:
    def _get_routes(self):
        from api_gateway.http_server import routes as gateway_routes
        result = {}
        for route in gateway_routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                for method in (route.methods or []):
                    result[(route.path, method)] = getattr(route, "endpoint", None)
        return result

    def test_case_templates_route(self):
        routes = self._get_routes()
        assert ("/api/v1/tad/case-templates", "GET") in routes

    def test_create_case_route(self):
        routes = self._get_routes()
        assert ("/api/v1/tad/cases", "POST") in routes

    def test_list_cases_route(self):
        routes = self._get_routes()
        assert ("/api/v1/tad/cases", "GET") in routes

    def test_case_detail_route(self):
        routes = self._get_routes()
        assert ("/api/v1/tad/cases/{id}", "GET") in routes


class TestPrivateCitizenSharesRoutesRegistered:
    def _get_paths(self):
        import main  # noqa: F401 (fuerza carga de include_endpoints)
        return {(r.path, m) for r in main.app.routes if hasattr(r, "path") for m in (getattr(r, "methods", None) or [])}

    def test_get_citizen_shares(self):
        assert ("/api/v1/cases/{case_id}/citizen-shares", "GET") in self._get_paths()

    def test_post_citizen_shares(self):
        assert ("/api/v1/cases/{case_id}/citizen-shares", "POST") in self._get_paths()

    def test_delete_citizen_shares(self):
        assert ("/api/v1/cases/{case_id}/citizen-shares/{citizen_id}", "DELETE") in self._get_paths()

    def test_citizens_search(self):
        assert ("/api/v1/citizens/search", "GET") in self._get_paths()


class TestF2HandlersExported:
    def test_handlers_async(self):
        import inspect
        from api_gateway.rest_api_tad import (
            api_tad_get_case_templates,
            api_tad_create_case,
            api_tad_get_cases,
            api_tad_get_case_detail,
        )
        for fn in (api_tad_get_case_templates, api_tad_create_case, api_tad_get_cases, api_tad_get_case_detail):
            assert callable(fn)
            assert inspect.iscoroutinefunction(fn)


class TestCaseDetailGate:
    @pytest.mark.asyncio
    async def test_sin_share_activo_devuelve_404_generico(self):
        from api_gateway import rest_api_tad as tad_module
        from starlette.requests import Request

        with patch.object(tad_module, "validate_tad_api_key", new_callable=AsyncMock) as mock_auth, \
             patch("services.cases.citizen_shares.can_citizen_access_case", new_callable=AsyncMock) as mock_gate:
            mock_auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            mock_gate.return_value = False

            scope = {
                "type": "http", "method": "GET",
                "path": f"/api/v1/tad/cases/{TEST_CASE_ID}",
                "path_params": {"id": TEST_CASE_ID},
                "headers": [(b"x-api-key", b"sk-x"), (b"x-citizen-id", TEST_CITIZEN_ID.encode())],
            }
            request = Request(scope)
            response = await tad_module.api_tad_get_case_detail(request)
            assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_id_malformado_devuelve_404(self):
        from api_gateway import rest_api_tad as tad_module
        from starlette.requests import Request

        with patch.object(tad_module, "validate_tad_api_key", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            scope = {
                "type": "http", "method": "GET",
                "path": "/api/v1/tad/cases/no-es-uuid",
                "path_params": {"id": "no-es-uuid"},
                "headers": [(b"x-api-key", b"sk-x"), (b"x-citizen-id", TEST_CITIZEN_ID.encode())],
            }
            request = Request(scope)
            response = await tad_module.api_tad_get_case_detail(request)
            assert response.status_code == 404
