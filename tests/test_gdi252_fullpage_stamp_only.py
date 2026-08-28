
import base64
import inspect

import pytest
from unittest.mock import AsyncMock, patch

from services.shared import notary_api
from shared.exceptions import NotaryBusinessError


class _Resp:
    def __init__(self, status_code: int, text: str = "", payload: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.headers: dict = {}

    def json(self):
        if self._payload is None:
            raise ValueError("sin json")
        return self._payload


def _ok_payload() -> dict:
    return {
        "stamped_pdf_b64": base64.b64encode(b"%PDF-1.7 estampado").decode(),
        "sig_llx": 1.0, "sig_lly": 2.0, "sig_urx": 3.0, "sig_ury": 4.0,
    }


@pytest.fixture
def sin_breaker():
    with (
        patch.object(notary_api, "check_breaker_before_call", AsyncMock(), create=True),
        patch("services.shared.notary_breaker.check_breaker_before_call", AsyncMock()),
        patch("services.shared.notary_breaker.record_notary_failure", AsyncMock()),
        patch("services.shared.notary_breaker.record_notary_success", AsyncMock()),
    ):
        yield


class TestFullpageEnStampOnly:
    @pytest.mark.asyncio
    async def test_reintenta_con_pagina_agregada(self, sin_breaker):
        respuestas = [
            _Resp(400, "FULLPAGE: la firma no entra en la última página"),
            _Resp(200, payload=_ok_payload()),
        ]
        llamadas = []

        async def fake_post(client, url, **kwargs):
            llamadas.append(kwargs.get("log_label", ""))
            return respuestas.pop(0)

        with (
            patch.object(notary_api, "post_micro_with_coldstart_retry", fake_post),
            patch.object(notary_api, "add_blank_page_to_pdf", lambda b: b + b" +pagina"),
        ):
            resultado = await notary_api.call_notary_stamp_only(
                b"%PDF-1.7 original", "IF-2026-00000001-XX-YY"
            )

        assert resultado[0] == b"%PDF-1.7 estampado"
        assert len(llamadas) == 2
        assert "FULLPAGE" in llamadas[1]

    @pytest.mark.asyncio
    async def test_manda_el_pdf_aumentado_no_el_original(self, sin_breaker):
        respuestas = [_Resp(400, "FULLPAGE"), _Resp(200, payload=_ok_payload())]
        enviados = []

        async def fake_post(client, url, **kwargs):
            enviados.append(kwargs["files"]["pdf_file"][1])
            return respuestas.pop(0)

        with (
            patch.object(notary_api, "post_micro_with_coldstart_retry", fake_post),
            patch.object(notary_api, "add_blank_page_to_pdf", lambda b: b + b" +pagina"),
        ):
            await notary_api.call_notary_stamp_only(b"%PDF-1.7 original", "IF-1")

        assert enviados[0] == b"%PDF-1.7 original"
        assert enviados[1] == b"%PDF-1.7 original +pagina"

    @pytest.mark.asyncio
    async def test_conserva_los_parametros_en_el_reintento(self, sin_breaker):
        respuestas = [_Resp(400, "FULLPAGE"), _Resp(200, payload=_ok_payload())]
        envios = []

        async def fake_post(client, url, **kwargs):
            envios.append(kwargs["files"])
            return respuestas.pop(0)

        with (
            patch.object(notary_api, "post_micro_with_coldstart_retry", fake_post),
            patch.object(notary_api, "add_blank_page_to_pdf", lambda b: b + b"x"),
        ):
            await notary_api.call_notary_stamp_only(
                b"pdf", "IF-9", city="TANDIL", stamp_position="last", existing_count=3
            )

        assert envios[1]["existing_count"] == (None, "3")
        assert envios[1]["document_number"] == (None, "IF-9")
        assert envios[1]["city"] == (None, "TANDIL")
        assert envios[1]["stamp_position"] == (None, "last")

    @pytest.mark.asyncio
    async def test_detecta_fullpage_tambien_en_json(self, sin_breaker):
        respuestas = [
            _Resp(400, "", payload={"detail": "FULLPAGE: sin espacio"}),
            _Resp(200, payload=_ok_payload()),
        ]

        async def fake_post(client, url, **kwargs):
            return respuestas.pop(0)

        with (
            patch.object(notary_api, "post_micro_with_coldstart_retry", fake_post),
            patch.object(notary_api, "add_blank_page_to_pdf", lambda b: b + b"x"),
        ):
            resultado = await notary_api.call_notary_stamp_only(b"pdf", "IF-1")

        assert resultado[0] == b"%PDF-1.7 estampado"

    @pytest.mark.asyncio
    async def test_si_falla_con_pagina_nueva_el_error_dice_por_que(self, sin_breaker):
        respuestas = [_Resp(400, "FULLPAGE"), _Resp(400, "OTRA_COSA")]

        async def fake_post(client, url, **kwargs):
            return respuestas.pop(0)

        with (
            patch.object(notary_api, "post_micro_with_coldstart_retry", fake_post),
            patch.object(notary_api, "add_blank_page_to_pdf", lambda b: b + b"x"),
        ):
            with pytest.raises(NotaryBusinessError) as exc:
                await notary_api.call_notary_stamp_only(b"pdf", "IF-1")

        assert "notary_fullpage" in str(exc.value)

    @pytest.mark.asyncio
    async def test_un_400_que_no_es_fullpage_no_agrega_pagina(self, sin_breaker):
        agregadas = []

        async def fake_post(client, url, **kwargs):
            return _Resp(400, "INVALID_STAMP_PARAMETERS")

        def fake_add(b):
            agregadas.append(b)
            return b

        with (
            patch.object(notary_api, "post_micro_with_coldstart_retry", fake_post),
            patch.object(notary_api, "add_blank_page_to_pdf", fake_add),
        ):
            with pytest.raises(NotaryBusinessError):
                await notary_api.call_notary_stamp_only(b"pdf", "IF-1")

        assert agregadas == []


class TestFailureReasonPropio:
    def test_fullpage_tiene_codigo_discreto(self):
        from workers.escri import _failure_code

        assert _failure_code(NotaryBusinessError("notary_fullpage: no entra")) == "notary_fullpage"

    def test_el_resto_de_errores_de_negocio_no_cambia(self):
        from workers.escri import _failure_code

        assert _failure_code(NotaryBusinessError("otra cosa")) == "notary_business_error"


class TestMensajeDeNotary:
    def test_notary_explica_el_motivo(self):
        from pathlib import Path

        ruta = Path(__file__).resolve().parents[2] / "GDI-Notary" / "app" / "layout.py"
        if not ruta.exists():
            pytest.skip("GDI-Notary no está en este worktree")
        fuente = ruta.read_text(encoding="utf-8")
        assert 'raise LayoutError("FULLPAGE")' not in fuente
        assert 'FULLPAGE:' in fuente


class TestHmacDelReintento:

    @pytest.mark.asyncio
    async def test_stamp_only_refirma_el_pdf_aumentado(self, sin_breaker, monkeypatch):
        import importlib
        monkeypatch.setenv("NOTARY_INTERNAL_HMAC_SECRET", "secreto-de-test")
        import services.notary_internal_hmac as hmac_mod
        importlib.reload(hmac_mod)

        respuestas = [_Resp(400, "FULLPAGE"), _Resp(200, payload=_ok_payload())]
        firmas = []

        async def fake_post(client, url, **kwargs):
            firmas.append(kwargs["headers"].get("X-Internal-Sign"))
            return respuestas.pop(0)

        with (
            patch.object(notary_api, "post_micro_with_coldstart_retry", fake_post),
            patch.object(notary_api, "add_blank_page_to_pdf", lambda b: b + b" +pagina"),
        ):
            await notary_api.call_notary_stamp_only(b"%PDF original", "IF-1")

        assert firmas[0], "el primer intento tiene que ir firmado"
        assert firmas[1], "el reintento tiene que ir firmado"
        assert firmas[0] != firmas[1], (
            "el reintento reusó la firma del PDF original: Notary lo rechaza con 401"
        )
        importlib.reload(hmac_mod)

    def test_sign_pdf_tambien_refirma_en_el_retry(self):
        import inspect

        fuente = inspect.getsource(notary_api.call_notary_sign_pdf)
        assert '_notary_headers("/sign-pdf", augmented_pdf)' in fuente

    def test_hay_un_solo_lugar_que_arma_los_headers(self):
        import inspect

        fuente = inspect.getsource(notary_api)
        assert fuente.count("build_internal_hmac_header(") == 2


class TestBreakerEnElReintento:
    @pytest.mark.asyncio
    async def test_un_5xx_en_el_retry_cuenta_como_caida(self, sin_breaker):
        from shared.exceptions import NotaryUnavailableError

        respuestas = [_Resp(400, "FULLPAGE"), _Resp(503, "upstream caido")]

        async def fake_post(client, url, **kwargs):
            return respuestas.pop(0)

        with (
            patch.object(notary_api, "post_micro_with_coldstart_retry", fake_post),
            patch.object(notary_api, "add_blank_page_to_pdf", lambda b: b + b"x"),
            patch("services.shared.notary_breaker.record_notary_failure", AsyncMock()) as m_fail,
        ):
            with pytest.raises(NotaryUnavailableError):
                await notary_api.call_notary_stamp_only(b"pdf", "IF-1")

        m_fail.assert_awaited_once()
