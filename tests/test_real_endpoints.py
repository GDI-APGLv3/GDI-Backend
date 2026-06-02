"""
Tests de integracion reales para endpoints de expedientes.
Conecta a BD real (dev-test, schema 100_test).
Sin mocks: TESTING_MODE bypassa auth y usa datos reales.
"""
import pytest

# Datos reales en BD dev-test (schema 100_test)
REAL_CASE_ID = "5130f93f-28c1-4ea3-8830-19e6822ea630"
REAL_CASE_NUMBER = "EE-2026-000005-MDEV-INTE"


class TestRealCasesEndpoints:
    """Tests de integracion contra endpoints reales de expedientes."""

    @pytest.mark.asyncio
    async def test_list_cases(self, client, test_headers):
        """GET /api/v1/cases/ con test_headers debe dar 200 con estructura correcta."""
        response = await client.get("/api/v1/cases/", headers=test_headers)

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        # La estructura debe tener cases (lista) y algun tipo de paginacion
        cases_data = data["data"]
        assert isinstance(cases_data, dict) or isinstance(cases_data, list)
        print(f"[PASS] GET /api/v1/cases/ responde 200, success=True")

    @pytest.mark.asyncio
    async def test_list_cases_with_search(self, client, test_headers):
        """GET /api/v1/cases/?search=comercial debe dar 200."""
        response = await client.get(
            "/api/v1/cases/",
            headers=test_headers,
            params={"search": "comercial"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        print(f"[PASS] GET /api/v1/cases/?search=comercial responde 200")

    @pytest.mark.asyncio
    async def test_get_case_detail(self, client, test_headers):
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
    async def test_get_case_by_number(self, client, test_headers):
        """GET /api/v1/cases/number/{case_number} con numero real debe dar 200."""
        response = await client.get(
            f"/api/v1/cases/number/{REAL_CASE_NUMBER}",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] GET /api/v1/cases/number/{REAL_CASE_NUMBER} responde 200")

    @pytest.mark.asyncio
    async def test_case_templates(self, client, test_headers):
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

    @pytest.mark.asyncio
    async def test_unauthorized_no_tenant(self, client):
        """GET /api/v1/cases/ sin headers debe dar 400."""
        response = await client.get("/api/v1/cases/")

        assert response.status_code == 400
        print(f"[PASS] GET /api/v1/cases/ sin headers da 400")
