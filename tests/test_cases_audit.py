import pytest

REAL_CASE_ID = "5130f93f-28c1-4ea3-8830-19e6822ea630"
NONEXISTENT_CASE_ID = "00000000-0000-0000-0000-000000000000"


class TestCaseMovementsPermissionsHistory:

    @pytest.mark.asyncio
    async def test_get_case_movements_success(self, client, test_headers):
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
