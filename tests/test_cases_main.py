import pytest

REAL_CASE_ID = "5130f93f-28c1-4ea3-8830-19e6822ea630"
NONEXISTENT_CASE_ID = "00000000-0000-0000-0000-000000000000"


class TestCasesMainEndpoints:

    @pytest.mark.asyncio
    async def test_list_cases_success(self, client, test_headers):
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
        response = await client.get("/api/v1/cases/")

        assert response.status_code == 401
        print(f"[PASS] GET /api/v1/cases/ sin tenant da 401")

    @pytest.mark.asyncio
    async def test_list_cases_no_user(self, client):
        headers = {"X-Tenant-Schema": "100_test"}
        response = await client.get("/api/v1/cases/", headers=headers)

        assert response.status_code == 401
        print(f"[PASS] GET /api/v1/cases/ sin user da 401")

    @pytest.mark.asyncio
    async def test_get_case_detail_success(self, client, test_headers):
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
        response = await client.get(
            "/api/v1/cases/templates",
            headers=test_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        assert "data" in data
        print(f"[PASS] GET /api/v1/cases/templates responde 200")
