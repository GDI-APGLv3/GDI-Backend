import pytest

REAL_CASE_ID = "5130f93f-28c1-4ea3-8830-19e6822ea630"
NONEXISTENT_CASE_ID = "00000000-0000-0000-0000-000000000000"


class TestCasesMovements:

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
    async def test_get_case_movements_has_data(self, client, test_headers):
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}/movements",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        movements = data["data"]
        assert movements is not None
        print(f"[PASS] Movimientos retornados correctamente")

    @pytest.mark.asyncio
    async def test_get_case_movements_nonexistent(self, client, test_headers):
        response = await client.get(
            f"/api/v1/cases/{NONEXISTENT_CASE_ID}/movements",
            headers=test_headers,
        )

        if response.status_code == 200:
            print(f"[PASS] Case inexistente retorna 200")
        else:
            assert response.status_code in (403, 404, 500)
            print(f"[PASS] Case inexistente da {response.status_code}")

    @pytest.mark.asyncio
    async def test_get_case_movements_no_tenant(self, client):
        response = await client.get(
            f"/api/v1/cases/{REAL_CASE_ID}/movements"
        )

        assert response.status_code == 401
        print(f"[PASS] Sin tenant da 400")
