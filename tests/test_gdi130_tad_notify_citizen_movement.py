from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TEST_SCHEMA = "100_test"
TEST_USER_ID = "u1000000-0000-0000-0000-000000000001"
TEST_CITIZEN_ID = "c1000000-0000-0000-0000-000000000001"
TEST_CASE_ID = "ca000000-0000-0000-0000-000000000001"
TEST_DOC_ID_1 = "d1000000-0000-0000-0000-000000000001"
TEST_DOC_ID_2 = "d2000000-0000-0000-0000-000000000002"
TEST_SECTOR_ID = "se000000-0000-0000-0000-000000000001"


def _make_request():
    request = MagicMock()
    request.state.tenant_user_id = "auth0|externo"
    return request


class TestNotifyCitizenMovement:
    async def _run_happy_path(self, mock_create_movement, document_ids=None, official_numbers=None):
        from endpoints.cases.notify_citizen import notify_citizen, NotifyCitizenRequest

        document_ids = document_ids or [TEST_DOC_ID_1, TEST_DOC_ID_2]
        official_numbers = official_numbers or ["IF-2026-00000001-MDEV-ADGEN", "IF-2026-00000002-MDEV-ADGEN"]
        linked_docs = [
            {"id": doc_id, "official_number": num}
            for doc_id, num in zip(document_ids, official_numbers)
        ]

        async def _fetch_all_side_effect(query, *args, **kwargs):
            if "case_official_documents" in query:
                return linked_docs
            if "sector_id, full_name FROM users" in query:
                return [{"sector_id": TEST_SECTOR_ID, "full_name": "Ana Actuante"}]
            if "admin_sector_id FROM case_movements" in query:
                return [{"admin_sector_id": TEST_SECTOR_ID}]
            return []

        with patch("endpoints.cases.notify_citizen.get_authenticated_user", new_callable=AsyncMock) as mock_auth_user, \
             patch("endpoints.cases.notify_citizen.CaseService.can_user_view_case", new_callable=AsyncMock) as mock_can_view, \
             patch("endpoints.cases.notify_citizen.can_user_manage_citizen_shares", new_callable=AsyncMock) as mock_can_manage, \
             patch("endpoints.cases.notify_citizen.can_citizen_access_case", new_callable=AsyncMock) as mock_can_access, \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("database.fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
             patch("services.webhooks.tad_notify.get_tad_webhook_config", new_callable=AsyncMock) as mock_config, \
             patch("services.webhooks.tad_notify.build_documents_notified_payload", new_callable=AsyncMock) as mock_payload, \
             patch("services.webhooks.tad_notify.enqueue_tad_webhook", new_callable=AsyncMock) as mock_enqueue:
            mock_auth_user.return_value = TEST_USER_ID
            mock_can_view.return_value = True
            mock_can_manage.return_value = True
            mock_can_access.return_value = True

            async def _fetch_one_side_effect(query, *args, **kwargs):
                if "citizens" in query:
                    return {"id": TEST_CITIZEN_ID, "full_name": "Maria Vecina", "country_id": "20111111112"}
                if "cases" in query:
                    return {"id": TEST_CASE_ID, "case_number": "EX-2026-1", "reference": "Referencia"}
                return None

            mock_fetch_one.side_effect = _fetch_one_side_effect
            mock_fetch_all.side_effect = _fetch_all_side_effect
            mock_config.return_value = {"api_key_id": "key-id"}
            mock_payload.return_value = {"event": "documents.notified"}
            mock_enqueue.return_value = "job-id-123"

            body = NotifyCitizenRequest(citizen_id=TEST_CITIZEN_ID, document_ids=document_ids)
            result = await notify_citizen(
                request=_make_request(), body=body, case_id=TEST_CASE_ID,
                current_user=MagicMock(), schema_name=TEST_SCHEMA,
            )

        return result

    async def test_happy_path_crea_movement_citizen_notify(self):
        with patch("services.cases.history.create_movement", new_callable=AsyncMock) as mock_create_movement:
            result = await self._run_happy_path(mock_create_movement)

        mock_create_movement.assert_awaited_once()
        args, kwargs = mock_create_movement.call_args
        assert args[0] == TEST_CASE_ID
        assert args[1] == "citizen_notify"
        assert kwargs["user_id"] == TEST_USER_ID
        assert kwargs["reason"] == (
            "Se enviaron a notificar los documentos IF-2026-00000001-MDEV-ADGEN, "
            "IF-2026-00000002-MDEV-ADGEN"
        )
        assert kwargs["creator_sector_id"] == TEST_SECTOR_ID
        assert kwargs["admin_sector_id"] == TEST_SECTOR_ID
        assert kwargs["schema_name"] == TEST_SCHEMA

    async def test_response_message_cambia(self):
        with patch("services.cases.history.create_movement", new_callable=AsyncMock):
            result = await self._run_happy_path(None)

        assert result["message"] == "Documentos enviados al sistema de notificaciones"
        assert result["success"] is True
        assert result["data"]["job_id"] == "job-id-123"

    async def test_reason_respeta_orden_de_document_ids(self):
        document_ids = [TEST_DOC_ID_2, TEST_DOC_ID_1]
        official_numbers_by_doc = {
            TEST_DOC_ID_1: "IF-2026-00000001-MDEV-ADGEN",
            TEST_DOC_ID_2: "IF-2026-00000002-MDEV-ADGEN",
        }
        with patch("services.cases.history.create_movement", new_callable=AsyncMock) as mock_create_movement, \
             patch("endpoints.cases.notify_citizen.get_authenticated_user", new_callable=AsyncMock) as mock_auth_user, \
             patch("endpoints.cases.notify_citizen.CaseService.can_user_view_case", new_callable=AsyncMock) as mock_can_view, \
             patch("endpoints.cases.notify_citizen.can_user_manage_citizen_shares", new_callable=AsyncMock) as mock_can_manage, \
             patch("endpoints.cases.notify_citizen.can_citizen_access_case", new_callable=AsyncMock) as mock_can_access, \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("database.fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
             patch("services.webhooks.tad_notify.get_tad_webhook_config", new_callable=AsyncMock) as mock_config, \
             patch("services.webhooks.tad_notify.build_documents_notified_payload", new_callable=AsyncMock) as mock_payload, \
             patch("services.webhooks.tad_notify.enqueue_tad_webhook", new_callable=AsyncMock) as mock_enqueue:
            mock_auth_user.return_value = TEST_USER_ID
            mock_can_view.return_value = True
            mock_can_manage.return_value = True
            mock_can_access.return_value = True

            async def _fetch_one_side_effect(query, *args, **kwargs):
                if "citizens" in query:
                    return {"id": TEST_CITIZEN_ID, "full_name": "Maria Vecina", "country_id": "20111111112"}
                if "cases" in query:
                    return {"id": TEST_CASE_ID, "case_number": "EX-2026-1", "reference": "Referencia"}
                return None

            async def _fetch_all_side_effect(query, *args, **kwargs):
                if "case_official_documents" in query:
                    return [
                        {"id": TEST_DOC_ID_1, "official_number": official_numbers_by_doc[TEST_DOC_ID_1]},
                        {"id": TEST_DOC_ID_2, "official_number": official_numbers_by_doc[TEST_DOC_ID_2]},
                    ]
                if "sector_id, full_name FROM users" in query:
                    return [{"sector_id": TEST_SECTOR_ID, "full_name": "Ana Actuante"}]
                if "admin_sector_id FROM case_movements" in query:
                    return [{"admin_sector_id": TEST_SECTOR_ID}]
                return []

            mock_fetch_one.side_effect = _fetch_one_side_effect
            mock_fetch_all.side_effect = _fetch_all_side_effect
            mock_config.return_value = {"api_key_id": "key-id"}
            mock_payload.return_value = {"event": "documents.notified"}
            mock_enqueue.return_value = "job-id-123"

            from endpoints.cases.notify_citizen import notify_citizen, NotifyCitizenRequest
            body = NotifyCitizenRequest(citizen_id=TEST_CITIZEN_ID, document_ids=document_ids)
            await notify_citizen(
                request=_make_request(), body=body, case_id=TEST_CASE_ID,
                current_user=MagicMock(), schema_name=TEST_SCHEMA,
            )

        _, kwargs = mock_create_movement.call_args
        assert kwargs["reason"] == (
            "Se enviaron a notificar los documentos IF-2026-00000002-MDEV-ADGEN, "
            "IF-2026-00000001-MDEV-ADGEN"
        )

    async def test_create_movement_falla_no_rompe_la_notificacion(self):
        with patch("services.cases.history.create_movement", new_callable=AsyncMock) as mock_create_movement:
            mock_create_movement.side_effect = Exception("DB caída")
            result = await self._run_happy_path(mock_create_movement)

        assert result["success"] is True
        assert result["message"] == "Documentos enviados al sistema de notificaciones"


class TestHistoryFormattingCitizenNotify:

    async def test_citizen_notify_message_vacio_reason_directo(self):
        from services.cases import history as history_module

        movement_row = {
            "id": "mv000000-0000-0000-0000-000000000001",
            "type": "citizen_notify",
            "reason": "Se enviaron a notificar los documentos IF-2026-00000001-MDEV-ADGEN",
            "created_at": None,
            "is_active": False,
            "closed_at": None,
            "closing_reason": None,
            "user": {"id": TEST_USER_ID, "name": "Ana", "lastname": "Actuante",
                      "email": "ana@example.com", "profile_picture_url": None},
            "citizen": None,
            "creator_sector": {"id": TEST_SECTOR_ID, "name": "MDEV#ADGEN"},
            "admin_sector": {"id": TEST_SECTOR_ID, "name": "MDEV#ADGEN"},
            "assigned_sector": None,
            "assigned_user": None,
            "supporting_document_id": None,
            "supporting_document_number": None,
            "supporting_document_reference": None,
            "supporting_document_resume": None,
        }

        with patch("services.case_queries.get_case_number_query", return_value="SELECT 1"), \
             patch.object(history_module, "fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
             patch.object(history_module, "get_case_movements", new_callable=AsyncMock) as mock_get_movements:
            mock_fetch_all.return_value = [{
                "case_number": "EX-2026-1", "ai_summary": None,
                "ai_summary_updated_at": None, "short_ai_summary": None,
            }]
            mock_get_movements.return_value = [movement_row]

            result = await history_module.get_case_history(TEST_CASE_ID, schema_name=TEST_SCHEMA)

        assert len(result["movements"]) == 1
        formatted = result["movements"][0]
        assert formatted["type"] == "citizen_notify"
        assert formatted["message"] == "Se enviaron a notificar los documentos IF-2026-00000001-MDEV-ADGEN"
