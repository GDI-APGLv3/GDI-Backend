import base64 as b64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.exceptions import ValidationError


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

_VALID_TXT_B64 = b64.b64encode(b"hola mundo").decode()

_FIELD_DEFS_SIMPLE = [
    {"name": "nombre", "label": "Nombre", "type": "text", "required": True},
    {"name": "edad", "label": "Edad", "type": "number", "required": False},
]

_FIELD_DEFS_SELECT = [
    {"name": "categoria", "label": "Categoria", "type": "select", "required": True,
     "options": ["A", "B", "C"]},
]

_FIELD_DEFS_FILE_OPCIONAL = [
    {"name": "adjunto", "label": "Adjunto", "type": "file", "required": False},
]

_FIELD_DEFS_FILE_REQUERIDO = [
    {"name": "adjunto", "label": "Adjunto", "type": "file", "required": True},
]


def _fetch_one_router(doc_type_row: dict, fields_row):
    async def _fetch_one(query, *args, **kwargs):
        if "field_definitions FROM document_type_fields" in query:
            return fields_row
        return doc_type_row
    return _fetch_one


def _ffcc_doc_type(document_type_id=1, accepts_embedded_files=False):
    return {
        "id": document_type_id, "type": "HTML", "name": "Formulario", "acronym": "FORMX",
        "accepts_embedded_files": accepts_embedded_files, "has_fields": True,
    }


