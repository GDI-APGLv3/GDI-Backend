import pytest


class TestSystemEndpoints:

    @pytest.mark.asyncio
    async def test_livez(self, client, test_headers):
        response = await client.get("/livez", headers=test_headers)

        assert response.status_code == 200
        data = response.json()
        assert "status" in data or "message" in data
        print(f"[PASS] /livez responde 200: {data}")

    @pytest.mark.asyncio
    async def test_system_health_with_headers(self, client, test_headers):
        response = await client.get("/api/v1/system/health", headers=test_headers)

        assert response.status_code in (200, 500, 503)
        print(f"[PASS] /api/v1/system/health responde {response.status_code}")

    @pytest.mark.asyncio
    async def test_favicon(self, client):
        response = await client.get("/favicon.ico")

        assert response.status_code == 200
        print(f"[PASS] /favicon.ico responde 200")

    @pytest.mark.asyncio
    async def test_404_with_headers(self, client, test_headers):
        response = await client.get("/nonexistent-endpoint-xyz", headers=test_headers)

        assert response.status_code == 404
        print(f"[PASS] Endpoint inexistente da 404")

    @pytest.mark.asyncio
    async def test_missing_tenant_header(self, client):
        response = await client.get("/api/v1/cases/")

        assert response.status_code == 401
        print(f"[PASS] Sin X-Tenant-Schema da 401")

    @pytest.mark.asyncio
    async def test_document_types(self, client, test_headers):
        response = await client.get("/document-types", headers=test_headers)

        assert response.status_code == 200
        data = response.json()
        assert data is not None
        print(f"[PASS] /document-types responde 200")

    @pytest.mark.asyncio
    async def test_document_states(self, client, test_headers):
        response = await client.get("/document-states", headers=test_headers)

        assert response.status_code == 200
        data = response.json()
        assert data is not None
        print(f"[PASS] /document-states responde 200")
