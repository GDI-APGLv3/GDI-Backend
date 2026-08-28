import pytest
from unittest.mock import patch, AsyncMock

REAL_CASE_ID = "5130f93f-28c1-4ea3-8830-19e6822ea630"
TEST_DB_USER_ID = "a1000000-0000-0000-0000-000000000001"
TEST_SECTOR_ID = "51000000-0000-0000-0000-000000000001"
TARGET_SECTOR_ID = "52000000-0000-0000-0000-000000000002"
TEST_SCHEMA = "100_test"
TEST_MOVEMENT_ID = "mov-11111111-1111-1111-1111-111111111111"
TEST_TASK_ID = "task-2222-2222-2222-222222222222"
TEST_DOC_ID = "doc-33333333-3333-3333-3333-333333333333"

TRANSFER_RESULT = {
    "movement_id": TEST_MOVEMENT_ID,
    "case_number": "EE-2026-000198-TXST-INTE",
    "action_type": "transferido",
    "target_sector": "HAC",
    "target_department": "Hacienda",
    "transferred_by": "Usuario Test",
    "assigned_user": None,
}

ENSURE_ASSIGNMENT_RESULT = {
    "assignment_id": TEST_MOVEMENT_ID,
    "task_id": TEST_TASK_ID,
    "sector_acronym": "INTE#HAC",
    "department_name": "Hacienda",
    "is_new_assignment": True,
}

CLOSE_ASSIGNMENT_RESULT = {
    "movement_id": TEST_MOVEMENT_ID,
    "case_id": REAL_CASE_ID,
    "movement_type": "assignment",
    "closing_reason": "Tarea completada",
}

DOCUMENT_RESULT = {
    "document_id": TEST_DOC_ID,
    "official_number": "PV-2026-00001613-TXST-TESO",
    "message": "Documento creado",
}

FETCH_ROW = [{
    "case_number": "EE-2026-000198-TXST-INTE",
    "owner_sector_id": TEST_SECTOR_ID,
    "sector_id": TEST_SECTOR_ID,
    "supporting_document_id": None,
    "assigned_sector_id": TARGET_SECTOR_ID,
}]


def _make_mock_auth_user():
    from models.schemas import AuthenticatedUser, SectorPermission
    return AuthenticatedUser(
        user_id=TEST_DB_USER_ID,
        auth_id="local_test_user",
        email="test.user@municipalidad.test",
        full_name="Usuario Test",
        permissions=[
            SectorPermission(
                sector_id=TEST_SECTOR_ID,
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

    app.dependency_overrides[get_current_user] = lambda: _make_mock_auth_user()

    with (
        patch.object(TenantMiddleware, "dispatch", _fake_tenant_dispatch),
        patch(
            "endpoints.cases.transfer_case.get_authenticated_user",
            new_callable=AsyncMock,
            return_value=TEST_DB_USER_ID,
        ),
    ):
        app.middleware_stack = None
        yield

    app.middleware_stack = None
    app.dependency_overrides.pop(get_current_user, None)


class TestOperacionRechazadaNoDejaPV:

    @pytest.mark.asyncio
    async def test_transfer_rechazada_no_emite_pv(self, client, test_headers):
        from shared.exceptions import ValidationError

        with patch("database.fetch_all", new=AsyncMock(return_value=FETCH_ROW)), \
             patch("services.case_service.CaseService.transfer_case",
                   new=AsyncMock(side_effect=ValidationError(
                       "El usuario asignado no pertenece al sector destino"))), \
             patch("endpoints.cases.transfer_case.create_transfer_document",
                   new=AsyncMock(return_value=dict(DOCUMENT_RESULT))) as mock_doc, \
             patch("services.case_service.CaseService.link_official_document",
                   new=AsyncMock(return_value={"order_number": 1})) as mock_link:
            response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/transfer",
                headers=test_headers,
                json={
                    "target_sector_id": TARGET_SECTOR_ID,
                    "reason": "Transferencia por competencia",
                    "transfer_ownership": True,
                    "assigned_user_id": "b2000000-0000-0000-0000-000000000002",
                    "create_official_doc": True,
                },
            )

        assert response.status_code >= 400, "la transferencia rechazada tiene que fallar"
        mock_doc.assert_not_called()
        mock_link.assert_not_called()
        print("[PASS] Transferencia rechazada no emite PV ni consume numeracion")

    @pytest.mark.asyncio
    async def test_assign_rechazada_no_emite_pv(self, client, test_headers):
        from shared.exceptions import ValidationError

        with patch("database.fetch_all", new=AsyncMock(return_value=FETCH_ROW)), \
             patch("services.cases.tasks.ensure_assignment_and_create_task",
                   new=AsyncMock(side_effect=ValidationError("Sector destino inactivo"))), \
             patch("endpoints.cases.transfer_case.create_transfer_document",
                   new=AsyncMock(return_value=dict(DOCUMENT_RESULT))) as mock_doc:
            response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/assign",
                headers=test_headers,
                json={
                    "target_sector_id": TARGET_SECTOR_ID,
                    "reason": "Revision legal solicitada",
                    "create_official_doc": True,
                },
            )

        assert response.status_code >= 400
        mock_doc.assert_not_called()
        print("[PASS] Asignacion rechazada no emite PV")

    @pytest.mark.asyncio
    async def test_close_assign_fallido_no_emite_pv(self, client, test_headers):
        from shared.exceptions import ValidationError

        with patch("database.fetch_all", new=AsyncMock(return_value=FETCH_ROW)), \
             patch("services.case_service.CaseService.close_assignment",
                   new=AsyncMock(side_effect=ValidationError("Movimiento ya cerrado"))), \
             patch("endpoints.cases.transfer_case.create_transfer_document",
                   new=AsyncMock(return_value=dict(DOCUMENT_RESULT))) as mock_doc:
            response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/close-assign",
                headers=test_headers,
                json={
                    "movement_id": TEST_MOVEMENT_ID,
                    "reason": "Tarea completada",
                    "create_official_doc": True,
                },
            )

        assert response.status_code >= 400
        mock_doc.assert_not_called()
        print("[PASS] Cierre fallido no emite PV de cierre")


