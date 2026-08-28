import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

TEST_SCHEMA = "100_test"
TEST_CITIZEN_ID = str(uuid.uuid4())
OTRO_CITIZEN_ID = str(uuid.uuid4())
TEST_DOC_ID = str(uuid.uuid4())
TEST_API_KEY_ID = str(uuid.uuid4())


def _request(method: str, path: str, *, path_params=None, body: bytes = b"", headers=None):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "path_params": path_params or {},
        "headers": headers or [
            (b"x-api-key", b"sk-tad"),
            (b"x-citizen-id", TEST_CITIZEN_ID.encode()),
        ],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive=receive)


def _body(response) -> dict:
    return json.loads(response.body.decode())


class TestConsultaDeEstado:

    @pytest.mark.asyncio
    async def test_la_ruta_existe_y_es_GET(self):
        from api_gateway.http_server import routes

        match = [
            r for r in routes
            if getattr(r, "path", None) == "/api/v1/tad/documents/{id}"
        ]
        assert match, "la ruta de consulta de estado no esta registrada"
        assert "GET" in match[0].methods

    @pytest.mark.asyncio
    async def test_documento_de_otro_ciudadano_devuelve_404_generico(self):
        from api_gateway import rest_api_tad as tad

        with patch.object(tad, "validate_tad_api_key", new_callable=AsyncMock) as auth, \
             patch("services.citizens.document_status.fetch_one", new_callable=AsyncMock) as fetch:
            auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            fetch.return_value = {
                "id": TEST_DOC_ID, "status": "signed", "reference": "Ajena",
                "created_at": None, "created_by_citizen": OTRO_CITIZEN_ID,
                "document_type_acronym": "PROV",
            }
            resp = await tad.api_tad_get_document(
                _request("GET", f"/api/v1/tad/documents/{TEST_DOC_ID}",
                         path_params={"id": TEST_DOC_ID})
            )

        assert resp.status_code == 404
        assert _body(resp)["error"] == tad._GENERIC_DOCUMENT_404

    @pytest.mark.asyncio
    async def test_id_malformado_no_llega_a_la_query(self):
        from api_gateway import rest_api_tad as tad

        with patch.object(tad, "validate_tad_api_key", new_callable=AsyncMock) as auth, \
             patch("services.citizens.document_status.fetch_one", new_callable=AsyncMock) as fetch:
            auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            resp = await tad.api_tad_get_document(
                _request("GET", "/api/v1/tad/documents/no-es-uuid",
                         path_params={"id": "no-es-uuid"})
            )

        assert resp.status_code == 404
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_documento_firmado_devuelve_numero_y_pdf(self):
        from services.citizens.document_status import get_citizen_document_status

        draft = {
            "id": TEST_DOC_ID, "status": "signed", "reference": "Poda",
            "created_at": None, "created_by_citizen": TEST_CITIZEN_ID,
            "document_type_acronym": "PROV",
        }
        oficial = {
            "official_number": "PROV-2026-00003039-MDEV-TAD",
            "signed_at": None, "pdf_location": "oficial",
        }
        with patch("services.citizens.document_status.fetch_one", new_callable=AsyncMock) as fetch, \
             patch("services.citizens.document_status._pdf_url", new_callable=AsyncMock) as url:
            fetch.side_effect = [draft, oficial]
            url.return_value = "https://r2/presignado"
            estado = await get_citizen_document_status(
                TEST_DOC_ID, TEST_CITIZEN_ID, schema_name=TEST_SCHEMA,
            )

        assert estado["status"] == "signed"
        assert estado["official_number"] == "PROV-2026-00003039-MDEV-TAD"
        assert estado["pdf_url"] == "https://r2/presignado"

    @pytest.mark.asyncio
    async def test_firma_en_cola_devuelve_queued_sin_numero(self):
        from services.citizens.document_status import get_citizen_document_status

        draft = {
            "id": TEST_DOC_ID, "status": "sent_to_sign", "reference": "Poda",
            "created_at": None, "created_by_citizen": TEST_CITIZEN_ID,
            "document_type_acronym": "PROV",
        }
        sesion = {
            "session_id": uuid.uuid4(), "status": "pending",
            "failure_reason": None, "expires_at": None,
        }
        with patch("services.citizens.document_status.fetch_one", new_callable=AsyncMock) as fetch:
            fetch.side_effect = [draft, None, sesion]
            estado = await get_citizen_document_status(
                TEST_DOC_ID, TEST_CITIZEN_ID, schema_name=TEST_SCHEMA,
            )

        assert estado["status"] == "queued"
        assert estado["official_number"] is None

    @pytest.mark.asyncio
    async def test_firma_fallida_devuelve_el_motivo(self):
        from services.citizens.document_status import get_citizen_document_status

        draft = {
            "id": TEST_DOC_ID, "status": "sent_to_sign", "reference": "Poda",
            "created_at": None, "created_by_citizen": TEST_CITIZEN_ID,
            "document_type_acronym": "PROV",
        }
        sesion = {
            "session_id": uuid.uuid4(), "status": "failed",
            "failure_reason": "notary_business_error", "expires_at": None,
        }
        with patch("services.citizens.document_status.fetch_one", new_callable=AsyncMock) as fetch:
            fetch.side_effect = [draft, None, sesion]
            estado = await get_citizen_document_status(
                TEST_DOC_ID, TEST_CITIZEN_ID, schema_name=TEST_SCHEMA,
            )

        assert estado["status"] == "failed"
        assert estado["failure_reason"] == "notary_business_error"

    @pytest.mark.asyncio
    async def test_una_url_que_no_se_puede_firmar_no_tira_abajo_la_respuesta(self):
        from services.citizens import document_status

        with patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock) as r2:
            r2.side_effect = RuntimeError("R2 caido")
            url = await document_status._pdf_url(
                "PROV-2026-1-X-TAD", "oficial", schema_name=TEST_SCHEMA,
            )

        assert url is None


