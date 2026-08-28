import pytest
from unittest.mock import patch

REAL_CASE_ID = "5130f93f-28c1-4ea3-8830-19e6822ea630"
TEST_DB_USER_ID = "a1000000-0000-0000-0000-000000000001"


async def _fake_tenant_dispatch(self, request, call_next):
    from starlette.responses import JSONResponse

    schema_name = request.headers.get("X-Tenant-Schema")
    if not schema_name:
        return JSONResponse(
            status_code=400,
            content={"detail": "Header 'X-Tenant-Schema' es requerido para acceder a este recurso"},
        )

    request.state.schema_name = schema_name
    request.state.tenant_user_id = TEST_DB_USER_ID
    request.state.tenant_email = "test.user@municipalidad.test"
    request.state.auth_source = "testing"
    return await call_next(request)


@pytest.fixture(autouse=True)
def mock_tenant_and_auth():
    from main import app
    from auth import get_current_user
    from middleware.tenant_middleware import TenantMiddleware
    from models.schemas import AuthenticatedUser, SectorPermission

    mock_auth_user = AuthenticatedUser(
        user_id=TEST_DB_USER_ID,
        auth_id="local_test_user",
        email="test.user@municipalidad.test",
        full_name="Usuario Test",
        permissions=[
            SectorPermission(
                sector_id="51000000-0000-0000-0000-000000000001",
                sector_acronym="PRIV",
                department_id="d1000000-0000-0000-0000-000000000001",
                department_name="Intendencia",
                department_acronym="INTE",
                can_view=True,
                can_edit=True,
                is_primary=True,
            )
        ],
    )

    app.dependency_overrides[get_current_user] = lambda: mock_auth_user

    with patch.object(TenantMiddleware, "dispatch", _fake_tenant_dispatch):
        app.middleware_stack = None
        yield

    app.middleware_stack = None
    app.dependency_overrides.pop(get_current_user, None)


class TestTransferEndpoints:

    @pytest.mark.asyncio
    async def test_transfer_validation_reason_too_short(self, client, test_headers):
        transfer_data = {
            "target_sector_id": "51000000-0000-0000-0000-000000000001",
            "reason": "muy",
            "transfer_ownership": True,
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/transfer",
            headers=test_headers,
            json=transfer_data,
        )

        assert response.status_code == 422
        print(f"[PASS] Transfer con reason corto da 422")

    @pytest.mark.asyncio
    async def test_transfer_validation_missing_target(self, client, test_headers):
        transfer_data = {
            "reason": "Motivo valido de transferencia",
            "transfer_ownership": True,
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/transfer",
            headers=test_headers,
            json=transfer_data,
        )

        assert response.status_code == 422
        print(f"[PASS] Transfer sin target da 422")

    @pytest.mark.asyncio
    async def test_assign_validation_missing_reason(self, client, test_headers):
        assign_data = {
            "target_sector_id": "51000000-0000-0000-0000-000000000001",
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/assign",
            headers=test_headers,
            json=assign_data,
        )

        assert response.status_code == 422
        print(f"[PASS] Assign sin reason da 422")

    @pytest.mark.asyncio
    async def test_assign_validation_reason_too_short(self, client, test_headers):
        assign_data = {
            "target_sector_id": "51000000-0000-0000-0000-000000000001",
            "reason": "ab",
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/assign",
            headers=test_headers,
            json=assign_data,
        )

        assert response.status_code == 422
        print(f"[PASS] Assign con reason corto da 422")

    @pytest.mark.asyncio
    async def test_transfer_no_tenant(self, client):
        transfer_data = {
            "target_sector_id": "51000000-0000-0000-0000-000000000001",
            "reason": "Motivo valido para transferencia",
            "transfer_ownership": True,
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/transfer",
            json=transfer_data,
        )

        assert response.status_code == 400
        print(f"[PASS] Transfer sin tenant da 400")

    @pytest.mark.asyncio
    async def test_assign_no_tenant(self, client):
        assign_data = {
            "target_sector_id": "51000000-0000-0000-0000-000000000001",
            "reason": "Motivo valido para asignacion",
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/assign",
            json=assign_data,
        )

        assert response.status_code == 400
        print(f"[PASS] Assign sin tenant da 400")
