import pytest
from unittest.mock import AsyncMock, patch
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from api_gateway.context import MCPContext

TEST_SCHEMA = "100_test"
TEST_USER_ID = "a1000000-0000-0000-0000-000000000001"
TEST_CASE_ID = "ef102207-86c4-415a-883b-088b51ee5d45"
TEST_SECTOR_ID = "51000000-0000-0000-0000-000000000002"
TEST_MOVEMENT_ID = "mov12300-86c4-415a-883b-088b51ee5d45"
TEST_TASK_ID = "task1230-86c4-415a-883b-088b51ee5d45"

MOCK_CTX = MCPContext(
    api_key="sk-test",
    municipality_id="m1",
    schema_name=TEST_SCHEMA,
    user_id=TEST_USER_ID,
)


def _build_app():
    from api_gateway.rest_api import api_assign_case, api_close_assignment
    return Starlette(routes=[
        Route("/cases/{case_id}/assign", api_assign_case, methods=["POST"]),
        Route("/cases/{case_id}/close-assign", api_close_assignment, methods=["POST"]),
    ])


ENSURE_ASSIGNMENT_RESULT = {
    "assignment_id": TEST_MOVEMENT_ID,
    "task_id": TEST_TASK_ID,
    "sector_acronym": "LEGAL#PRIV",
    "department_name": "Legal",
    "is_new_assignment": True,
}

CLOSE_ASSIGNMENT_RESULT = {
    "movement_id": TEST_MOVEMENT_ID,
    "case_id": TEST_CASE_ID,
    "movement_type": "assignment",
    "closing_reason": "Tarea completada",
}


class TestAssignResponseAlias:

    @pytest.mark.asyncio
    async def test_assign_response_has_both_movement_id_and_assignment_id(self):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)

        with patch("api_gateway.rest_common.validate_rest_api_key", new=AsyncMock(return_value=MOCK_CTX)), \
             patch("services.cases.tasks.ensure_assignment_and_create_task",
                   new=AsyncMock(return_value=dict(ENSURE_ASSIGNMENT_RESULT))):
            resp = client.post(
                f"/cases/{TEST_CASE_ID}/assign",
                headers={"X-API-Key": "sk-test", "X-User-ID": TEST_USER_ID},
                json={"target_sector_id": TEST_SECTOR_ID, "reason": "Revision legal solicitada"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["assignment_id"] == TEST_MOVEMENT_ID
        assert data["task_id"] == TEST_TASK_ID
        assert data["movement_id"] == TEST_MOVEMENT_ID
        assert data["movement_id"] == data["assignment_id"]


class TestCloseAssignAcceptsBothFieldNames:

    @pytest.mark.asyncio
    async def test_close_assign_with_legacy_movement_id(self):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)

        with patch("api_gateway.rest_common.validate_rest_api_key", new=AsyncMock(return_value=MOCK_CTX)), \
             patch("services.case_service.CaseService.close_assignment",
                   new=AsyncMock(return_value=dict(CLOSE_ASSIGNMENT_RESULT))), \
             patch("api_gateway.tools.cases.fetch_all", new=AsyncMock(return_value=[])):
            resp = client.post(
                f"/cases/{TEST_CASE_ID}/close-assign",
                headers={"X-API-Key": "sk-test", "X-User-ID": TEST_USER_ID},
                json={"movement_id": TEST_MOVEMENT_ID, "reason": "Tarea completada"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["movement_id"] == TEST_MOVEMENT_ID
        assert data["assignment_id"] == TEST_MOVEMENT_ID

    @pytest.mark.asyncio
    async def test_close_assign_with_assignment_id_alias(self):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)

        with patch("api_gateway.rest_common.validate_rest_api_key", new=AsyncMock(return_value=MOCK_CTX)), \
             patch("services.case_service.CaseService.close_assignment",
                   new=AsyncMock(return_value=dict(CLOSE_ASSIGNMENT_RESULT))) as mock_close, \
             patch("api_gateway.tools.cases.fetch_all", new=AsyncMock(return_value=[])):
            resp = client.post(
                f"/cases/{TEST_CASE_ID}/close-assign",
                headers={"X-API-Key": "sk-test", "X-User-ID": TEST_USER_ID},
                json={"assignment_id": TEST_MOVEMENT_ID, "reason": "Tarea completada"},
            )

        assert resp.status_code == 200
        assert mock_close.call_args.kwargs["movement_id"] == TEST_MOVEMENT_ID

    @pytest.mark.asyncio
    async def test_close_assign_missing_both_returns_400(self):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)

        with patch("api_gateway.rest_common.validate_rest_api_key", new=AsyncMock(return_value=MOCK_CTX)):
            resp = client.post(
                f"/cases/{TEST_CASE_ID}/close-assign",
                headers={"X-API-Key": "sk-test", "X-User-ID": TEST_USER_ID},
                json={"reason": "Tarea completada"},
            )

        assert resp.status_code == 400


class TestAssignThenCloseAssignChained:

    @pytest.mark.asyncio
    async def test_chained_assign_then_close_assign_using_raw_assign_response(self):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)

        with patch("api_gateway.rest_common.validate_rest_api_key", new=AsyncMock(return_value=MOCK_CTX)), \
             patch("services.cases.tasks.ensure_assignment_and_create_task",
                   new=AsyncMock(return_value=dict(ENSURE_ASSIGNMENT_RESULT))):
            assign_resp = client.post(
                f"/cases/{TEST_CASE_ID}/assign",
                headers={"X-API-Key": "sk-test", "X-User-ID": TEST_USER_ID},
                json={"target_sector_id": TEST_SECTOR_ID, "reason": "Revision legal solicitada"},
            )
        assert assign_resp.status_code == 200
        assign_data = assign_resp.json()

        with patch("api_gateway.rest_common.validate_rest_api_key", new=AsyncMock(return_value=MOCK_CTX)), \
             patch("services.case_service.CaseService.close_assignment",
                   new=AsyncMock(return_value=dict(CLOSE_ASSIGNMENT_RESULT))), \
             patch("api_gateway.tools.cases.fetch_all", new=AsyncMock(return_value=[])):
            close_resp = client.post(
                f"/cases/{TEST_CASE_ID}/close-assign",
                headers={"X-API-Key": "sk-test", "X-User-ID": TEST_USER_ID},
                json={"movement_id": assign_data["assignment_id"], "reason": "Tarea completada"},
            )

        assert close_resp.status_code == 200
        assert close_resp.json()["movement_id"] == TEST_MOVEMENT_ID