class TestEventoEnElWebhook:


    @pytest.mark.asyncio
    async def test_documents_signed_llega_con_su_event(self):
        from services.webhooks import tad_notify

        enviados = {}

        class _Resp:
            status_code = 200
            def raise_for_status(self): pass

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, content=None, headers=None):
                enviados["body"] = json.loads(content.decode())
                return _Resp()

        job = {
            "id": str(uuid.uuid4()), "schema_name": TEST_SCHEMA,
            "api_key_id": TEST_API_KEY_ID, "event_type": "documents.signed",
            "payload": {
                "document_id": TEST_DOC_ID,
                "official_number": "PROV-2026-1-X-TAD",
                "status": "signed",
            },
            "attempts": 0,
        }
        with patch.object(tad_notify, "_validar_destino_webhook", lambda url: None), \
             patch.object(tad_notify, "get_tad_webhook_config", new_callable=AsyncMock) as cfg, \
             patch.object(tad_notify, "_mark_job_sent", new_callable=AsyncMock), \
             patch.object(tad_notify.httpx, "AsyncClient", lambda *a, **k: _Client()):
            cfg.return_value = {
                "api_key_id": TEST_API_KEY_ID,
                "webhook_url": "https://portal.muni.gob.ar/avisos",
                "webhook_secret": "s3cr3t",
            }
            await tad_notify.send_tad_webhook_job(job)

        assert enviados["body"]["event"] == "documents.signed", (
            "sin `event`, el portal recibe los tres eventos en la misma URL "
            "sin poder rutearlos"
        )
        assert enviados["body"]["official_number"] == "PROV-2026-1-X-TAD"
        assert "sent_at" in enviados["body"]

    @pytest.mark.asyncio
    async def test_un_evento_de_firma_no_lleva_documents_vacio(self):
        from services.webhooks import tad_notify

        enviados = {}

        class _Resp:
            status_code = 200
            def raise_for_status(self): pass

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, content=None, headers=None):
                enviados["body"] = json.loads(content.decode())
                return _Resp()

        job = {
            "id": str(uuid.uuid4()), "schema_name": TEST_SCHEMA,
            "api_key_id": TEST_API_KEY_ID,
            "event_type": "documents.signature_failed",
            "payload": {
                "document_id": TEST_DOC_ID, "status": "failed",
                "failure_reason": "notary_business_error",
            },
            "attempts": 0,
        }
        with patch.object(tad_notify, "_validar_destino_webhook", lambda url: None), \
             patch.object(tad_notify, "get_tad_webhook_config", new_callable=AsyncMock) as cfg, \
             patch.object(tad_notify, "_mark_job_sent", new_callable=AsyncMock), \
             patch.object(tad_notify, "_resolve_documents_for_payload", new_callable=AsyncMock) as resolve, \
             patch.object(tad_notify.httpx, "AsyncClient", lambda *a, **k: _Client()):
            cfg.return_value = {
                "api_key_id": TEST_API_KEY_ID,
                "webhook_url": "https://portal.muni.gob.ar/avisos",
                "webhook_secret": "s3cr3t",
            }
            await tad_notify.send_tad_webhook_job(job)

        assert "documents" not in enviados["body"]
        resolve.assert_not_called()
        assert enviados["body"]["event"] == "documents.signature_failed"

    @pytest.mark.asyncio
    async def test_documents_notified_sigue_regenerando_sus_links(self):
        from services.webhooks import tad_notify

        enviados = {}

        class _Resp:
            status_code = 200
            def raise_for_status(self): pass

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, content=None, headers=None):
                enviados["body"] = json.loads(content.decode())
                return _Resp()

        job = {
            "id": str(uuid.uuid4()), "schema_name": TEST_SCHEMA,
            "api_key_id": TEST_API_KEY_ID, "event_type": "documents.notified",
            "payload": {
                "event": "documents.notified",
                "documents": [{"id": TEST_DOC_ID, "url": "https://vieja"}],
            },
            "attempts": 3,
        }
        with patch.object(tad_notify, "_validar_destino_webhook", lambda url: None), \
             patch.object(tad_notify, "get_tad_webhook_config", new_callable=AsyncMock) as cfg, \
             patch.object(tad_notify, "_mark_job_sent", new_callable=AsyncMock), \
             patch.object(tad_notify, "_resolve_documents_for_payload", new_callable=AsyncMock) as resolve, \
             patch.object(tad_notify.httpx, "AsyncClient", lambda *a, **k: _Client()):
            cfg.return_value = {
                "api_key_id": TEST_API_KEY_ID,
                "webhook_url": "https://portal.muni.gob.ar/avisos",
                "webhook_secret": "s3cr3t",
            }
            resolve.return_value = [{"id": TEST_DOC_ID, "url": "https://fresca"}]
            await tad_notify.send_tad_webhook_job(job)

        resolve.assert_awaited_once()
        assert enviados["body"]["documents"][0]["url"] == "https://fresca"


