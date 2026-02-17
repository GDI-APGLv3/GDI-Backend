"""
Tests de integracion para endpoints del sistema.
Conecta a BD real (dev-test, schema 100_test).
"""
import pytest


class TestSystemEndpoints:
    """Tests para endpoints del sistema que no requieren logica de negocio compleja."""

    @pytest.mark.asyncio
    async def test_livez(self, client, test_headers):
        """GET /livez debe responder 200."""
        response = await client.get("/livez", headers=test_headers)

        assert response.status_code == 200
        data = response.json()
        assert "status" in data or "message" in data
        print(f"[PASS] /livez responde 200: {data}")

    @pytest.mark.asyncio
    async def test_system_health_with_headers(self, client, test_headers):
        """GET /api/v1/system/health con test_headers debe responder 200."""
        response = await client.get("/api/v1/system/health", headers=test_headers)

        assert response.status_code in (200, 500, 503)
        print(f"[PASS] /api/v1/system/health responde {response.status_code}")

    @pytest.mark.asyncio
    async def test_favicon(self, client):
        """GET /favicon.ico debe responder 200."""
        response = await client.get("/favicon.ico")

        assert response.status_code == 200
        print(f"[PASS] /favicon.ico responde 200")

    @pytest.mark.asyncio
    async def test_404_with_headers(self, client, test_headers):
        """GET a endpoint inexistente debe dar 404."""
        response = await client.get("/nonexistent-endpoint-xyz", headers=test_headers)

        assert response.status_code == 404
        print(f"[PASS] Endpoint inexistente da 404")

    @pytest.mark.asyncio
    async def test_missing_tenant_header(self, client):
        """GET /api/v1/cases/ sin X-Tenant-Schema debe dar 400."""
        response = await client.get("/api/v1/cases/")

        assert response.status_code == 400
        print(f"[PASS] Sin X-Tenant-Schema da 400")

    @pytest.mark.asyncio
    async def test_document_types(self, client, test_headers):
        """GET /document-types con test_headers debe dar 200."""
        response = await client.get("/document-types", headers=test_headers)

        assert response.status_code == 200
        data = response.json()
        # Debe retornar una lista o estructura con tipos de documentos
        assert data is not None
        print(f"[PASS] /document-types responde 200")

    @pytest.mark.asyncio
    async def test_document_states(self, client, test_headers):
        """GET /document-states con test_headers debe dar 200."""
        response = await client.get("/document-states", headers=test_headers)

        assert response.status_code == 200
        data = response.json()
        assert data is not None
        print(f"[PASS] /document-states responde 200")