class TestFfccFormDataGuards:
    async def test_form_data_faltante_en_tipo_ffcc_levanta_400(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock,
                    side_effect=_fetch_one_router(_ffcc_doc_type(), {"field_definitions": _FIELD_DEFS_SIMPLE})):
            with pytest.raises(ValidationError, match="form_data"):
                await cs_module.create_and_sign_citizen_document(
                    "FORMX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                )

    async def test_form_data_en_tipo_no_ffcc_levanta_400(self):
        from services.documents.signing import citizen_signing as cs_module

        doc_type = {"id": 1, "type": "HTML", "accepts_embedded_files": False, "has_fields": False}
        with patch("database.fetch_one", new_callable=AsyncMock,
                    side_effect=_fetch_one_router(doc_type, None)):
            with pytest.raises(ValidationError, match="form_data"):
                await cs_module.create_and_sign_citizen_document(
                    "SOLIC", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    form_data={"nombre": "Juan"},
                )

    async def test_form_data_y_content_html_son_excluyentes(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock,
                    side_effect=_fetch_one_router(_ffcc_doc_type(), {"field_definitions": _FIELD_DEFS_SIMPLE})):
            with pytest.raises(ValidationError, match="excluyentes"):
                await cs_module.create_and_sign_citizen_document(
                    "FORMX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    content_html="<p>hola</p>", form_data={"nombre": "Juan"},
                )

    async def test_form_data_y_pdf_base64_son_excluyentes(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock,
                    side_effect=_fetch_one_router(_ffcc_doc_type(), {"field_definitions": _FIELD_DEFS_SIMPLE})):
            with pytest.raises(ValidationError, match="excluyentes"):
                await cs_module.create_and_sign_citizen_document(
                    "FORMX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    pdf_base64=b64.b64encode(b"%PDF-1.4 fake").decode(), form_data={"nombre": "Juan"},
                )

    async def test_campo_file_required_bloquea_el_tipo_siempre(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock,
                    side_effect=_fetch_one_router(_ffcc_doc_type(), {"field_definitions": _FIELD_DEFS_FILE_REQUERIDO})):
            with pytest.raises(ValidationError, match="obligatorio"):
                await cs_module.create_and_sign_citizen_document(
                    "FORMX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    form_data={"adjunto": "11111111-1111-1111-1111-111111111111"},
                )

    async def test_campo_file_opcional_con_valor_no_vacio_levanta_400(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock,
                    side_effect=_fetch_one_router(_ffcc_doc_type(), {"field_definitions": _FIELD_DEFS_FILE_OPCIONAL})):
            with pytest.raises(ValidationError, match="archivo"):
                await cs_module.create_and_sign_citizen_document(
                    "FORMX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    form_data={"adjunto": "11111111-1111-1111-1111-111111111111"},
                )

    async def test_requerido_faltante_levanta_400(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock,
                    side_effect=_fetch_one_router(_ffcc_doc_type(), {"field_definitions": _FIELD_DEFS_SIMPLE})):
            with pytest.raises(ValidationError, match="requerido"):
                await cs_module.create_and_sign_citizen_document(
                    "FORMX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    form_data={"edad": 30},
                )

    async def test_tipo_de_dato_invalido_levanta_400(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock,
                    side_effect=_fetch_one_router(_ffcc_doc_type(), {"field_definitions": _FIELD_DEFS_SIMPLE})):
            with pytest.raises(ValidationError, match="numero"):
                await cs_module.create_and_sign_citizen_document(
                    "FORMX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    form_data={"nombre": "Juan", "edad": "no-es-un-numero"},
                )

    async def test_select_fuera_de_options_levanta_400(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock,
                    side_effect=_fetch_one_router(_ffcc_doc_type(), {"field_definitions": _FIELD_DEFS_SELECT})):
            with pytest.raises(ValidationError, match="uno de"):
                await cs_module.create_and_sign_citizen_document(
                    "FORMX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    form_data={"categoria": "Z"},
                )


class TestFfccHappyPath:
    async def _run_happy_path(self, *, form_data, embedded_files=None, accepts_embedded_files=False,
                               field_definitions=None):
        from services.documents.signing import citizen_signing as cs_module

        doc_type = _ffcc_doc_type(accepts_embedded_files=accepts_embedded_files)
        fields_row = {"field_definitions": field_definitions or _FIELD_DEFS_SIMPLE}

        with patch("database.fetch_one", new_callable=AsyncMock,
                    side_effect=_fetch_one_router(doc_type, fields_row)), \
             patch.object(cs_module, "execute", new_callable=AsyncMock) as mock_execute, \
             patch("services.documents.lifecycle.creation.create_document", new_callable=AsyncMock) as mock_create, \
             patch("services.documents.lifecycle.embedded_files.upload_embedded_files_for_citizen_document",
                   new_callable=AsyncMock) as mock_upload, \
             patch("services.documents.signing.signing.preparar_documento_para_firma",
                   new_callable=AsyncMock, return_value={"estado": "listo", "document": {"status": "draft"}, "original_status": "draft"}) as mock_start_signing, \
             patch.object(cs_module, "enqueue_citizen_signing", new_callable=AsyncMock) as mock_sign, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock) as mock_r2, \
             patch.object(cs_module, "run_in_threadpool", new_callable=AsyncMock) as mock_threadpool:
            mock_create.return_value = {"document_id": TEST_DOC_ID}
            mock_sign.side_effect = _respuesta_202
            mock_r2.return_value = MagicMock()
            mock_threadpool.return_value = "https://r2.example/presigned"

            result = await cs_module.create_and_sign_citizen_document(
                "FORMX", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                form_data=form_data, embedded_files=embedded_files,
            )

        return result, mock_execute, mock_upload, mock_start_signing

    async def test_happy_path_persiste_form_data_plano_sin_envolver(self):
        form_data = {"nombre": "Juan", "edad": 30}
        result, mock_execute, mock_upload, mock_start_signing = await self._run_happy_path(form_data=form_data)

        assert result["document_id"] == TEST_DOC_ID
        mock_execute.assert_awaited_once()
        persisted_content = mock_execute.call_args.args[1]
        assert persisted_content == form_data
        mock_start_signing.assert_awaited_once()

    async def test_ffcc_con_embedded_files_ok(self):
        embedded_files = [{"file_name": "notas.txt", "content_base64": _VALID_TXT_B64}]
        result, mock_execute, mock_upload, mock_start_signing = await self._run_happy_path(
            form_data={"nombre": "Juan", "edad": 30},
            embedded_files=embedded_files,
            accepts_embedded_files=True,
        )

        assert result["document_id"] == TEST_DOC_ID
        mock_upload.assert_awaited_once()
        upload_args = mock_upload.call_args.args
        assert upload_args[0] == TEST_DOC_ID
        assert upload_args[2] == [("notas.txt", b"hola mundo")]
        persisted_content = mock_execute.call_args.args[1]
        assert persisted_content == {"nombre": "Juan", "edad": 30}

    async def test_ffcc_con_campo_file_opcional_en_null_pasa(self):
        form_data = {"adjunto": None}
        result, mock_execute, mock_upload, mock_start_signing = await self._run_happy_path(
            form_data=form_data, field_definitions=_FIELD_DEFS_FILE_OPCIONAL,
        )

        assert result["document_id"] == TEST_DOC_ID
        mock_start_signing.assert_awaited_once()


