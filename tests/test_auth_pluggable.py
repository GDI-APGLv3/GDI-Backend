import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api_gateway.context import MCPContext
from models.schemas import AuthenticatedUser, SectorPermission

TEST_SCHEMA = "100_test"
TEST_USER_ID = "a1000000-0000-0000-0000-000000000001"
TEST_API_KEY = "sk-gdi-test-key"
PRIMARY_SECTOR_ID = "51000000-0000-0000-0000-000000000001"

USER_DB_ROW = {
    "user_id": TEST_USER_ID,
    "auth_id": "auth0|test123",
    "full_name": "Usuario Test",
    "email": "test.user@municipalidad.test",
    "sector_id": PRIMARY_SECTOR_ID,
    "estado": 1,
}

PERMISSIONS = [
    SectorPermission(
        sector_id=PRIMARY_SECTOR_ID,
        sector_acronym="PRIV",
        department_id="d1000000-0000-0000-0000-000000000001",
        department_name="Intendencia",
        department_acronym="INTE",
        can_view=True,
        can_edit=True,
        is_primary=True,
    )
]


def _make_request(headers: dict, path: str = "/api/v1/cases/"):
    lower = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda k, default=None: lower.get(k.lower(), default)),
        url=SimpleNamespace(path=path),
        state=SimpleNamespace(),
    )


class TestResolveAuth:
    @pytest.mark.asyncio
    async def test_jwt_is_delegated(self):
        from middleware.auth_router import resolve_auth
        req = _make_request({"Authorization": "Bearer eyJ.fake.jwt"})
        result = await resolve_auth(req)
        assert result is not None
        assert result.is_jwt is True
        assert result.auth_source == "jwt"

    @pytest.mark.asyncio
    async def test_no_credentials_returns_none(self):
        from middleware.auth_router import resolve_auth
        req = _make_request({})
        assert await resolve_auth(req) is None

    @pytest.mark.asyncio
    async def test_api_key_resolves_tenant(self):
        from middleware import auth_router
        ctx = MCPContext(
            api_key=TEST_API_KEY,
            municipality_id="m1",
            schema_name=TEST_SCHEMA,
            auth_source="api_key",
            user_id=TEST_USER_ID,
        )
        req = _make_request({"X-API-Key": TEST_API_KEY, "X-User-ID": TEST_USER_ID})
        with patch("api_gateway.auth_rest.validate_rest_api_key",
                   new=AsyncMock(return_value=ctx)):
            result = await auth_router.resolve_auth(req)
        assert result.is_jwt is False
        assert result.auth_source == "api_key"
        assert result.schema_name == TEST_SCHEMA
        assert result.tenant_user_id == TEST_USER_ID

    @pytest.mark.asyncio
    async def test_api_key_with_bearer_uses_api_key(self):
        from middleware import auth_router
        ctx = MCPContext(
            api_key=TEST_API_KEY,
            municipality_id="m1",
            schema_name=TEST_SCHEMA,
            auth_source="api_key",
            user_id=TEST_USER_ID,
        )
        req = _make_request({
            "X-API-Key": TEST_API_KEY,
            "X-User-ID": TEST_USER_ID,
            "Authorization": "Bearer eyJ.fake.jwt",
        })
        with patch("api_gateway.auth_rest.validate_rest_api_key",
                   new=AsyncMock(return_value=ctx)):
            result = await auth_router.resolve_auth(req)
        assert result.auth_source == "api_key"
        assert result.is_jwt is False

    @pytest.mark.asyncio
    async def test_invalid_api_key_raises_value_error(self):
        from middleware import auth_router
        req = _make_request({"X-API-Key": "bad-key", "X-User-ID": TEST_USER_ID})
        with patch("api_gateway.auth_rest.validate_rest_api_key",
                   new=AsyncMock(side_effect=ValueError("API Key inválida"))):
            with pytest.raises(ValueError):
                await auth_router.resolve_auth(req)


def _primary_sector(user: AuthenticatedUser):
    for perm in user.permissions:
        if perm.is_primary:
            return perm.sector_id
    for perm in user.permissions:
        if perm.can_edit:
            return perm.sector_id
    return None


