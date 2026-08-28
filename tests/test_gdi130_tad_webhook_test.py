from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TEST_SCHEMA = "100_test"
_CFG = {"api_key_id": "k1", "webhook_url": "https://muni.test/hook", "webhook_secret": "s3cr3t"}
_MUNI = {"name": "Municipalidad Test", "acronym": "MT"}


class TestBuildWebhookTestPayload:
    def test_payload_es_webhook_test_con_datos_ficticios(self):
        from services.webhooks.tad_notify import build_webhook_test_payload
        p = build_webhook_test_payload(_MUNI)
        assert p["event"] == "webhook.test"
        assert p["municipality"] == _MUNI
        assert p["citizen"]["full_name"] == "Ciudadano de Prueba"
        assert len(p["documents"]) == 1
        assert "note" in p
        assert "sent_at" in p


class TestSendTestWebhook:
    @pytest.mark.asyncio
    async def test_sin_webhook_configurado_levanta_value_error(self):
        from services.webhooks import tad_notify as tn
        with patch.object(tn, "get_tad_webhook_config", AsyncMock(return_value=None)):
            with pytest.raises(ValueError):
                await tn.send_test_webhook(schema_name=TEST_SCHEMA)

    @pytest.mark.asyncio
    async def test_entrega_2xx_marca_delivered_true_y_firma(self):
        from services.webhooks import tad_notify as tn
        resp = MagicMock(status_code=200)
        with patch.object(tn, "get_tad_webhook_config", AsyncMock(return_value=_CFG)), \
             patch.object(tn, "_get_municipality_info", AsyncMock(return_value=_MUNI)), \
             patch.object(tn, "_validar_destino_webhook", MagicMock()), \
             patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(return_value=resp)
            r = await tn.send_test_webhook(schema_name=TEST_SCHEMA)
        assert r["delivered"] is True
        assert r["status_code"] == 200
        assert r["event"] == "webhook.test"
        assert r["signature"].startswith("t=") and ",v1=" in r["signature"]
        assert r["error"] is None

    @pytest.mark.asyncio
    async def test_endpoint_del_muni_responde_500_marca_delivered_false(self):
        from services.webhooks import tad_notify as tn
        resp = MagicMock(status_code=500)
        with patch.object(tn, "get_tad_webhook_config", AsyncMock(return_value=_CFG)), \
             patch.object(tn, "_get_municipality_info", AsyncMock(return_value=_MUNI)), \
             patch.object(tn, "_validar_destino_webhook", MagicMock()), \
             patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(return_value=resp)
            r = await tn.send_test_webhook(schema_name=TEST_SCHEMA)
        assert r["delivered"] is False
        assert r["status_code"] == 500
        assert "500" in r["error"]

    @pytest.mark.asyncio
    async def test_error_de_conexion_no_rompe_devuelve_error(self):
        from services.webhooks import tad_notify as tn
        with patch.object(tn, "get_tad_webhook_config", AsyncMock(return_value=_CFG)), \
             patch.object(tn, "_get_municipality_info", AsyncMock(return_value=_MUNI)), \
             patch.object(tn, "_validar_destino_webhook", MagicMock()), \
             patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=RuntimeError("Connection refused")
            )
            r = await tn.send_test_webhook(schema_name=TEST_SCHEMA)
        assert r["delivered"] is False
        assert r["status_code"] is None
        assert "Connection refused" in r["error"]


class TestRutaRegistrada:
    def test_ruta_webhook_test_registrada(self):
        from api_gateway import http_server
        paths = {r.path for r in http_server.routes if hasattr(r, "path")}
        assert "/api/v1/tad/webhook/test" in paths
