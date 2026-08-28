import pytest
from unittest.mock import AsyncMock, patch

TEST_SCHEMA = "100_test"
REAL_CASE_ID = "5130f93f-28c1-4ea3-8830-19e6822ea630"
TEST_SECTOR_ID = "51000000-0000-0000-0000-000000000001"
TEST_MOVEMENT_ID = "mov12300-86c4-415a-883b-088b51ee5d45"
TEST_TASK_ID = "task1230-86c4-415a-883b-088b51ee5d45"
TEST_DB_USER_ID = "a1000000-0000-0000-0000-000000000001"

ENSURE_ASSIGNMENT_RESULT = {
    "assignment_id": TEST_MOVEMENT_ID,
    "task_id": TEST_TASK_ID,
    "sector_acronym": "LEGAL#PRIV",
    "department_name": "Legal",
    "is_new_assignment": True,
}

CLOSE_ASSIGNMENT_RESULT = {
    "movement_id": TEST_MOVEMENT_ID,
    "case_id": REAL_CASE_ID,
    "movement_type": "assignment",
    "closing_reason": "Tarea completada",
}


def _make_mock_auth_user():
    from models.schemas import AuthenticatedUser, SectorPermission
    return AuthenticatedUser(
        user_id=TEST_DB_USER_ID,
        auth_id="local_test_user",
        email="test.user@municipalidad.test",
        full_name="Usuario Test",
        permissions=[
            SectorPermission(
                sector_id=TEST_SECTOR_ID,
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


async def _fake_tenant_dispatch(self, request, call_next):
    request.state.schema_name = TEST_SCHEMA
    request.state.tenant_user_id = TEST_DB_USER_ID
    request.state.tenant_email = "test.user@municipalidad.test"
    request.state.auth_source = "testing"
    return await call_next(request)


@pytest.fixture(autouse=True)
def mock_tenant_and_auth():
    from main import app
    from auth import get_current_user
    from middleware.tenant_middleware import TenantMiddleware

    mock_auth_user = _make_mock_auth_user()

    app.dependency_overrides[get_current_user] = lambda: mock_auth_user

    with (
        patch.object(TenantMiddleware, "dispatch", _fake_tenant_dispatch),
        patch(
            "endpoints.cases.transfer_case.get_authenticated_user",
            new_callable=AsyncMock,
            return_value=TEST_DB_USER_ID,
        ),
    ):
        yield

    app.dependency_overrides.pop(get_current_user, None)


class TestAssignResponseAliasNative:

    @pytest.mark.asyncio
    async def test_assign_response_has_both_movement_id_and_assignment_id(self, client, test_headers):
        with patch("services.cases.tasks.ensure_assignment_and_create_task",
                   new=AsyncMock(return_value=dict(ENSURE_ASSIGNMENT_RESULT))):
            response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/assign",
                headers=test_headers,
                json={"target_sector_id": TEST_SECTOR_ID, "reason": "Revision legal solicitada"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["assignment_id"] == TEST_MOVEMENT_ID
        assert data["task_id"] == TEST_TASK_ID
        assert data["movement_id"] == TEST_MOVEMENT_ID
        assert data["movement_id"] == data["assignment_id"]
        print("[PASS] /assign (nativo) devuelve movement_id alias + assignment_id + task_id")


class TestCloseAssignAcceptsBothFieldNamesNative:

    @pytest.mark.asyncio
    async def test_close_assign_with_legacy_movement_id(self, client, test_headers):
        with patch("database.fetch_all", new=AsyncMock(return_value=[])), \
             patch("services.case_service.CaseService.close_assignment",
                   new=AsyncMock(return_value=dict(CLOSE_ASSIGNMENT_RESULT))):
            response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/close-assign",
                headers=test_headers,
                json={"movement_id": TEST_MOVEMENT_ID, "reason": "Tarea completada"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["movement_id"] == TEST_MOVEMENT_ID
        assert data["assignment_id"] == TEST_MOVEMENT_ID
        print("[PASS] close-assign (nativo) con movement_id legacy da 200")

    @pytest.mark.asyncio
    async def test_close_assign_with_assignment_id_alias(self, client, test_headers):
        with patch("database.fetch_all", new=AsyncMock(return_value=[])), \
             patch("services.case_service.CaseService.close_assignment",
                   new=AsyncMock(return_value=dict(CLOSE_ASSIGNMENT_RESULT))) as mock_close:
            response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/close-assign",
                headers=test_headers,
                json={"assignment_id": TEST_MOVEMENT_ID, "reason": "Tarea completada"},
            )

        assert response.status_code == 200
        assert mock_close.call_args.kwargs["movement_id"] == TEST_MOVEMENT_ID
        print("[PASS] close-assign (nativo) con assignment_id alias da 200")

    @pytest.mark.asyncio
    async def test_close_assign_missing_both_returns_422(self, client, test_headers):
        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/close-assign",
            headers=test_headers,
            json={"reason": "Tarea completada"},
        )

        assert response.status_code == 422
        print("[PASS] close-assign (nativo) sin movement_id ni assignment_id da 422")


class TestAssignThenCloseAssignChainedNative:

    @pytest.mark.asyncio
    async def test_chained_assign_then_close_assign_using_raw_assign_response(self, client, test_headers):
        with patch("services.cases.tasks.ensure_assignment_and_create_task",
                   new=AsyncMock(return_value=dict(ENSURE_ASSIGNMENT_RESULT))):
            assign_response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/assign",
                headers=test_headers,
                json={"target_sector_id": TEST_SECTOR_ID, "reason": "Revision legal solicitada"},
            )
        assert assign_response.status_code == 200
        assign_data = assign_response.json()["data"]

        with patch("database.fetch_all", new=AsyncMock(return_value=[])), \
             patch("services.case_service.CaseService.close_assignment",
                   new=AsyncMock(return_value=dict(CLOSE_ASSIGNMENT_RESULT))):
            close_response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/close-assign",
                headers=test_headers,
                json={"assignment_id": assign_data["assignment_id"], "reason": "Tarea completada"},
            )

        assert close_response.status_code == 200
        assert close_response.json()["data"]["movement_id"] == TEST_MOVEMENT_ID
        print("[PASS] assign -> close-assign encadenado (nativo) con assignment_id OK")
