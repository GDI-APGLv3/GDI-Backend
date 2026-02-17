"""
Tests de integracion para endpoints principales de expedientes.
GET /api/v1/cases/, GET /api/v1/cases/{case_id}, GET /api/v1/cases/templates
Conecta a BD real (dev-test, schema 100_test).
"""
import pytest

REAL_CASE_ID = "f615d581-a73a-48fc-b4e5-363938da3e1c"
NONEXISTENT_CASE_ID = "00000000-0000-0000-0000-000000000000"


class TestCasesMainEndpoints:
    """Tests de integracion para endpoints principales de expedientes."""

    @pytest.mark.asyncio
    async def test_list_cases_success(self, client, test_headers):
        """GET /api/v1/cases/ con test_headers debe dar 200."""
        response = await client.get(
            "/api/v1/cases/",
            headers=test_headers,
            params={"page": 1, "page_size": 20, "status": "active"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] GET /api/v1/cases/ responde 200")

    @pytest.mark.asyncio
    async def test_list_cases_with_search(self, client, test_headers):
        """GET /api/v1/cases/?search=... con test_headers debe dar 200."""
        response = await client.get(
            "/api/v1/cases/",
            headers=test_headers,
            params={"search": "comercial", "page": 1},
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        print(f"[PASS] GET /api/v1/cases/?search=comercial responde 200")

    @pytest.mark.asyncio
    async def test_list_cases_no_tenant(self, client):
        """GET /api/v1/cases/ sin X-Tenant-Schema debe dar 400."""
        response = await client.get("/api/v1/cases/")

        assert response.status_code == 400
        print(f"[PASS] GET /api/v1/cases/ sin tenant da 400")

    @pytest.mark.asyncio
    async def test_list_cases_no_user(self, client):
        """GET /api/v1/cases/ con tenant pero sin X-User-ID debe dar 400."""
        headers = {"X-Tenant-Schema": "100_test"}
        response = await client.get("/api/v1/cases/", headers=headers)

        # El middleware rechaza con 400 cuando falta X-User-ID en TESTING_MODE
        assert response.status_code == 400
        print(f"[PASS] GET /api/v1/cases/ sin user da 400")

    @pytest.mark.asyncio
    async def test_get_case_detail_success(self, client, test_headers):
        """GET /api/v1/cases/{case_id} con case_id real debe dar 200."""
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] GET /api/v1/cases/{REAL_CASE_ID} responde 200")

    @pytest.mark.asyncio
    async def test_get_case_detail_not_found(self, client, test_headers):
        """GET /api/v1/cases/{case_id} con ID inexistente."""
        response = await client.get(
            f"/api/v1/cases/{NONEXISTENT_CASE_ID}",
            headers=test_headers,
        )

        if response.status_code == 404:
            print(f"[PASS] Case inexistente da 404")
        else:
            data = response.json()
            assert data.get("success") is False or response.status_code in (400, 403, 404)
            print(f"[PASS] Case inexistente da {response.status_code}")

    @pytest.mark.asyncio
    async def test_get_case_templates_success(self, client, test_headers):
        """GET /api/v1/cases/templates debe dar 200 con templates."""
        response = await client.get(
            "/api/v1/cases/templates",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] GET /api/v1/cases/templates responde 200")