class TestOperacionOKEmitePVDespues:

    @pytest.mark.asyncio
    async def test_transfer_ok_emite_pv_despues_y_lo_engancha(self, client, test_headers):
        orden = []

        async def _transfer(*args, **kwargs):
            orden.append("transfer")
            return dict(TRANSFER_RESULT)

        async def _create_doc(*args, **kwargs):
            orden.append("pv")
            return dict(DOCUMENT_RESULT)

        with patch("database.fetch_all", new=AsyncMock(return_value=FETCH_ROW)), \
             patch("database.execute", new=AsyncMock(return_value="UPDATE 1")) as mock_exec, \
             patch("services.case_service.CaseService.transfer_case", new=_transfer), \
             patch("endpoints.cases.transfer_case.create_transfer_document", new=_create_doc), \
             patch("services.case_service.CaseService.link_official_document",
                   new=AsyncMock(return_value={"order_number": 1})) as mock_link:
            response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/transfer",
                headers=test_headers,
                json={
                    "target_sector_id": TARGET_SECTOR_ID,
                    "reason": "Transferencia por competencia",
                    "transfer_ownership": True,
                    "create_official_doc": True,
                },
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["official_document"]["official_number"] == DOCUMENT_RESULT["official_number"]

        assert orden == ["transfer", "pv"], f"orden incorrecto: {orden}"

        assert mock_link.await_args.kwargs["system_generated"] is True

        update_sql = mock_exec.await_args.args[0]
        assert "UPDATE case_movements" in update_sql
        assert "supporting_document_id" in update_sql
        assert mock_exec.await_args.args[1] == TEST_DOC_ID
        assert mock_exec.await_args.args[2] == TEST_MOVEMENT_ID
        print("[PASS] Transferencia OK emite el PV despues y lo engancha al movimiento")

    @pytest.mark.asyncio
    async def test_assign_ok_emite_pv_despues(self, client, test_headers):
        orden = []

        async def _ensure(*args, **kwargs):
            orden.append("assign")
            return dict(ENSURE_ASSIGNMENT_RESULT)

        async def _create_doc(*args, **kwargs):
            orden.append("pv")
            return dict(DOCUMENT_RESULT)

        with patch("database.fetch_all", new=AsyncMock(return_value=FETCH_ROW)), \
             patch("database.execute", new=AsyncMock(return_value="UPDATE 1")), \
             patch("services.cases.tasks.ensure_assignment_and_create_task", new=_ensure), \
             patch("endpoints.cases.transfer_case.create_transfer_document", new=_create_doc), \
             patch("services.case_service.CaseService.link_official_document",
                   new=AsyncMock(return_value={"order_number": 1})):
            response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/assign",
                headers=test_headers,
                json={
                    "target_sector_id": TARGET_SECTOR_ID,
                    "reason": "Revision legal solicitada",
                    "create_official_doc": True,
                },
            )

        assert response.status_code == 200
        assert orden == ["assign", "pv"], f"orden incorrecto: {orden}"
        print("[PASS] Asignacion OK emite el PV despues")