class TestFfccSnapshotAlFirmar:
    async def test_snapshot_correcto_se_pasa_a_reserve_citizen_number(self):
        from services.documents.signing import citizen_signing as cs_module

        raw_data = {"nombre": "Juan", "edad": 30}
        signer_info = {
            "is_numerator": True, "signer_status": "pending", "doc_status": "sent_to_sign",
            "reference": "Referencia", "content": raw_data, "document_type_id": 1,
            "resume": None, "document_type_acronym": "FORMX", "source_type": "HTML",
            "special_numbering": False,
        }

        def _fetch_one_side_effect(query, *args, **kwargs):
            if "field_definitions FROM document_type_fields" in query:
                return {"field_definitions": _FIELD_DEFS_SIMPLE}
            return signer_info

        async def _fetch_one(*args, **kwargs):
            return _fetch_one_side_effect(*args, **kwargs)

        with patch.object(cs_module, "fetch_one", new_callable=AsyncMock, side_effect=_fetch_one) as mock_fetch_one, \
             patch.object(cs_module, "execute", new_callable=AsyncMock), \
             patch.object(cs_module, "reserve_citizen_number", new_callable=AsyncMock) as mock_reserve, \
             patch.object(cs_module, "confirm_number", new_callable=AsyncMock), \
             patch.object(cs_module, "finalize_number", new_callable=AsyncMock), \
             patch.object(cs_module, "cancel_number", new_callable=AsyncMock), \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock) as mock_r2, \
             patch.object(cs_module, "run_in_threadpool", new_callable=AsyncMock) as mock_threadpool, \
             patch.object(cs_module, "get_citizen_signer_data", new_callable=AsyncMock) as mock_signer_data, \
             patch("services.shared.notary_api.call_notary_sign_pdf", new_callable=AsyncMock) as mock_notary, \
             patch("services.shared.settings_utils.get_city_from_settings", new_callable=AsyncMock), \
             patch("services.storage.publish_public.maybe_publish_official_pdf", new_callable=AsyncMock), \
             patch("services.documents.lifecycle.embedded_files.promote_embedded_files_to_official",
                   new_callable=AsyncMock), \
             patch.object(cs_module, "transaction") as mock_tx:
            mock_reserve.return_value = ("FORMX-2026-00000001-MUNI-TAD", "dept-id", 1, "reservation-id")
            mock_r2.return_value = MagicMock()

            async def _threadpool_side_effect(fn, *args, **kwargs):
                return "https://r2.example/tosign-url"
            mock_threadpool.side_effect = _threadpool_side_effect

            mock_signer_data.return_value = {
                "full_name": "Maria Vecina", "seal": "sello", "department_name": "Depto",
                "municipality_name": "Muni",
            }
            mock_notary.return_value = b"%PDF-1.4 signed"

            class _FakeHttpxClient:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    return False
                async def get(self, url):
                    resp = MagicMock()
                    resp.raise_for_status = MagicMock()
                    resp.content = b"%PDF-1.4 unsigned"
                    return resp

            with patch("httpx.AsyncClient", return_value=_FakeHttpxClient()):
                mock_conn = MagicMock()
                mock_conn.execute = AsyncMock(return_value="UPDATE 1")
                mock_conn.fetch = AsyncMock(return_value=[{"id": TEST_DOC_ID}])

                class _FakeTx:
                    async def __aenter__(self):
                        return mock_conn
                    async def __aexit__(self, *a):
                        return False
                mock_tx.return_value = _FakeTx()

                result = await cs_module.sign_and_number_citizen_document(
                    TEST_DOC_ID, TEST_CITIZEN_ID, schema_name=TEST_SCHEMA,
                )

        assert result["success"] is True

        reserve_kwargs = mock_reserve.call_args.kwargs
        assert reserve_kwargs["content"] == {"schema": _FIELD_DEFS_SIMPLE, "data": raw_data}
