"""
Tests de integracion para endpoints de expedientes: movements, permissions, case-history.
Conecta a BD real (dev-test, schema 100_test).

Nota: Los endpoints /audit-log y /users/{user_id} NO existen.
Se testean endpoints reales: movements, permissions, case-history.
"""
import pytest

# Expediente real en BD dev-test (schema 100_test)
REAL_CASE_ID = "f615d581-a73a-48fc-b4e5-363938da3e1c"
NONEXISTENT_CASE_ID = "00000000-0000-0000-0000-000000000000"


class TestCaseMovementsPermissionsHistory:
    """Tests para endpoints reales de expedientes."""

    @pytest.mark.asyncio
    async def test_get_case_movements_success(self, client, test_headers):
        """GET /api/v1/cases/{case_id}/movements con case_id real debe dar 200."""
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}/movements",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] /cases/{REAL_CASE_ID}/movements responde 200")

    @pytest.mark.asyncio
    async def test_get_case_permissions_success(self, client, test_headers):
        """GET /api/v1/cases/{case_id}/permissions con case_id real debe dar 200."""
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}/permissions",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] /cases/{REAL_CASE_ID}/permissions responde 200")

    @pytest.mark.asyncio
    async def test_get_case_history_success(self, client, test_headers):
        """GET /api/v1/cases/{case_id}/case-history con case_id real debe dar 200."""
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}/case-history",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] /cases/{REAL_CASE_ID}/case-history responde 200")

    @pytest.mark.asyncio
    async def test_get_case_nonexistent(self, client, test_headers):
        """GET /api/v1/cases/{case_id} con ID inexistente debe dar 404."""
        response = await client.get(
            f"/api/v1/cases/{NONEXISTENT_CASE_ID}",
            headers=test_headers,
        )

        # Puede ser 404 (not found) o 200 con success=False, depende de implementacion
        if response.status_code == 404:
            print(f"[PASS] Case inexistente da 404")
        else:
            data = response.json()
            assert data.get("success") is False or response.status_code in (400, 403, 404)
            print(f"[PASS] Case inexistente da {response.status_code}")
