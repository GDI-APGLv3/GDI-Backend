from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def _respuesta_202(document_id, citizen_id, *, schema_name, payload=None):
    return {
        "success": True,
        "message": "Documento recibido — la firma se está procesando",
        "document_id": document_id,
        "session_id": "5e551000-0000-0000-0000-00000000000a",
        "status": "queued",
        "expires_at": "2026-08-20T23:59:00Z",
    }


TEST_SCHEMA = "100_test"
TEST_CITIZEN_ID = "c1000000-0000-0000-0000-000000000001"
TEST_DOC_ID = "d1000000-0000-0000-0000-000000000001"


def _minimal_valid_pdf() -> bytes:
    import io
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


VALID_PDF_BYTES = _minimal_valid_pdf()


def _mock_get_conn_with_tx(doc_type_row, insert_draft_row):
    mock_conn = MagicMock()

    fetchrow_results = [doc_type_row, insert_draft_row]

    async def _fetchrow(*args, **kwargs):
        return fetchrow_results.pop(0)

    mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    mock_conn.execute = AsyncMock(return_value="INSERT 0 1")

    @asynccontextmanager
    async def _tx():
        yield mock_conn

    mock_conn.transaction = _tx

    @asynccontextmanager
    async def _fake_get_conn(*args, **kwargs):
        yield mock_conn

    return _fake_get_conn, mock_conn


class TestCreateDocumentCitizenBranch:
    @pytest.mark.asyncio
    async def test_requiere_exactamente_uno_de_creator_o_citizen(self):
        from services.documents.lifecycle.creation import create_document
        from shared.exceptions import ValidationError
        with pytest.raises(ValidationError):
            await create_document("SOLIC", "ref", None, schema_name=TEST_SCHEMA, citizen_id=None)

    @pytest.mark.asyncio
    async def test_no_acepta_ambos_creator_y_citizen(self):
        from services.documents.lifecycle.creation import create_document
        from shared.exceptions import ValidationError
        with pytest.raises(ValidationError):
            await create_document(
                "SOLIC", "ref", "u1000000-0000-0000-0000-000000000001",
                schema_name=TEST_SCHEMA, citizen_id=TEST_CITIZEN_ID,
            )

    @pytest.mark.asyncio
    async def test_citizen_id_no_uuid_levanta_validation_error(self):
        from services.documents.lifecycle.creation import create_document
        from shared.exceptions import ValidationError
        with pytest.raises(ValidationError):
            await create_document("SOLIC", "ref", None, schema_name=TEST_SCHEMA, citizen_id="no-es-uuid")

    @pytest.mark.asyncio
    async def test_citizen_branch_usa_queries_citizen(self):
        from services.documents.lifecycle import creation as creation_module
        import datetime as _dt

        doc_type_row = {"document_type_id": 1, "name": "Solicitud", "base_type": "HTML"}
        insert_draft_row = {"document_id": TEST_DOC_ID, "last_modified_at": _dt.datetime.now()}
        fake_get_conn, mock_conn = _mock_get_conn_with_tx(doc_type_row, insert_draft_row)

        with patch.object(creation_module, "get_conn", fake_get_conn):
            result = await creation_module.create_document(
                "SOLIC", "Referencia de prueba", None,
                schema_name=TEST_SCHEMA, auth_source="tad", citizen_id=TEST_CITIZEN_ID,
            )

        assert result["success"] is True
        assert result["citizen_id"] == TEST_CITIZEN_ID
        assert result["creator_id"] is None

        draft_call = mock_conn.fetchrow.call_args_list[1]
        draft_document_id = draft_call[0][1]
        signer_call = mock_conn.execute.call_args
        assert signer_call[0][1] == draft_document_id
        assert signer_call[0][2] == TEST_CITIZEN_ID
        assert signer_call[0][3] == 1
        assert signer_call[0][4] is True