class TestIdempotencia:

    def test_key_vacia_o_gigante_se_rechaza(self):
        from services.citizens import idempotency

        assert idempotency.validate_key(None) is None
        assert idempotency.validate_key("  abc  ") == "abc"
        with pytest.raises(ValueError):
            idempotency.validate_key("   ")
        with pytest.raises(ValueError):
            idempotency.validate_key("x" * 300)

    def test_la_huella_distingue_cuerpos_distintos(self):
        from services.citizens.idempotency import fingerprint

        assert fingerprint(b'{"a":1}') == fingerprint(b'{"a":1}')
        assert fingerprint(b'{"a":1}') != fingerprint(b'{"a":2}')

    @pytest.mark.asyncio
    async def test_sin_header_no_toca_la_tabla_de_idempotencia(self):
        from api_gateway import rest_api_tad as tad

        cuerpo = json.dumps({
            "document_type_acronym": "PROV", "reference": "Poda",
        }).encode()

        with patch.object(tad, "validate_tad_api_key", new_callable=AsyncMock) as auth, \
             patch.object(tad.idempotency, "begin", new_callable=AsyncMock) as begin, \
             patch.object(tad.idempotency, "resolve_api_key_id", new_callable=AsyncMock) as resolve, \
             patch("services.documents.signing.citizen_signing.create_and_sign_citizen_document",
                   new_callable=AsyncMock) as crear:
            auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            crear.return_value = {"document_id": TEST_DOC_ID, "status": "queued"}
            resp = await tad.api_tad_create_document(
                _request("POST", "/api/v1/tad/documents", body=cuerpo)
            )

        assert resp.status_code == 202
        begin.assert_not_called()
        resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_reintento_con_la_misma_key_devuelve_la_misma_respuesta(self):
        from api_gateway import rest_api_tad as tad
        from services.citizens.idempotency import IdempotencyDecision, IdempotencyOutcome

        cuerpo = json.dumps({
            "document_type_acronym": "PROV", "reference": "Poda",
        }).encode()
        original = {"document_id": TEST_DOC_ID, "status": "queued"}

        with patch.object(tad, "validate_tad_api_key", new_callable=AsyncMock) as auth, \
             patch.object(tad.idempotency, "resolve_api_key_id", new_callable=AsyncMock) as resolve, \
             patch.object(tad.idempotency, "begin", new_callable=AsyncMock) as begin, \
             patch("services.documents.signing.citizen_signing.create_and_sign_citizen_document",
                   new_callable=AsyncMock) as crear:
            auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            resolve.return_value = TEST_API_KEY_ID
            begin.return_value = IdempotencyDecision(
                IdempotencyOutcome.REPLAY, response=original,
            )
            resp = await tad.api_tad_create_document(
                _request("POST", "/api/v1/tad/documents", body=cuerpo, headers=[
                    (b"x-api-key", b"sk-tad"),
                    (b"x-citizen-id", TEST_CITIZEN_ID.encode()),
                    (b"idempotency-key", b"tramite-4711"),
                ])
            )

        crear.assert_not_called(), "el reintento NO puede crear un segundo documento"
        assert resp.status_code == 202
        assert _body(resp) == original
        assert resp.headers["Idempotent-Replay"] == "true"

    @pytest.mark.asyncio
    async def test_key_en_vuelo_devuelve_409(self):
        from api_gateway import rest_api_tad as tad
        from services.citizens.idempotency import IdempotencyDecision, IdempotencyOutcome

        cuerpo = json.dumps({
            "document_type_acronym": "PROV", "reference": "Poda",
        }).encode()

        with patch.object(tad, "validate_tad_api_key", new_callable=AsyncMock) as auth, \
             patch.object(tad.idempotency, "resolve_api_key_id", new_callable=AsyncMock) as resolve, \
             patch.object(tad.idempotency, "begin", new_callable=AsyncMock) as begin, \
             patch("services.documents.signing.citizen_signing.create_and_sign_citizen_document",
                   new_callable=AsyncMock) as crear:
            auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            resolve.return_value = TEST_API_KEY_ID
            begin.return_value = IdempotencyDecision(
                IdempotencyOutcome.CONFLICT, message="en curso",
            )
            resp = await tad.api_tad_create_document(
                _request("POST", "/api/v1/tad/documents", body=cuerpo, headers=[
                    (b"x-api-key", b"sk-tad"),
                    (b"x-citizen-id", TEST_CITIZEN_ID.encode()),
                    (b"idempotency-key", b"tramite-4711"),
                ])
            )

        assert resp.status_code == 409
        crear.assert_not_called()

    @pytest.mark.asyncio
    async def test_un_alta_que_falla_libera_la_key(self):
        from api_gateway import rest_api_tad as tad
        from services.citizens.idempotency import IdempotencyDecision, IdempotencyOutcome
        from shared.exceptions import ValidationError

        cuerpo = json.dumps({
            "document_type_acronym": "PROV", "reference": "Poda",
        }).encode()

        with patch.object(tad, "validate_tad_api_key", new_callable=AsyncMock) as auth, \
             patch.object(tad.idempotency, "resolve_api_key_id", new_callable=AsyncMock) as resolve, \
             patch.object(tad.idempotency, "begin", new_callable=AsyncMock) as begin, \
             patch.object(tad.idempotency, "release", new_callable=AsyncMock) as release, \
             patch("services.documents.signing.citizen_signing.create_and_sign_citizen_document",
                   new_callable=AsyncMock) as crear:
            auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            resolve.return_value = TEST_API_KEY_ID
            begin.return_value = IdempotencyDecision(IdempotencyOutcome.PROCEED)
            crear.side_effect = ValidationError("form_data es requerido")
            resp = await tad.api_tad_create_document(
                _request("POST", "/api/v1/tad/documents", body=cuerpo, headers=[
                    (b"x-api-key", b"sk-tad"),
                    (b"x-citizen-id", TEST_CITIZEN_ID.encode()),
                    (b"idempotency-key", b"tramite-4711"),
                ])
            )

        assert resp.status_code == 400
        release.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_el_alta_exitosa_guarda_la_respuesta_para_el_replay(self):
        from api_gateway import rest_api_tad as tad
        from services.citizens.idempotency import IdempotencyDecision, IdempotencyOutcome

        cuerpo = json.dumps({
            "document_type_acronym": "PROV", "reference": "Poda",
        }).encode()
        creado = {"document_id": TEST_DOC_ID, "status": "queued"}

        with patch.object(tad, "validate_tad_api_key", new_callable=AsyncMock) as auth, \
             patch.object(tad.idempotency, "resolve_api_key_id", new_callable=AsyncMock) as resolve, \
             patch.object(tad.idempotency, "begin", new_callable=AsyncMock) as begin, \
             patch.object(tad.idempotency, "complete", new_callable=AsyncMock) as complete, \
             patch("services.documents.signing.citizen_signing.create_and_sign_citizen_document",
                   new_callable=AsyncMock) as crear:
            auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            resolve.return_value = TEST_API_KEY_ID
            begin.return_value = IdempotencyDecision(IdempotencyOutcome.PROCEED)
            crear.return_value = creado
            resp = await tad.api_tad_create_document(
                _request("POST", "/api/v1/tad/documents", body=cuerpo, headers=[
                    (b"x-api-key", b"sk-tad"),
                    (b"x-citizen-id", TEST_CITIZEN_ID.encode()),
                    (b"idempotency-key", b"tramite-4711"),
                ])
            )

        assert resp.status_code == 202
        complete.assert_awaited_once()
        assert complete.await_args.kwargs["response"] == creado

    @pytest.mark.asyncio
    async def test_la_misma_key_con_otro_cuerpo_es_conflicto(self):
        from services.citizens import idempotency

        existente = {
            "status": "completed",
            "request_fingerprint": idempotency.fingerprint(b'{"a":1}'),
            "response_json": {"document_id": TEST_DOC_ID},
        }
        with patch.object(idempotency, "execute", new_callable=AsyncMock), \
             patch.object(idempotency, "fetch_one", new_callable=AsyncMock) as fetch:
            fetch.side_effect = [None, existente]
            decision = await idempotency.begin(
                api_key_id=TEST_API_KEY_ID, key="tramite-4711",
                schema_name=TEST_SCHEMA, citizen_id=TEST_CITIZEN_ID,
                request_fingerprint=idempotency.fingerprint(b'{"a":2}'),
            )

        assert decision.outcome is idempotency.IdempotencyOutcome.CONFLICT
        assert "otro contenido" in decision.message

    @pytest.mark.asyncio
    async def test_key_libre_deja_pasar(self):
        from services.citizens import idempotency

        with patch.object(idempotency, "execute", new_callable=AsyncMock), \
             patch.object(idempotency, "fetch_one", new_callable=AsyncMock) as fetch:
            fetch.return_value = {"idempotency_key": "tramite-4711"}
            decision = await idempotency.begin(
                api_key_id=TEST_API_KEY_ID, key="tramite-4711",
                schema_name=TEST_SCHEMA, citizen_id=TEST_CITIZEN_ID,
                request_fingerprint="abc",
            )

        assert decision.outcome is idempotency.IdempotencyOutcome.PROCEED

    @pytest.mark.asyncio
    async def test_guardar_el_rastro_no_puede_romper_un_alta_que_salio_bien(self):
        from services.citizens import idempotency

        with patch.object(idempotency, "execute", new_callable=AsyncMock) as ex:
            ex.side_effect = RuntimeError("BD caida")
            await idempotency.complete(
                api_key_id=TEST_API_KEY_ID, key="k",
                document_id=TEST_DOC_ID, response={"document_id": TEST_DOC_ID},
            )

    @pytest.mark.asyncio
    async def test_la_respuesta_se_guarda_como_objeto_no_como_string(self):
        from services.citizens import idempotency

        respuesta = {"document_id": TEST_DOC_ID, "status": "queued"}
        with patch.object(idempotency, "execute", new_callable=AsyncMock) as ex:
            await idempotency.complete(
                api_key_id=TEST_API_KEY_ID, key="k",
                document_id=TEST_DOC_ID, response=respuesta,
            )

        assert respuesta in ex.await_args.args, (
            "response_json tiene que viajar como dict: el codec jsonb lo serializa"
        )
