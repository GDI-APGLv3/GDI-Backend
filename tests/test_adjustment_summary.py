import pytest


class TestAdjustmentSummary:

    def test_adjustments_summary(self):

        summary = {
            "testing_mode": "TESTING_MODE con X-Tenant-Schema + X-User-ID",
            "schema": "100_test",
            "user": "a1000000-0000-0000-0000-000000000001 (Maria Rodriguez)",
            "bd": "dev-test (caboose)",
            "auth_bypass": "TESTING_MODE bypassa Auth0 completamente",
            "tests_tipo": "Integracion contra BD real, sin mocks",
        }

        print("\n" + "=" * 60)
        print("RESUMEN DE CONFIGURACION DE TESTS")
        print("=" * 60)
        for key, value in summary.items():
            print(f"  {key}: {value}")
        print("=" * 60)

        assert True

    @pytest.mark.asyncio
    async def test_system_endpoints_working(self, client, test_headers):

        response = await client.get("/livez", headers=test_headers)
        assert response.status_code == 200
        print(f"[PASS] /livez responde 200")

        response = await client.get("/favicon.ico", headers=test_headers)
        assert response.status_code == 200
        print(f"[PASS] /favicon.ico responde 200")