class TestStartSigningCitizenGuard:
    @pytest.mark.asyncio
    async def test_citizen_creador_pasa_el_guard(self):
        from services.documents.signing import signing as signing_module
        from shared.exceptions import DocumentStateError

        document_row = {
            "document_id": TEST_DOC_ID,
            "reference": "ref",
            "status": "draft",
            "content": {},
            "created_by": None,
            "created_by_citizen": TEST_CITIZEN_ID,
            "document_type_id": 1,
            "type_name": "Solicitud",
            "type_acronym": "SOLIC",
            "source_type": "HTML",
            "has_fields": False,
        }

        with patch("services.documents.signing.signing.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [document_row, None]
            with pytest.raises(DocumentStateError):
                await signing_module.start_document_signing_process(
                    TEST_DOC_ID, TEST_CITIZEN_ID, schema_name=TEST_SCHEMA,
                )

    @pytest.mark.asyncio
    async def test_actor_distinto_de_creador_y_citizen_levanta_authorization_error(self):
        from services.documents.signing import signing as signing_module
        from shared.exceptions import AuthorizationError

        document_row = {
            "document_id": TEST_DOC_ID,
            "reference": "ref",
            "status": "draft",
            "content": {},
            "created_by": None,
            "created_by_citizen": TEST_CITIZEN_ID,
            "document_type_id": 1,
            "type_name": "Solicitud",
            "type_acronym": "SOLIC",
            "source_type": "HTML",
            "has_fields": False,
        }
        otro_citizen_id = "c2000000-0000-0000-0000-000000000002"

        with patch("services.documents.signing.signing.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = document_row
            with pytest.raises(AuthorizationError):
                await signing_module.start_document_signing_process(
                    TEST_DOC_ID, otro_citizen_id, schema_name=TEST_SCHEMA,
                )


class TestSignersForPdfQueryBranch:
    def test_query_incluye_left_join_citizens_y_coalesce(self):
        from services.documents.core.queries import get_document_signers_for_pdf_query
        sql = get_document_signers_for_pdf_query()
        assert "LEFT JOIN users" in sql
        assert "LEFT JOIN citizens" in sql
        assert "COALESCE(u.full_name, c.full_name)" in sql
        assert "ds.citizen_id" in sql


class TestNumberingTadHelpers:
    @pytest.mark.asyncio
    async def test_get_tad_department_no_encontrado_levanta_validation_error(self):
        from shared.numbering import _get_tad_department
        from shared.exceptions import ValidationError
        mock_conn = MagicMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        with pytest.raises(ValidationError):
            await _get_tad_department(mock_conn)

    @pytest.mark.asyncio
    async def test_get_tad_department_ok(self):
        from shared.numbering import _get_tad_department
        mock_conn = MagicMock()
        mock_conn.fetchrow = AsyncMock(return_value={"dept_acronym": "TAD", "department_id": "dept-1"})
        acronym, dept_id = await _get_tad_department(mock_conn)
        assert acronym == "TAD"
        assert dept_id == "dept-1"

    def test_precondicion_numerator_citizen_marcada_en_el_modulo(self):
        import inspect
        import shared.numbering as numbering

        source = inspect.getsource(numbering)
        assert "PRECONDICIÓN DE AMBIENTE" in source
        assert "numerator_citizen" in source

    @pytest.mark.asyncio
    async def test_reserve_citizen_number_rechaza_special_numbering(self):
        from shared.numbering import reserve_citizen_number
        from shared.exceptions import ValidationError

        mock_conn = MagicMock()
        call_results = [
            {"city_acronym": "MUNI"},
            {"dept_acronym": "TAD", "department_id": "dept-1"},
            {"id": 1, "special_numbering": True},
        ]

        async def _fetchrow(*args, **kwargs):
            return call_results.pop(0)

        mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow)
        mock_conn.execute = AsyncMock(return_value="OK")

        @asynccontextmanager
        async def _fake_get_conn(*args, **kwargs):
            yield mock_conn

        with patch("shared.numbering.get_conn", _fake_get_conn):
            with pytest.raises(ValidationError) as exc_info:
                await reserve_citizen_number(
                    "SOLIC", TEST_CITIZEN_ID, 2026,
                    schema_name=TEST_SCHEMA, document_id=TEST_DOC_ID,
                    reference="ref", document_type_id=1, content={},
                )
        assert "SPECIAL" in exc_info.value.message


class TestProposeDocumentAuditFix:
    @pytest.mark.asyncio
    async def test_auth_source_parametrizado_default_jwt(self):
        import inspect
        from services.cases.documents import propose_document_to_case
        sig = inspect.signature(propose_document_to_case)
        assert "auth_source" in sig.parameters
        assert sig.parameters["auth_source"].default == "jwt"

    @pytest.mark.asyncio
    async def test_propose_registra_movement_document_proposal(self):
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
                [{"sector_id": "sector-user"}],
                [{"reference": "Doc de prueba", "document_number": None}],
            ]

            await documents_module.propose_document_to_case(
                "case-1", "draft-1", "user-1", schema_name=TEST_SCHEMA, auth_source="jwt",
            )

        mock_create_movement.assert_awaited_once()
        call_kwargs = mock_create_movement.call_args.kwargs
        assert call_kwargs["movement_type"] == "document_proposal"
        assert call_kwargs["case_id"] == "case-1"