class TestGetCurrentUserBothPaths:
    @pytest.mark.parametrize("auth_mode", ["jwt", "api_key"])
    @pytest.mark.asyncio
    async def test_sender_sector_id_populated(self, auth_mode):
        import auth as auth_module

        req = SimpleNamespace(state=SimpleNamespace(schema_name=TEST_SCHEMA),
                              headers={})

        with patch.object(auth_module.user_service, "get_user_by_id",
                          new=AsyncMock(return_value=USER_DB_ROW)), \
             patch.object(auth_module.user_service, "get_user_by_auth_id",
                          new=AsyncMock(return_value=USER_DB_ROW)), \
             patch.object(auth_module, "load_user_permissions",
                          new=AsyncMock(return_value=PERMISSIONS)):

            if auth_mode == "api_key":
                req.state.auth_source = "api_key"
                req.state.tenant_user_id = TEST_USER_ID
                user = await auth_module.get_current_user(req, credentials=None, x_user_id=None)
            else:
                with patch.object(auth_module, "verify_token",
                                  return_value={"sub": "auth0|test123",
                                                "email": USER_DB_ROW["email"]}):
                    creds = SimpleNamespace(credentials="eyJ.fake.jwt")
                    user = await auth_module.get_current_user(req, credentials=creds, x_user_id=None)

        assert isinstance(user, AuthenticatedUser)
        assert user.user_id == TEST_USER_ID
        sector = _primary_sector(user)
        assert sector == PRIMARY_SECTOR_ID, \
            f"[{auth_mode}] sender_sector_id quedó vacío: {sector}"

    @pytest.mark.asyncio
    async def test_api_key_without_tenant_user_rejected(self):
        import auth as auth_module
        from fastapi import HTTPException

        req = SimpleNamespace(state=SimpleNamespace(
            schema_name=TEST_SCHEMA, auth_source="api_key"), headers={})
        with pytest.raises(HTTPException) as exc:
            await auth_module.get_current_user(req, credentials=None, x_user_id=None)
        assert exc.value.status_code == 401


class TestRateLimitApiKey:
    def test_multiplier_constant(self):
        from middleware.rate_limit import RATE_LIMIT, API_KEY_RATE_MULTIPLIER
        assert API_KEY_RATE_MULTIPLIER >= 10
        assert RATE_LIMIT * API_KEY_RATE_MULTIPLIER > RATE_LIMIT

    @pytest.mark.asyncio
    async def test_api_key_gets_higher_limit(self):
        from middleware.rate_limit import RateLimitMiddleware, RATE_LIMIT, API_KEY_RATE_MULTIPLIER

        mw = RateLimitMiddleware(app=None)
        mw._mode = "in-memory"

        async def call_next(_req):
            return SimpleNamespace(headers={})

        req = SimpleNamespace(
            method="GET",
            url=SimpleNamespace(path="/api/v1/cases/"),
            headers=SimpleNamespace(get=lambda k, d=None: d),
            client=SimpleNamespace(host="1.2.3.4"),
            state=SimpleNamespace(auth_source="api_key"),
        )
        resp = await mw.dispatch(req, call_next)
        assert resp.headers["X-RateLimit-Limit"] == str(RATE_LIMIT * API_KEY_RATE_MULTIPLIER)

    @pytest.mark.asyncio
    async def test_jwt_gets_default_limit(self):
        from middleware.rate_limit import RateLimitMiddleware, RATE_LIMIT

        mw = RateLimitMiddleware(app=None)
        mw._mode = "in-memory"

        async def call_next(_req):
            return SimpleNamespace(headers={})

        req = SimpleNamespace(
            method="GET",
            url=SimpleNamespace(path="/api/v1/cases/"),
            headers=SimpleNamespace(get=lambda k, d=None: d),
            client=SimpleNamespace(host="1.2.3.5"),
            state=SimpleNamespace(auth_source="jwt"),
        )
        resp = await mw.dispatch(req, call_next)
        assert resp.headers["X-RateLimit-Limit"] == str(RATE_LIMIT)
