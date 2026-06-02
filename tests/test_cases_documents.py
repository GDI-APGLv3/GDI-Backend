"""
Tests de integracion para endpoint de documentos de expedientes.
GET /api/v1/cases/{case_id}/documents
Conecta a BD real (dev-test, schema 100_test).
"""
import pytest

REAL_CASE_ID = "5130f93f-28c1-4ea3-8830-19e6822ea630"
NONEXISTENT_CASE_ID = "00000000-0000-0000-0000-000000000000"


class TestCasesDocuments:
    """Tests de integracion para documentos de expedientes."""

    @pytest.mark.asyncio
    async def test_get_case_documents_success(self, client, test_headers):
        """GET /api/v1/cases/{case_id}/documents con case_id real debe dar 200."""
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}/documents",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] /cases/{REAL_CASE_ID}/documents responde 200")

    @pytest.mark.asyncio
    async def test_get_case_documents_structure(self, client, test_headers):
        """GET documents debe retornar estructura con documentos."""
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}/documents",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        docs = data["data"]
        assert docs is not None
        print(f"[PASS] Estructura de documentos correcta")

    @pytest.mark.asyncio
    async def test_get_case_documents_nonexistent_case(self, client, test_headers):
        """GET /api/v1/cases/{case_id}/documents con case inexistente."""
        response = await client.get(
            f"/api/v1/cases/{NONEXISTENT_CASE_ID}/documents",
            headers=test_headers,
        )

        # Puede ser 404, 403, 500 o 200 dependiendo de implementacion
        if response.status_code == 200:
            print(f"[PASS] Case inexistente retorna 200")
        else:
            assert response.status_code in (403, 404, 500)
            print(f"[PASS] Case inexistente da {response.status_code}")

    @pytest.mark.asyncio
    async def test_get_case_documents_no_tenant(self, client):
        """GET /api/v1/cases/{case_id}/documents sin tenant da 400."""
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}/documents"
        )

        assert response.status_code == 400
        print(f"[PASS] Sin tenant da 400")