class TestSyncWhitelistAndRoute:
    def test_citizens_en_sync_tables(self):
        from api_gateway.tools.sync import SYNC_TABLES
        assert "citizens" in SYNC_TABLES

    def test_numerator_citizen_en_official_documents_columns(self):
        from api_gateway.tools.sync import OFFICIAL_DOCUMENTS_COLUMNS
        assert "numerator_citizen" in OFFICIAL_DOCUMENTS_COLUMNS

    def test_ruta_tad_documents_registrada(self):
        from api_gateway.http_server import routes as gateway_routes
        found = any(
            getattr(r, "path", None) == "/api/v1/tad/documents" and "POST" in (getattr(r, "methods", None) or [])
            for r in gateway_routes
        )
        assert found

    def test_handler_create_document_exportado(self):
        import inspect
        from api_gateway.rest_api_tad import api_tad_create_document
        assert callable(api_tad_create_document)
        assert inspect.iscoroutinefunction(api_tad_create_document)


class TestSignerDataShapeForFrontend:
    def test_query_incluye_left_join_citizens(self):
        from services.shared.user_data import _build_document_signers_query
        sql = _build_document_signers_query()
        assert "LEFT JOIN citizens" in sql
        assert "LEFT JOIN users" in sql
        assert "COALESCE(u.full_name, c.full_name)" in sql
        assert "country_id" in sql

    def test_format_signer_data_citizen(self):
        from services.shared.user_data import _format_signer_data
        raw = {
            "user_id": None,
            "citizen_id": TEST_CITIZEN_ID,
            "full_name": "Juan Perez",
            "country_id": "20111111112",
            "email": None,
            "is_numerator": True,
            "has_signed": True,
            "signed_at": None,
            "profile_picture_url": None,
            "seal_name": None,
            "department_acronym": None,
        }
        result = _format_signer_data(raw)
        assert "is_citizen" not in result
        assert result["citizen_id"] == TEST_CITIZEN_ID
        assert result["country_id"] == "20111111112"
        assert result["user_id"] is None
        assert result["full_name"] == "Juan Perez"
        assert result["seal_name"] == "CIUDADANO · 20111111112"
        assert result["department_acronym"] == "TAD"

    def test_format_signer_data_user_no_regresion(self):
        from services.shared.user_data import _format_signer_data
        raw = {
            "user_id": "u1000000-0000-0000-0000-000000000001",
            "citizen_id": None,
            "full_name": "Empleado Municipal",
            "country_id": None,
            "email": "empleado@muni.gob.ar",
            "is_numerator": False,
            "has_signed": False,
            "signed_at": None,
            "profile_picture_url": None,
            "seal_name": "Sello X",
            "department_acronym": "OBPU",
        }
        result = _format_signer_data(raw)
        assert result["citizen_id"] is None
        assert result["country_id"] is None
        assert result["user_id"] == "u1000000-0000-0000-0000-000000000001"
        assert result["seal_name"] == "Sello X"
        assert result["department_acronym"] == "OBPU"


class TestAuditLogActorType:
    @pytest.mark.asyncio
    async def test_log_signature_event_default_actor_type_user(self):
        from services.documents.signing.audit_logger import log_signature_event
        with patch("database.execute", new_callable=AsyncMock) as mock_execute:
            await log_signature_event(
                schema_name=TEST_SCHEMA, document_id=TEST_DOC_ID,
                user_id="u1", signature_method="electronic", result="ok",
            )
            args = mock_execute.call_args[0]
            assert args[-1] == "user"

    @pytest.mark.asyncio
    async def test_log_signature_event_actor_type_citizen(self):
        from services.documents.signing.audit_logger import log_signature_event
        with patch("database.execute", new_callable=AsyncMock) as mock_execute:
            await log_signature_event(
                schema_name=TEST_SCHEMA, document_id=TEST_DOC_ID,
                user_id=TEST_CITIZEN_ID, signature_method="electronic", result="ok",
                actor_type="citizen",
            )
            args = mock_execute.call_args[0]
            assert args[-1] == "citizen"


