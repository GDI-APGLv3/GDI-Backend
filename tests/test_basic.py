import pytest


class TestBasicFunctionality:

    @pytest.mark.asyncio
    async def test_livez_endpoint(self, client, test_headers):
        response = await client.get("/livez", headers=test_headers)

        assert response.status_code == 200
        data = response.json()
        assert "status" in data or "message" in data
        print(f"[PASS] /livez responde 200: {data}")

    @pytest.mark.asyncio
    async def test_system_health(self, client, test_headers):
        response = await client.get("/api/v1/system/health", headers=test_headers)

        assert response.status_code in (200, 503), (
            f"/api/v1/system/health devolvió {response.status_code}. "
            f"500 = bug interno (no debe aceptarse). Respuesta: {response.text[:200]}"
        )
        print(f"[PASS] /api/v1/system/health responde {response.status_code}")

    @pytest.mark.asyncio
    async def test_favicon(self, client, test_headers):
        response = await client.get("/favicon.ico", headers=test_headers)

        assert response.status_code == 200
        print(f"[PASS] /favicon.ico responde 200")

    @pytest.mark.asyncio
    async def test_cases_list_requires_tenant(self, client):
        response = await client.get("/api/v1/cases/")

        assert response.status_code == 401
        print(f"[PASS] /api/v1/cases/ sin credenciales da 401")

    @pytest.mark.asyncio
    async def test_cases_list_authenticated(self, client, test_headers):
        response = await client.get("/api/v1/cases/", headers=test_headers)

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        print(f"[PASS] /api/v1/cases/ con TESTING_MODE da 200, success=True")

    @pytest.mark.asyncio
    async def test_case_templates(self, client, test_headers):
        response = await client.get("/api/v1/cases/templates", headers=test_headers)

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] /api/v1/cases/templates responde 200 con templates")