class TestPVFallidoNoTiraAbajoLaOperacion:

    @pytest.mark.asyncio
    async def test_transfer_ok_con_pv_fallido_da_200(self, client, test_headers):
        with patch("database.fetch_all", new=AsyncMock(return_value=FETCH_ROW)), \
             patch("database.execute", new=AsyncMock(return_value="UPDATE 1")), \
             patch("services.case_service.CaseService.transfer_case",
                   new=AsyncMock(return_value=dict(TRANSFER_RESULT))) as mock_transfer, \
             patch("endpoints.cases.transfer_case.create_transfer_document",
                   new=AsyncMock(side_effect=Exception("PDFComposer timeout"))):
            response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/transfer",
                headers=test_headers,
                json={
                    "target_sector_id": TARGET_SECTOR_ID,
                    "reason": "Transferencia por competencia",
                    "transfer_ownership": True,
                    "create_official_doc": True,
                },
            )

        assert response.status_code == 200, "la transferencia ya ocurrio: no se puede fallar el request"
        body = response.json()
        assert body["data"].get("official_document") is None
        assert "no se pudo emitir" in body["message"].lower()
        mock_transfer.assert_awaited_once()
        print("[PASS] PV fallido tras transferir da 200 con official_document null + aviso")

    @pytest.mark.asyncio
    async def test_assign_ok_con_pv_fallido_da_200(self, client, test_headers):
        with patch("database.fetch_all", new=AsyncMock(return_value=FETCH_ROW)), \
             patch("database.execute", new=AsyncMock(return_value="UPDATE 1")), \
             patch("services.cases.tasks.ensure_assignment_and_create_task",
                   new=AsyncMock(return_value=dict(ENSURE_ASSIGNMENT_RESULT))), \
             patch("endpoints.cases.transfer_case.create_transfer_document",
                   new=AsyncMock(side_effect=Exception("Notary caido"))):
            response = await client.post(
                f"/api/v1/cases/{REAL_CASE_ID}/assign",
                headers=test_headers,
                json={
                    "target_sector_id": TARGET_SECTOR_ID,
                    "reason": "Revision legal solicitada",
                    "create_official_doc": True,
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"].get("official_document") is None
        assert "no se pudo emitir" in body["message"].lower()
        print("[PASS] PV fallido tras asignar da 200 con official_document null + aviso")


class TestLinkOfficialDocumentSystemGenerated:

    @pytest.mark.asyncio
    async def test_system_generated_saltea_permisos(self):
        from services.cases.documents import link_official_document

        with patch("services.cases.documents._assert_can_link_documents",
                   new=AsyncMock()) as mock_perm, \
             patch("services.cases.documents.fetch_all",
                   new=AsyncMock(return_value=[])) as mock_fetch:
            with pytest.raises(Exception):
                await link_official_document(
                    case_id=REAL_CASE_ID,
                    official_document_id=TEST_DOC_ID,
                    linking_user_id=TEST_DB_USER_ID,
                    user_sector_id=TEST_SECTOR_ID,
                    system_generated=True,
                    schema_name=TEST_SCHEMA,
                )
        mock_perm.assert_not_called()
        assert mock_fetch.await_count >= 1
        print("[PASS] system_generated=True saltea el chequeo de permisos")

    @pytest.mark.asyncio
    async def test_default_sigue_chequeando_permisos(self):
        from services.cases.documents import link_official_document
        from shared.exceptions import AuthorizationError

        with patch("services.cases.documents._assert_can_link_documents",
                   new=AsyncMock(side_effect=AuthorizationError("sin permisos"))) as mock_perm, \
             patch("services.cases.documents.fetch_all",
                   new=AsyncMock(return_value=[{"id": REAL_CASE_ID, "case_number": "EE-1"}])):
            with pytest.raises(Exception):
                await link_official_document(
                    case_id=REAL_CASE_ID,
                    official_document_id=TEST_DOC_ID,
                    linking_user_id=TEST_DB_USER_ID,
                    user_sector_id=TEST_SECTOR_ID,
                    schema_name=TEST_SCHEMA,
                )
        mock_perm.assert_called_once()
        print("[PASS] Sin system_generated el chequeo de permisos sigue corriendo")