class TestCreateAndSignResponseShape:
    @pytest.mark.asyncio
    async def test_la_respuesta_es_un_acuse_no_un_resultado(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch.object(cs_module, "execute", new_callable=AsyncMock) as mock_execute, \
             patch("services.documents.lifecycle.creation.create_document", new_callable=AsyncMock) as mock_create, \
             patch("services.documents.signing.signing.preparar_documento_para_firma",
                   new_callable=AsyncMock, return_value={"estado": "listo", "document": {"status": "draft"}, "original_status": "draft"}), \
             patch.object(cs_module, "enqueue_citizen_signing", new_callable=AsyncMock) as mock_sign, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock) as mock_r2, \
             patch.object(cs_module, "run_in_threadpool", new_callable=AsyncMock) as mock_threadpool:
            mock_fetch_one.return_value = {"id": 1, "type": "HTML", "has_fields": False}
            mock_create.return_value = {"document_id": TEST_DOC_ID}
            mock_sign.side_effect = _respuesta_202
            mock_r2.return_value = MagicMock()
            mock_threadpool.return_value = "https://r2.example/presigned"

            result = await cs_module.create_and_sign_citizen_document(
                "SOLIC", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
            )

        assert result["document_id"] == TEST_DOC_ID
        assert result["status"] == "queued"
        assert result["session_id"], "sin session_id el portal no puede trazar nada"
        assert "official_number" not in result
        assert "pdf_url" not in result


