from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TEST_SCHEMA = "100_test"
TEST_CITIZEN_ID = "c1000000-0000-0000-0000-000000000001"
TEST_CASE_ID = "ca000000-0000-0000-0000-000000000001"
TEST_DOC_ID = "d1000000-0000-0000-0000-000000000001"


class TestProposeCitizenBranch:
    @pytest.mark.asyncio
    async def test_requiere_exactamente_uno_de_user_o_citizen(self):
        from services.cases.documents import propose_document_to_case
        from shared.exceptions import ValidationError
        with pytest.raises(ValidationError):
            await propose_document_to_case(
                TEST_CASE_ID, TEST_DOC_ID, None, schema_name=TEST_SCHEMA, proposing_citizen_id=None,
            )

    @pytest.mark.asyncio
    async def test_no_acepta_ambos(self):
        from services.cases.documents import propose_document_to_case
        from shared.exceptions import ValidationError
        with pytest.raises(ValidationError):
            await propose_document_to_case(
                TEST_CASE_ID, TEST_DOC_ID, "u1000000-0000-0000-0000-000000000001",
                schema_name=TEST_SCHEMA, proposing_citizen_id=TEST_CITIZEN_ID,
            )

    @pytest.mark.asyncio
    async def test_citizen_branch_inserta_proposing_citizen_id_y_movement_con_citizen(self):
        from services.cases import documents as documents_module

        mock_conn = MagicMock()
        mock_conn.fetchrow = AsyncMock(return_value={"doc_reserved": False, "case_reserved": False})
        mock_conn.execute = AsyncMock(return_value="INSERT 0 1")

        @asynccontextmanager
        async def _fake_transaction(*args, **kwargs):
            yield mock_conn

        with patch.object(documents_module, "transaction", _fake_transaction), \
             patch("database.check_document_exists", new_callable=AsyncMock) as mock_exists, \
             patch.object(documents_module, "fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
             patch("services.cases.history.create_movement", new_callable=AsyncMock) as mock_create_movement:
            mock_exists.return_value = True
            mock_fetch_all.side_effect = [
                [{"admin_sector_id": "sector-admin"}],
                [{"reference": "Solicitud de prueba", "document_number": "SOLIC-2026-00000001-MUNI-TAD"}],
            ]

            await documents_module.propose_document_to_case(
                TEST_CASE_ID, TEST_DOC_ID, schema_name=TEST_SCHEMA,
                auth_source="tad", proposing_citizen_id=TEST_CITIZEN_ID,
            )

        insert_call = mock_conn.execute.call_args_list[0]
        assert "proposing_citizen_id" in insert_call[0][0]
        assert insert_call[0][3] == TEST_CITIZEN_ID

        mock_create_movement.assert_awaited_once()
        kwargs = mock_create_movement.call_args.kwargs
        assert kwargs["user_id"] is None
        assert kwargs["citizen_id"] == TEST_CITIZEN_ID
        assert kwargs["creator_sector_id"] == "sector-admin"
        assert kwargs["admin_sector_id"] == "sector-admin"
        assert kwargs["movement_type"] == "document_proposal"


class TestCreateMovementCitizen:
    @pytest.mark.asyncio
    async def test_movement_con_citizen_id_pasa_user_id_null_al_insert(self):
        from services.cases import history as history_module
        with patch.object(history_module, "execute", new_callable=AsyncMock) as mock_execute:
            await history_module.create_movement(
                case_id=TEST_CASE_ID,
                movement_type="document_proposal",
                citizen_id=TEST_CITIZEN_ID,
                creator_sector_id="sector-1",
                admin_sector_id="sector-1",
                reason="Propuso vincular X",
                schema_name=TEST_SCHEMA,
                auth_source="tad",
            )
        args = mock_execute.call_args[0]
        assert args[4] is None
        assert args[5] == TEST_CITIZEN_ID
        kwargs = mock_execute.call_args.kwargs
        assert kwargs["user_id"] == TEST_CITIZEN_ID


class TestWebhookHmac:
    def test_header_format(self):
        from services.webhooks.tad_hmac import build_webhook_hmac_header
        header = build_webhook_hmac_header(
            "un-secreto", method="POST", path="/webhooks/gdi", body_bytes=b'{"a":1}',
        )
        assert header.startswith("t=")
        assert ",v1=" in header

    def test_secretos_distintos_dan_firmas_distintas(self):
        from services.webhooks.tad_hmac import build_webhook_hmac_header
        import time
        with patch("time.time", return_value=1000.0):
            h1 = build_webhook_hmac_header("secreto-a", method="POST", path="/x", body_bytes=b"{}")
            h2 = build_webhook_hmac_header("secreto-b", method="POST", path="/x", body_bytes=b"{}")
        assert h1 != h2

    def test_bodies_distintos_dan_firmas_distintas(self):
        from services.webhooks.tad_hmac import build_webhook_hmac_header
        with patch("time.time", return_value=1000.0):
            h1 = build_webhook_hmac_header("secreto", method="POST", path="/x", body_bytes=b'{"a":1}')
            h2 = build_webhook_hmac_header("secreto", method="POST", path="/x", body_bytes=b'{"a":2}')
        assert h1 != h2


class TestWebhookConfig:
    @pytest.mark.asyncio
    async def test_sin_key_configurada_devuelve_none(self):
        from services.webhooks import tad_notify as notify_module
        with patch.object(notify_module, "fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            result = await notify_module.get_tad_webhook_config(schema_name=TEST_SCHEMA)
        assert result is None

    @pytest.mark.asyncio
    async def test_con_key_configurada_desencripta_secret(self):
        from cryptography.fernet import Fernet
        from services.webhooks import tad_notify as notify_module

        key = Fernet.generate_key()
        fernet = Fernet(key)
        encrypted_secret = fernet.encrypt(b"mi-secreto-plano").decode()

        with patch.object(notify_module, "fetch_one", new_callable=AsyncMock) as mock_fetch, \
             patch.object(notify_module, "CERT_MASTER_KEY", key.decode()):
            mock_fetch.return_value = {
                "id": "key-1", "webhook_url": "https://muni.example/webhook",
                "webhook_secret": encrypted_secret,
            }
            result = await notify_module.get_tad_webhook_config(schema_name=TEST_SCHEMA)

        assert result["webhook_secret"] == "mi-secreto-plano"
        assert result["webhook_url"] == "https://muni.example/webhook"
        assert result["api_key_id"] == "key-1"


class TestEnqueueWebhook:
    @pytest.mark.asyncio
    async def test_enqueue_hace_insert_y_notify(self):
        from services.webhooks import tad_notify as notify_module

        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(return_value="INSERT 0 1")

        @asynccontextmanager
        async def _tx():
            yield mock_conn

        mock_conn.transaction = _tx

        @asynccontextmanager
        async def _fake_get_conn(*args, **kwargs):
            yield mock_conn

        with patch.object(notify_module, "get_conn", _fake_get_conn):
            job_id = await notify_module.enqueue_tad_webhook(
                schema_name=TEST_SCHEMA, api_key_id="key-1",
                event_type="documents.notified", payload={"event": "documents.notified"},
            )

        assert job_id
        calls = [c[0][0] for c in mock_conn.execute.call_args_list]
        assert any("INSERT INTO public.tad_webhook_jobs" in c for c in calls)
        assert any("pg_notify" in c for c in calls)


class TestSendWebhookJobRegeneratesUrls:
    @pytest.mark.asyncio
    async def test_regenera_urls_antes_de_enviar(self):
        from services.webhooks import tad_notify as notify_module

        job = {
            "id": "job-1", "schema_name": TEST_SCHEMA, "api_key_id": "key-1",
            "event_type": "documents.notified", "attempts": 0,
            "payload": {
                "event": "documents.notified",
                "documents": [{"id": TEST_DOC_ID, "official_number": "OLD-NUM", "name": "ref", "url": "https://old-expired-url"}],
            },
        }

        with patch.object(notify_module, "get_tad_webhook_config", new_callable=AsyncMock) as mock_cfg, \
             patch.object(notify_module, "_resolve_documents_for_payload", new_callable=AsyncMock) as mock_resolve, \
             patch.object(notify_module, "_validar_destino_webhook", MagicMock()), \
             patch("httpx.AsyncClient") as mock_client_cls, \
             patch.object(notify_module, "_mark_job_sent", new_callable=AsyncMock) as mock_mark_sent:
            mock_cfg.return_value = {
                "api_key_id": "key-1", "webhook_url": "https://muni.example/webhook",
                "webhook_secret": "secreto",
            }
            fresh_docs = [{"id": TEST_DOC_ID, "official_number": "OLD-NUM", "name": "ref", "url": "https://fresh-url"}]
            mock_resolve.return_value = fresh_docs

            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await notify_module.send_tad_webhook_job(job)

        mock_resolve.assert_awaited_once()
        sent_body = mock_client_instance.post.call_args.kwargs["content"]
        assert b"fresh-url" in sent_body
        assert b"old-expired-url" not in sent_body
        mock_mark_sent.assert_awaited_once_with("job-1")

    @pytest.mark.asyncio
    async def test_falla_reencola_con_backoff(self):
        from services.webhooks import tad_notify as notify_module

        job = {
            "id": "job-1", "schema_name": TEST_SCHEMA, "api_key_id": "key-1",
            "event_type": "documents.notified", "attempts": 0,
            "payload": {"event": "documents.notified", "documents": []},
        }

        with patch.object(notify_module, "get_tad_webhook_config", new_callable=AsyncMock) as mock_cfg, \
             patch.object(notify_module, "_requeue_or_fail", new_callable=AsyncMock) as mock_requeue:
            mock_cfg.return_value = None

            await notify_module.send_tad_webhook_job(job)

        mock_requeue.assert_awaited_once()
        args = mock_requeue.call_args[0]
        assert args[0] == "job-1"
        assert args[1] == 1


class TestApiTadProposeDocument:
    def _get_routes(self):
        from api_gateway.http_server import routes as gateway_routes
        result = {}
        for route in gateway_routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                for method in (route.methods or []):
                    result[(route.path, method)] = getattr(route, "endpoint", None)
        return result

    def test_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/tad/cases/{id}/propose", "POST") in routes

    @pytest.mark.asyncio
    async def test_documento_de_otro_ciudadano_devuelve_404(self):
        from api_gateway import rest_api_tad as tad_module
        from starlette.requests import Request

        with patch.object(tad_module, "validate_tad_api_key", new_callable=AsyncMock) as mock_auth, \
             patch("services.cases.citizen_shares.can_citizen_access_case", new_callable=AsyncMock) as mock_gate, \
             patch.object(tad_module, "fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            mock_gate.return_value = True
            mock_fetch.return_value = {
                "id": TEST_DOC_ID, "status": "signed",
                "created_by_citizen": "c9999999-0000-0000-0000-000000000009",
            }

            async def _body():
                return {"document_id": TEST_DOC_ID}

            scope = {
                "type": "http", "method": "POST",
                "path": f"/api/v1/tad/cases/{TEST_CASE_ID}/propose",
                "path_params": {"id": TEST_CASE_ID},
                "headers": [(b"x-api-key", b"sk-x"), (b"x-citizen-id", TEST_CITIZEN_ID.encode())],
            }
            request = Request(scope)
            request.json = _body
            response = await tad_module.api_tad_propose_document(request)
            assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_documento_no_firmado_devuelve_409(self):
        from api_gateway import rest_api_tad as tad_module
        from starlette.requests import Request

        with patch.object(tad_module, "validate_tad_api_key", new_callable=AsyncMock) as mock_auth, \
             patch("services.cases.citizen_shares.can_citizen_access_case", new_callable=AsyncMock) as mock_gate, \
             patch.object(tad_module, "fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_auth.return_value = (TEST_SCHEMA, {"id": TEST_CITIZEN_ID, "estado": "validado"})
            mock_gate.return_value = True
            mock_fetch.return_value = {
                "id": TEST_DOC_ID, "status": "draft", "created_by_citizen": TEST_CITIZEN_ID,
            }

            async def _body():
                return {"document_id": TEST_DOC_ID}

            scope = {
                "type": "http", "method": "POST",
                "path": f"/api/v1/tad/cases/{TEST_CASE_ID}/propose",
                "path_params": {"id": TEST_CASE_ID},
                "headers": [(b"x-api-key", b"sk-x"), (b"x-citizen-id", TEST_CITIZEN_ID.encode())],
            }
            request = Request(scope)
            request.json = _body
            response = await tad_module.api_tad_propose_document(request)
            assert response.status_code == 409


class TestNotifyCitizenRouteRegistered:
    def test_route_registered(self):
        import main
        paths = {(r.path, m) for r in main.app.routes if hasattr(r, "path") for m in (getattr(r, "methods", None) or [])}
        assert ("/api/v1/cases/{case_id}/notify-citizen", "POST") in paths
