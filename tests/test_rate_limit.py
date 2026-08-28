import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import middleware.rate_limit as rate_limit_module
from middleware.rate_limit import RateLimitMiddleware


class _FakeTenantMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request, call_next):
        user_id = request.headers.get("x-test-user-id")
        auth_source = request.headers.get("x-test-auth-source")
        if user_id:
            request.state.tenant_user_id = user_id
        if auth_source:
            request.state.auth_source = auth_source
        return await call_next(request)


async def _ping(request):
    return PlainTextResponse("ok")


def _build_client() -> TestClient:
    app = Starlette(routes=[Route("/ping", _ping)])
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(_FakeTenantMiddleware)
    return TestClient(app)


@pytest.fixture
def rate_limit_client(monkeypatch):
    monkeypatch.setattr(rate_limit_module, "RATE_LIMIT", 2)
    monkeypatch.setattr(rate_limit_module, "RATE_LIMIT_PER_USER", 3)
    return _build_client()


class TestRateLimitPerUser:
    def test_usuario_jwt_tiene_ventana_propia(self, rate_limit_client):
        headers = {"x-test-auth-source": "jwt", "x-test-user-id": "user-A"}
        codes = [rate_limit_client.get("/ping", headers=headers).status_code for _ in range(3)]
        assert codes == [200, 200, 200]

        r = rate_limit_client.get("/ping", headers=headers)
        assert r.status_code == 429

    def test_usuario_b_no_se_come_el_cupo_de_usuario_a_misma_ip(self, rate_limit_client):
        headers_a = {"x-test-auth-source": "jwt", "x-test-user-id": "user-A"}
        headers_b = {"x-test-auth-source": "jwt", "x-test-user-id": "user-B"}

        for _ in range(3):
            assert rate_limit_client.get("/ping", headers=headers_a).status_code == 200
        assert rate_limit_client.get("/ping", headers=headers_a).status_code == 429

        codes_b = [rate_limit_client.get("/ping", headers=headers_b).status_code for _ in range(3)]
        assert codes_b == [200, 200, 200]

    def test_anonimo_sigue_per_ip(self, rate_limit_client):
        codes = [rate_limit_client.get("/ping").status_code for _ in range(2)]
        assert codes == [200, 200]

        r = rate_limit_client.get("/ping")
        assert r.status_code == 429

    def test_api_key_no_se_toca_sigue_per_ip_con_multiplicador(self, rate_limit_client):
        headers = {"x-test-auth-source": "api_key"}
        codes = [rate_limit_client.get("/ping", headers=headers).status_code for _ in range(5)]
        assert codes == [200] * 5

    def test_sin_tenant_user_id_cae_a_per_ip_sin_romper(self, rate_limit_client):
        headers = {"x-test-auth-source": "jwt"}
        codes = [rate_limit_client.get("/ping", headers=headers).status_code for _ in range(2)]
        assert codes == [200, 200]

        r = rate_limit_client.get("/ping", headers=headers)
        assert r.status_code == 429