class TestCreateAndSignCitizenDocumentImportado:

    async def test_importado_sin_pdf_base64_levanta_validation_error(self):
        from services.documents.signing import citizen_signing as cs_module
        from shared.exceptions import ValidationError

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = {"id": 1, "type": "Importado", "has_fields": False}
            with pytest.raises(ValidationError, match="requerido"):
                await cs_module.create_and_sign_citizen_document(
                    "ANEXO", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                )

    async def test_no_importado_con_pdf_base64_levanta_validation_error(self):
        from services.documents.signing import citizen_signing as cs_module
        from shared.exceptions import ValidationError

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = {"id": 1, "type": "HTML", "has_fields": False}
            with pytest.raises(ValidationError, match="no está permitido"):
                await cs_module.create_and_sign_citizen_document(
                    "SOLIC", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    pdf_base64="ZmFrZQ==",
                )

    async def test_pdf_base64_invalido_levanta_validation_error(self):
        from services.documents.signing import citizen_signing as cs_module
        from shared.exceptions import ValidationError

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = {"id": 1, "type": "Importado", "has_fields": False}
            with pytest.raises(ValidationError, match="base64"):
                await cs_module.create_and_sign_citizen_document(
                    "ANEXO", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    pdf_base64="no-es-base64-valido-!!!",
                )

    async def test_pdf_base64_no_es_pdf_levanta_validation_error(self):
        import base64 as b64
        from services.documents.signing import citizen_signing as cs_module
        from shared.exceptions import ValidationError

        not_a_pdf = b64.b64encode(b"esto no es un pdf").decode()
        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = {"id": 1, "type": "Importado", "has_fields": False}
            with pytest.raises(ValidationError, match="PDF válido"):
                await cs_module.create_and_sign_citizen_document(
                    "ANEXO", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    pdf_base64=not_a_pdf,
                )

    async def test_pdf_base64_excede_tamano_maximo_levanta_validation_error(self):
        import base64 as b64
        from services.documents.signing import citizen_signing as cs_module
        from shared.exceptions import ValidationError

        oversized_pdf = b64.b64encode(b'%PDF-1.4' + b'0' * (21 * 1024 * 1024)).decode()
        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = {"id": 1, "type": "Importado", "has_fields": False}
            with pytest.raises(ValidationError, match="tamaño máximo"):
                await cs_module.create_and_sign_citizen_document(
                    "ANEXO", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    pdf_base64=oversized_pdf,
                )

    async def test_importado_sube_el_pdf_crudo_y_deja_el_procesado_para_el_worker(self):
        import base64 as b64
        from services.documents.signing import citizen_signing as cs_module

        valid_pdf_b64 = b64.b64encode(VALID_PDF_BYTES).decode()
        processed_pdf_bytes = b'%PDF-1.4 processed by pdfcomposer with end-text'

        async def _fetch_one_side_effect(query, *args, **kwargs):
            if "document_types" in query:
                return {"id": 1, "type": "Importado", "name": "Anexo", "acronym": "ANEXO", "has_fields": False}
            if "settings" in query:
                return {"logo_url": "https://muni.example/logo.png"}
            return None

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch.object(cs_module, "execute", new_callable=AsyncMock) as mock_execute, \
             patch("services.documents.lifecycle.creation.create_document", new_callable=AsyncMock) as mock_create, \
             patch("services.documents.signing.signing.preparar_documento_para_firma",
                   new_callable=AsyncMock, return_value={"estado": "listo", "document": {"status": "draft"}, "original_status": "draft"}), \
             patch.object(cs_module, "enqueue_citizen_signing", new_callable=AsyncMock) as mock_sign, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock) as mock_r2, \
             patch("services.shared.pdfcomposer_api.call_pdfcomposer_import", new_callable=AsyncMock) as mock_composer, \
             patch.object(cs_module, "run_in_threadpool", new_callable=AsyncMock) as mock_threadpool:
            mock_fetch_one.side_effect = _fetch_one_side_effect
            mock_create.return_value = {"document_id": TEST_DOC_ID}
            mock_sign.side_effect = _respuesta_202
            mock_r2_client = MagicMock()
            mock_r2.return_value = mock_r2_client
            mock_composer.return_value = processed_pdf_bytes
            mock_threadpool.return_value = "https://r2.example/presigned"

            result = await cs_module.create_and_sign_citizen_document(
                "ANEXO", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                pdf_base64=valid_pdf_b64,
            )

        mock_execute.assert_not_called()

        mock_composer.assert_not_awaited()

        upload_call = mock_threadpool.call_args_list[0]
        assert upload_call.args[0] == mock_r2_client.upload_tosign
        assert upload_call.args[1] == VALID_PDF_BYTES
        assert upload_call.args[2] == TEST_DOC_ID.replace('-', '') + '_raw.pdf'

        _, enqueue_kwargs = mock_sign.call_args
        pendiente = enqueue_kwargs["payload"]["import_pendiente"]
        assert pendiente["raw_filename"] == TEST_DOC_ID.replace('-', '') + '_raw.pdf'
        assert pendiente["name_acrony_type"] == "ANEXO"
        assert pendiente["document_type"] == "Anexo"
        assert pendiente["reference"] == "Referencia"
        assert pendiente["url_logo"] == "https://muni.example/logo.png"
        assert enqueue_kwargs["payload"]["pdf_pendiente"] is True
        assert result["document_id"] == TEST_DOC_ID
        assert result["status"] == "queued"

    async def test_importado_sin_logo_en_settings_usa_default(self):
        import base64 as b64
        from services.documents.signing import citizen_signing as cs_module
        from config.constants import DEFAULT_LOGO_URL

        valid_pdf_b64 = b64.b64encode(VALID_PDF_BYTES).decode()

        async def _fetch_one_side_effect(query, *args, **kwargs):
            if "document_types" in query:
                return {"id": 1, "type": "Importado", "name": "Anexo", "acronym": "ANEXO", "has_fields": False}
            return None

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch.object(cs_module, "execute", new_callable=AsyncMock), \
             patch("services.documents.lifecycle.creation.create_document", new_callable=AsyncMock) as mock_create, \
             patch("services.documents.signing.signing.preparar_documento_para_firma",
                   new_callable=AsyncMock, return_value={"estado": "listo", "document": {"status": "draft"}, "original_status": "draft"}), \
             patch.object(cs_module, "enqueue_citizen_signing", new_callable=AsyncMock) as mock_sign, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock) as mock_r2, \
             patch("services.shared.pdfcomposer_api.call_pdfcomposer_import", new_callable=AsyncMock) as mock_composer, \
             patch.object(cs_module, "run_in_threadpool", new_callable=AsyncMock) as mock_threadpool:
            mock_fetch_one.side_effect = _fetch_one_side_effect
            mock_create.return_value = {"document_id": TEST_DOC_ID}
            mock_sign.side_effect = _respuesta_202
            mock_r2.return_value = MagicMock()
            mock_composer.return_value = b'%PDF-1.4 processed'
            mock_threadpool.return_value = "https://r2.example/presigned"

            await cs_module.create_and_sign_citizen_document(
                "ANEXO", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                pdf_base64=valid_pdf_b64,
            )

        mock_composer.assert_not_awaited()
        _, enqueue_kwargs = mock_sign.call_args
        assert enqueue_kwargs["payload"]["import_pendiente"]["url_logo"] == DEFAULT_LOGO_URL

    async def test_importado_si_falla_la_subida_del_crudo_levanta_validation_error(self):
        import base64 as b64
        from services.documents.signing import citizen_signing as cs_module
        from shared.exceptions import ValidationError

        valid_pdf_b64 = b64.b64encode(VALID_PDF_BYTES).decode()

        async def _fetch_one_side_effect(query, *args, **kwargs):
            if "document_types" in query:
                return {"id": 1, "type": "Importado", "name": "Anexo", "acronym": "ANEXO", "has_fields": False}
            return {"logo_url": "https://muni.example/logo.png"}

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one,              patch("services.documents.lifecycle.creation.create_document", new_callable=AsyncMock) as mock_create,              patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock) as mock_r2,              patch("services.shared.pdfcomposer_api.call_pdfcomposer_import", new_callable=AsyncMock) as mock_composer,              patch.object(cs_module, "enqueue_citizen_signing", new_callable=AsyncMock) as mock_sign,              patch.object(cs_module, "run_in_threadpool", new_callable=AsyncMock) as mock_threadpool:
            mock_fetch_one.side_effect = _fetch_one_side_effect
            mock_create.return_value = {"document_id": TEST_DOC_ID}
            mock_r2.return_value = MagicMock()
            mock_threadpool.side_effect = Exception("R2 no disponible")

            with pytest.raises(ValidationError, match="subir el PDF"):
                await cs_module.create_and_sign_citizen_document(
                    "ANEXO", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    pdf_base64=valid_pdf_b64,
                )

        mock_composer.assert_not_awaited()
        mock_sign.assert_not_awaited()


