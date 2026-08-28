
from api_gateway import rest_api_signing as sig_mod


class _FakeCtx:
    schema_name = "100_test"


class _FakeRequest:
    def __init__(self, session_id, headers=None):
        self.path_params = {"session_id": session_id}
        self.headers = headers or {
            "X-API-Key": "gdi-test-key",
            "X-User-ID": "a1000000-0000-0000-0000-000000000100",
        }


class TestRewritePollUrl:
    def test_reapunta_al_prefijo_del_gateway(self):
        out = sig_mod._rewrite_poll_url(
            {"poll_url": "/signing/async-poll/abc-123", "flow": "electronic_async"}
        )
        assert out["poll_url"] == "/api/v1/signing/async-poll/abc-123"

    def test_no_toca_una_url_ya_reescrita(self):
        out = sig_mod._rewrite_poll_url({"poll_url": "/api/v1/signing/async-poll/abc-123"})
        assert out["poll_url"] == "/api/v1/signing/async-poll/abc-123"

    def test_respuesta_sin_poll_url_pasa_intacta(self):
        payload = {"success": True, "official_number": "IF-2026-00000001-TXST-INNO"}
        assert sig_mod._rewrite_poll_url(dict(payload)) == payload


class TestApiAsyncPoll:
    async def _call(self, monkeypatch, status_result, session_id="11111111-2222-3333-4444-555555555555"):
        async def fake_validate(api_key, user_id):
            return _FakeCtx()

        async def fake_status(sid, uid, *, schema_name):
            assert schema_name == "100_test"
            return status_result

        monkeypatch.setattr(sig_mod, "validate_rest_api_key", fake_validate)
        monkeypatch.setattr(sig_mod, "get_async_poll_status", fake_status)
        return await sig_mod.api_async_poll(_FakeRequest(session_id))

    async def test_sesion_signed_devuelve_numero(self, monkeypatch):
        resp = await self._call(monkeypatch, {
            "session_id": "11111111-2222-3333-4444-555555555555",
            "status": "signed",
            "official_number": "IF-2026-00002468-TXST-INNO",
            "auto_link_results": [],
            "reason": None,
            "failure_reason": None,
        })
        assert resp.status_code == 200
        assert b"IF-2026-00002468-TXST-INNO" in resp.body

    async def test_sesion_ajena_o_inexistente_es_404(self, monkeypatch):
        resp = await self._call(monkeypatch, None)
        assert resp.status_code == 404

    async def test_session_id_no_uuid_es_400_no_500(self, monkeypatch):
        resp = await self._call(monkeypatch, None, session_id="no-es-uuid")
        assert resp.status_code == 400

    async def test_api_key_invalida_es_401(self, monkeypatch):
        async def fake_validate(api_key, user_id):
            raise ValueError("API Key inválida")

        monkeypatch.setattr(sig_mod, "validate_rest_api_key", fake_validate)
        resp = await sig_mod.api_async_poll(_FakeRequest("11111111-2222-3333-4444-555555555555"))
        assert resp.status_code == 401
