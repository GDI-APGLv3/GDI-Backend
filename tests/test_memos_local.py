import pytest
from unittest.mock import patch


def _make_mock_auth_user():
    from models.schemas import AuthenticatedUser, SectorPermission
    return AuthenticatedUser(
        user_id="a1000000-0000-0000-0000-000000000001",
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


TEST_SCHEMA = "100_test"
TEST_DB_USER_ID = "a1000000-0000-0000-0000-000000000001"


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

    with patch.object(TenantMiddleware, "dispatch", _fake_tenant_dispatch):
        app.middleware_stack = None
        yield

    app.middleware_stack = None
    app.dependency_overrides.pop(get_current_user, None)


class TestMemosUUIDValidation:

    async def test_memo_id_malformado_devuelve_422(self, client, test_headers):
        r = await client.get("/memos/no-es-un-uuid-valido", headers=test_headers)
        assert r.status_code == 422, (
            f"GET /memos/{{uuid_malformado}} devuelve {r.status_code} en vez de 422. "
            f"Respuesta: {r.text[:200]}"
        )

    async def test_archive_uuid_malformado_devuelve_422(self, client, test_headers):
        r = await client.patch(
            "/memos/no-es-un-uuid-valido/archive",
            json={"archived": True},
            headers=test_headers,
        )
        assert r.status_code == 422, (
            f"PATCH /memos/{{uuid_malformado}}/archive devuelve {r.status_code} en vez de 422. "
            f"Respuesta: {r.text[:200]}"
        )