class TestExternalSignableTypeGuardBackend:
    async def test_tipo_no_permitido_levanta_validation_error(self):
        from services.documents.signing import citizen_signing as cs_module
        from shared.exceptions import ValidationError

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = {"id": 1, "type": "NOTA", "has_fields": False}
            with pytest.raises(ValidationError, match="Importado, HTML o FFCC"):
                await cs_module.create_and_sign_citizen_document(
                    "NOTAX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                )

    async def test_ffcc_has_fields_true_sin_form_data_levanta_validation_error(self):
        from services.documents.signing import citizen_signing as cs_module
        from shared.exceptions import ValidationError

        doc_type_row = {"id": 1, "type": "HTML", "has_fields": True}
        fields_row = {"field_definitions": [{"name": "campo1", "type": "text", "required": True}]}

        async def _fetch_one(*args, **kwargs):
            return fields_row if "field_definitions FROM document_type_fields" in args[0] else doc_type_row

        with patch("database.fetch_one", new_callable=AsyncMock, side_effect=_fetch_one):
            with pytest.raises(ValidationError, match="form_data"):
                await cs_module.create_and_sign_citizen_document(
                    "FORMX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                )

    async def test_html_sin_has_fields_no_se_rechaza_por_ffcc(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch.object(cs_module, "execute", new_callable=AsyncMock), \
             patch("services.documents.lifecycle.creation.create_document", new_callable=AsyncMock) as mock_create, \
             patch("services.documents.signing.signing.preparar_documento_para_firma",
                   new_callable=AsyncMock, return_value={"estado": "listo", "document": {"status": "draft"}, "original_status": "draft"}), \
             patch.object(cs_module, "enqueue_citizen_signing", new_callable=AsyncMock) as mock_sign, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock) as mock_r2, \
             patch.object(cs_module, "run_in_threadpool", new_callable=AsyncMock) as mock_threadpool:
            mock_fetch_one.return_value = {"id": 1, "type": "HTML", "has_fields": False}
            mock_create.return_value = {"document_id": TEST_DOC_ID}
            mock_sign.side_effect = _respuesta_202
            mock_r2.return_value = MagicMock()
            mock_threadpool.return_value = "https://r2.example/presigned"

            result = await cs_module.create_and_sign_citizen_document(
                "SOLIC", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
            )

        assert result["document_id"] == TEST_DOC_ID


class TestTadDocumentTypesEndpointFiltersByType:
    async def test_query_filtra_por_type_in_html_importado(self):
        from unittest.mock import MagicMock as _MagicMock
        from api_gateway import rest_api_tad

        mock_request = _MagicMock()
        mock_request.headers = {"X-API-Key": "fake-key"}

        with patch.object(rest_api_tad, "validate_tad_api_key", new_callable=AsyncMock) as mock_auth, \
             patch.object(rest_api_tad, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
            mock_auth.return_value = (TEST_SCHEMA, None)
            mock_fetch_all.return_value = []

            await rest_api_tad.api_tad_get_document_types(mock_request)

        query_arg = mock_fetch_all.call_args.args[0]
        assert "type IN ('HTML', 'Importado')" in query_arg
        assert "external_signable = true" in query_arg

    async def test_query_incluye_has_fields(self):
        from unittest.mock import MagicMock as _MagicMock
        from api_gateway import rest_api_tad

        mock_request = _MagicMock()
        mock_request.headers = {"X-API-Key": "fake-key"}

        with patch.object(rest_api_tad, "validate_tad_api_key", new_callable=AsyncMock) as mock_auth, \
             patch.object(rest_api_tad, "fetch_all", new_callable=AsyncMock) as mock_fetch_all:
            mock_auth.return_value = (TEST_SCHEMA, None)
            mock_fetch_all.return_value = [{"id": 1, "name": "Formulario", "acronym": "FORMX",
                                             "description": None, "has_fields": True}]

            response = await rest_api_tad.api_tad_get_document_types(mock_request)

        query_arg = mock_fetch_all.call_args.args[0]
        assert "has_fields" in query_arg
        assert "document_type_fields" in query_arg

        import json
        body = json.loads(response.body)
        assert body["document_types"][0]["has_fields"] is True


class TestTadDocumentTypeFieldsEndpoint:

    def _mock_request(self, doc_type_id="1"):
        from unittest.mock import MagicMock as _MagicMock
        mock_request = _MagicMock()
        mock_request.headers = {"X-API-Key": "fake-key"}
        mock_request.path_params = {"id": doc_type_id}
        return mock_request

    async def test_id_no_numerico_devuelve_404(self):
        from api_gateway import rest_api_tad

        with patch.object(rest_api_tad, "validate_tad_api_key", new_callable=AsyncMock) as mock_auth:
            mock_auth.return_value = (TEST_SCHEMA, None)
            response = await rest_api_tad.api_tad_get_document_type_fields(self._mock_request("no-es-un-id"))

        assert response.status_code == 404

    async def test_tipo_inexistente_o_no_signable_devuelve_404(self):
        from api_gateway import rest_api_tad

        with patch.object(rest_api_tad, "validate_tad_api_key", new_callable=AsyncMock) as mock_auth, \
             patch.object(rest_api_tad, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_auth.return_value = (TEST_SCHEMA, None)
            mock_fetch_one.return_value = None

            response = await rest_api_tad.api_tad_get_document_type_fields(self._mock_request("999"))

        assert response.status_code == 404

    async def test_tipo_sin_fila_en_document_type_fields_devuelve_404(self):
        from api_gateway import rest_api_tad

        with patch.object(rest_api_tad, "validate_tad_api_key", new_callable=AsyncMock) as mock_auth, \
             patch.object(rest_api_tad, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_auth.return_value = (TEST_SCHEMA, None)
            mock_fetch_one.side_effect = [{"id": 1}, None]

            response = await rest_api_tad.api_tad_get_document_type_fields(self._mock_request("1"))

        assert response.status_code == 404

    async def test_happy_path_devuelve_field_definitions(self):
        from api_gateway import rest_api_tad
        import json

        field_defs = [{"name": "nombre", "label": "Nombre", "type": "text", "required": True}]

        with patch.object(rest_api_tad, "validate_tad_api_key", new_callable=AsyncMock) as mock_auth, \
             patch.object(rest_api_tad, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_auth.return_value = (TEST_SCHEMA, None)
            mock_fetch_one.side_effect = [{"id": 1}, {"field_definitions": field_defs}]

            response = await rest_api_tad.api_tad_get_document_type_fields(self._mock_request("1"))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["document_type_id"] == 1
        assert body["field_definitions"] == field_defs
