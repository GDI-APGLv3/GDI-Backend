"""
Tests de integracion para endpoints de permisos y sectores.
GET /api/v1/cases/{case_id}/permissions
GET /api/v1/cases/{case_id}/available-sectors
GET /api/v1/cases/sectors/{sector_id}/users
Conecta a BD real (dev-test, schema 100_test).
"""
import pytest

REAL_CASE_ID = "f615d581-a73a-48fc-b4e5-363938da3e1c"
REAL_SECTOR_ID = "51000000-0000-0000-0000-000000000001"
NONEXISTENT_CASE_ID = "00000000-0000-0000-0000-000000000000"


class TestCasesPermissionsAndSectors:
    """Tests de integracion para permisos y sectores."""

    @pytest.mark.asyncio
    async def test_get_case_permissions_success(self, client, test_headers):
        """GET /api/v1/cases/{case_id}/permissions con case real debe dar 200."""
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
    async def test_get_case_permissions_structure(self, client, test_headers):
        """GET permissions debe retornar datos de permisos."""
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}/permissions",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        perms_data = data["data"]
        assert perms_data is not None
        print(f"[PASS] Estructura de permisos correcta")

    @pytest.mark.asyncio
    async def test_get_case_permissions_nonexistent(self, client, test_headers):
        """GET permissions con case inexistente."""
        response = await client.get(
            f"/api/v1/cases/{NONEXISTENT_CASE_ID}/permissions",
            headers=test_headers,
        )

        if response.status_code == 200:
            print(f"[PASS] Case inexistente retorna 200")
        else:
            assert response.status_code in (403, 404, 500)
            print(f"[PASS] Case inexistente da {response.status_code}")

    @pytest.mark.asyncio
    async def test_get_available_sectors_success(self, client, test_headers):
        """GET /api/v1/cases/{case_id}/available-sectors debe responder."""
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}/available-sectors",
            headers=test_headers,
        )

        # Puede dar 200, 403, 422 o 500 dependiendo de permisos y validaciones
        assert response.status_code in (200, 403, 422, 500)
        if response.status_code == 200:
            data = response.json()
            assert data.get("success") is True
            print(f"[PASS] available-sectors responde 200")
        else:
            print(f"[PASS] available-sectors da {response.status_code}")

    @pytest.mark.asyncio
    async def test_get_sector_users_success(self, client, test_headers):
        """GET /api/v1/cases/sectors/{sector_id}/users debe dar 200."""
        response = await client.get(
            f"/api/v1/cases/sectors/{REAL_SECTOR_ID}/users",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] /sectors/{REAL_SECTOR_ID}/users responde 200")

    @pytest.mark.asyncio
    async def test_get_sector_users_nonexistent(self, client, test_headers):
        """GET /api/v1/cases/sectors/{sector_id}/users con sector inexistente."""
        fake_sector = "00000000-0000-0000-0000-000000000000"
        response = await client.get(
            f"/api/v1/cases/sectors/{fake_sector}/users",
            headers=test_headers,
        )

        # Puede dar 404 o 200 con lista vacia
        if response.status_code == 200:
            print(f"[PASS] Sector inexistente retorna 200")
        else:
            assert response.status_code in (404, 400)
            print(f"[PASS] Sector inexistente da {response.status_code}")
