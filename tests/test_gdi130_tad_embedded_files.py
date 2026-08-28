import base64 as b64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.exceptions import ValidationError, DocumentNotFoundError, AuthorizationError


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

_VALID_PNG_B64 = b64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()
_VALID_TXT_B64 = b64.b64encode(b"hola mundo").decode()


def _minimal_valid_pdf_b64() -> str:
    import io
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return b64.b64encode(buf.getvalue()).decode()


class TestDecodeAndValidateEmbeddedFiles:
    def _doc_type(self, accepts=True):
        return {"id": 1, "type": "HTML", "name": "Solicitud", "acronym": "SOLIC",
                "has_fields": False, "accepts_embedded_files": accepts}

    def test_none_o_vacio_retorna_lista_vacia(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files

        assert _decode_and_validate_embedded_files(
            None, doc_type=self._doc_type(), is_imported=False, document_type_acronym="SOLIC"
        ) == []
        assert _decode_and_validate_embedded_files(
            [], doc_type=self._doc_type(), is_imported=False, document_type_acronym="SOLIC"
        ) == []

    def test_tipo_importado_con_embedded_files_levanta_400(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files

        with pytest.raises(ValidationError, match="Importado"):
            _decode_and_validate_embedded_files(
                [{"file_name": "a.txt", "content_base64": _VALID_TXT_B64}],
                doc_type=self._doc_type(), is_imported=True, document_type_acronym="ANEXO",
            )

    def test_tipo_no_acepta_embebidos_levanta_400(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files

        with pytest.raises(ValidationError, match="no admite"):
            _decode_and_validate_embedded_files(
                [{"file_name": "a.txt", "content_base64": _VALID_TXT_B64}],
                doc_type=self._doc_type(accepts=False), is_imported=False, document_type_acronym="SOLIC",
            )

    def test_shape_invalido_no_es_lista_levanta_400(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files

        with pytest.raises(ValidationError, match="lista"):
            _decode_and_validate_embedded_files(
                {"file_name": "a.txt"}, doc_type=self._doc_type(), is_imported=False,
                document_type_acronym="SOLIC",
            )

    def test_item_sin_file_name_levanta_400(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files

        with pytest.raises(ValidationError, match="file_name"):
            _decode_and_validate_embedded_files(
                [{"content_base64": _VALID_TXT_B64}], doc_type=self._doc_type(), is_imported=False,
                document_type_acronym="SOLIC",
            )

    def test_item_sin_content_base64_levanta_400(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files

        with pytest.raises(ValidationError, match="content_base64"):
            _decode_and_validate_embedded_files(
                [{"file_name": "a.txt"}], doc_type=self._doc_type(), is_imported=False,
                document_type_acronym="SOLIC",
            )

    def test_base64_invalido_levanta_400(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files

        with pytest.raises(ValidationError, match="base64"):
            _decode_and_validate_embedded_files(
                [{"file_name": "a.txt", "content_base64": "no-es-base64-!!!"}],
                doc_type=self._doc_type(), is_imported=False, document_type_acronym="SOLIC",
            )

    def test_excede_cantidad_maxima_levanta_400(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files
        from config.constants import MAX_EMBEDDED_FILES_PER_DOCUMENT

        files = [{"file_name": f"a{i}.txt", "content_base64": _VALID_TXT_B64}
                 for i in range(MAX_EMBEDDED_FILES_PER_DOCUMENT + 1)]
        with pytest.raises(ValidationError, match="máximo"):
            _decode_and_validate_embedded_files(
                files, doc_type=self._doc_type(), is_imported=False, document_type_acronym="SOLIC",
            )

    def test_excede_tamano_individual_levanta_400(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files
        from config.constants import MAX_EMBEDDED_FILE_SIZE

        oversized_b64 = b64.b64encode(b"0" * (MAX_EMBEDDED_FILE_SIZE + 1024)).decode()
        with pytest.raises(ValidationError, match="tamaño máximo"):
            _decode_and_validate_embedded_files(
                [{"file_name": "a.txt", "content_base64": oversized_b64}],
                doc_type=self._doc_type(), is_imported=False, document_type_acronym="SOLIC",
            )

    def test_extension_no_permitida_levanta_400(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files

        with pytest.raises(ValidationError, match="permitida"):
            _decode_and_validate_embedded_files(
                [{"file_name": "malware.exe", "content_base64": _VALID_TXT_B64}],
                doc_type=self._doc_type(), is_imported=False, document_type_acronym="SOLIC",
            )

    def test_contenido_no_corresponde_a_extension_levanta_400(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files

        with pytest.raises(ValidationError, match="PNG"):
            _decode_and_validate_embedded_files(
                [{"file_name": "falso.png", "content_base64": _VALID_TXT_B64}],
                doc_type=self._doc_type(), is_imported=False, document_type_acronym="SOLIC",
            )

    def test_happy_path_decodifica_correctamente(self):
        from services.documents.signing.citizen_signing import _decode_and_validate_embedded_files

        result = _decode_and_validate_embedded_files(
            [
                {"file_name": "foto.png", "content_base64": _VALID_PNG_B64},
                {"file_name": "notas.txt", "content_base64": _VALID_TXT_B64},
            ],
            doc_type=self._doc_type(), is_imported=False, document_type_acronym="SOLIC",
        )
        assert result == [
            ("foto.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20),
            ("notas.txt", b"hola mundo"),
        ]


class TestUploadEmbeddedFilesForCitizenDocument:
    async def test_documento_no_encontrado_levanta_404(self):
        from services.documents.lifecycle import embedded_files as ef_module

        with patch.object(ef_module, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = None
            with pytest.raises(DocumentNotFoundError):
                await ef_module.upload_embedded_files_for_citizen_document(
                    TEST_DOC_ID, TEST_CITIZEN_ID, [("a.txt", b"hola")], schema_name=TEST_SCHEMA,
                )

    async def test_documento_de_otro_ciudadano_levanta_403(self):
        from services.documents.lifecycle import embedded_files as ef_module

        with patch.object(ef_module, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = {
                "id": TEST_DOC_ID, "created_by_citizen": "otro-ciudadano-id",
                "accepts_embedded_files": True,
            }
            with pytest.raises(AuthorizationError):
                await ef_module.upload_embedded_files_for_citizen_document(
                    TEST_DOC_ID, TEST_CITIZEN_ID, [("a.txt", b"hola")], schema_name=TEST_SCHEMA,
                )

    async def test_tipo_no_acepta_embebidos_levanta_400(self):
        from services.documents.lifecycle import embedded_files as ef_module

        with patch.object(ef_module, "fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = {
                "id": TEST_DOC_ID, "created_by_citizen": TEST_CITIZEN_ID,
                "accepts_embedded_files": False,
            }
            with pytest.raises(ValidationError, match="no admite"):
                await ef_module.upload_embedded_files_for_citizen_document(
                    TEST_DOC_ID, TEST_CITIZEN_ID, [("a.txt", b"hola")], schema_name=TEST_SCHEMA,
                )

    async def test_happy_path_inserta_y_sube_a_r2(self):
        from services.documents.lifecycle import embedded_files as ef_module

        with patch.object(ef_module, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch.object(ef_module, "execute", new_callable=AsyncMock) as mock_execute, \
             patch.object(ef_module, "r2_put", new_callable=AsyncMock) as mock_r2_put:
            mock_fetch_one.return_value = {
                "id": TEST_DOC_ID, "created_by_citizen": TEST_CITIZEN_ID,
                "accepts_embedded_files": True,
            }

            result = await ef_module.upload_embedded_files_for_citizen_document(
                TEST_DOC_ID, TEST_CITIZEN_ID,
                [("foto.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20)],
                schema_name=TEST_SCHEMA,
            )

        assert len(result) == 1
        assert result[0]["file_name"] == "foto.png"
        assert result[0]["extension"] == "png"

        insert_call = mock_execute.call_args_list[0]
        assert "created_by_citizen" in insert_call.args[0]
        assert "VALUES ($1, $2, $3, $4, $5, $6, $7)" in insert_call.args[0]
        assert len(insert_call.args) == 8
        assert insert_call.args[-1] == TEST_CITIZEN_ID

        mock_r2_put.assert_awaited_once()
        _, put_kwargs = mock_r2_put.call_args
        assert put_kwargs["key"].startswith(f"editing/{TEST_DOC_ID}/")

    async def test_r2_put_falla_revierte_fila_insertada(self):
        from services.documents.lifecycle import embedded_files as ef_module

        with patch.object(ef_module, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch.object(ef_module, "execute", new_callable=AsyncMock) as mock_execute, \
             patch.object(ef_module, "r2_put", new_callable=AsyncMock) as mock_r2_put:
            mock_fetch_one.return_value = {
                "id": TEST_DOC_ID, "created_by_citizen": TEST_CITIZEN_ID,
                "accepts_embedded_files": True,
            }
            mock_r2_put.side_effect = Exception("R2 no disponible")

            with pytest.raises(ValidationError, match="No se pudo subir"):
                await ef_module.upload_embedded_files_for_citizen_document(
                    TEST_DOC_ID, TEST_CITIZEN_ID,
                    [("notas.txt", b"hola mundo")],
                    schema_name=TEST_SCHEMA,
                )

        assert mock_execute.await_count == 2
        delete_query = mock_execute.call_args_list[1].args[0]
        assert "DELETE FROM document_draft_embedded_files" in delete_query


class TestPromoteEmbeddedFilesToOfficialPropagatesCitizenAuthor:

    async def test_propaga_created_by_citizen_al_oficial(self):
        from services.documents.lifecycle import embedded_files as ef_module

        draft_row = {
            "id": "f1000000-0000-0000-0000-000000000001",
            "r2_key": f"editing/{TEST_DOC_ID}/f1/notas.txt",
            "file_name": "notas.txt",
            "file_size": 10,
            "extension": "txt",
            "created_by": None,
            "created_by_citizen": TEST_CITIZEN_ID,
            "created_at": None,
        }

        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_transaction(*args, **kwargs):
            yield mock_conn

        with patch.object(ef_module, "fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
             patch.object(ef_module, "transaction", _fake_transaction), \
             patch.object(ef_module, "execute", new_callable=AsyncMock), \
             patch.object(ef_module, "r2_delete", new_callable=AsyncMock):
            mock_fetch_all.return_value = [draft_row]

            await ef_module.promote_embedded_files_to_official(
                TEST_DOC_ID, TEST_DOC_ID, schema_name=TEST_SCHEMA,
            )

        mock_conn.execute.assert_awaited_once()
        insert_args = mock_conn.execute.call_args.args
        assert "created_by_citizen" in insert_args[0]
        assert insert_args[6] is None
        assert insert_args[7] == TEST_CITIZEN_ID


class TestCreateAndSignCitizenDocumentEmbeddedFilesWiring:
    async def test_happy_path_llama_upload_antes_de_start_signing(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch.object(cs_module, "execute", new_callable=AsyncMock), \
             patch("services.documents.lifecycle.creation.create_document", new_callable=AsyncMock) as mock_create, \
             patch("services.documents.lifecycle.embedded_files.upload_embedded_files_for_citizen_document", new_callable=AsyncMock) as mock_upload, \
             patch("services.documents.signing.signing.preparar_documento_para_firma",
                   new_callable=AsyncMock, return_value={"estado": "listo", "document": {"status": "draft"}, "original_status": "draft"}) as mock_start_signing, \
             patch.object(cs_module, "enqueue_citizen_signing", new_callable=AsyncMock) as mock_sign, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock) as mock_r2, \
             patch.object(cs_module, "run_in_threadpool", new_callable=AsyncMock) as mock_threadpool:
            mock_fetch_one.return_value = {
                "id": 1, "type": "HTML", "has_fields": False, "accepts_embedded_files": True,
            }
            mock_create.return_value = {"document_id": TEST_DOC_ID}
            mock_sign.side_effect = _respuesta_202
            mock_r2.return_value = MagicMock()
            mock_threadpool.return_value = "https://r2.example/presigned"

            result = await cs_module.create_and_sign_citizen_document(
                "SOLIC", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                embedded_files=[{"file_name": "notas.txt", "content_base64": _VALID_TXT_B64}],
            )

        mock_upload.assert_awaited_once()
        upload_args = mock_upload.call_args.args
        assert upload_args[0] == TEST_DOC_ID
        assert upload_args[1] == TEST_CITIZEN_ID
        assert upload_args[2] == [("notas.txt", b"hola mundo")]

        assert mock_upload.call_count == 1
        mock_start_signing.assert_awaited_once()

        assert result["document_id"] == TEST_DOC_ID

    async def test_sin_embedded_files_no_llama_upload(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch.object(cs_module, "execute", new_callable=AsyncMock), \
             patch("services.documents.lifecycle.creation.create_document", new_callable=AsyncMock) as mock_create, \
             patch("services.documents.lifecycle.embedded_files.upload_embedded_files_for_citizen_document", new_callable=AsyncMock) as mock_upload, \
             patch("services.documents.signing.signing.preparar_documento_para_firma",
                   new_callable=AsyncMock, return_value={"estado": "listo", "document": {"status": "draft"}, "original_status": "draft"}), \
             patch.object(cs_module, "enqueue_citizen_signing", new_callable=AsyncMock) as mock_sign, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock) as mock_r2, \
             patch.object(cs_module, "run_in_threadpool", new_callable=AsyncMock) as mock_threadpool:
            mock_fetch_one.return_value = {
                "id": 1, "type": "HTML", "has_fields": False, "accepts_embedded_files": True,
            }
            mock_create.return_value = {"document_id": TEST_DOC_ID}
            mock_sign.side_effect = _respuesta_202
            mock_r2.return_value = MagicMock()
            mock_threadpool.return_value = "https://r2.example/presigned"

            await cs_module.create_and_sign_citizen_document(
                "SOLIC", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
            )

        mock_upload.assert_not_called()

    async def test_importado_con_embedded_files_levanta_400_antes_de_crear_draft(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("services.documents.lifecycle.creation.create_document", new_callable=AsyncMock) as mock_create:
            mock_fetch_one.return_value = {
                "id": 1, "type": "Importado", "has_fields": False, "accepts_embedded_files": True,
            }
            with pytest.raises(ValidationError, match="Importado"):
                await cs_module.create_and_sign_citizen_document(
                    "ANEXO", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    pdf_base64=_minimal_valid_pdf_b64(),
                    embedded_files=[{"file_name": "notas.txt", "content_base64": _VALID_TXT_B64}],
                )

        mock_create.assert_not_called()

    async def test_tipo_no_acepta_embebidos_levanta_400_antes_de_crear_draft(self):
        from services.documents.signing import citizen_signing as cs_module

        with patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("services.documents.lifecycle.creation.create_document", new_callable=AsyncMock) as mock_create:
            mock_fetch_one.return_value = {
                "id": 1, "type": "HTML", "has_fields": False, "accepts_embedded_files": False,
            }
            with pytest.raises(ValidationError, match="no admite"):
                await cs_module.create_and_sign_citizen_document(
                    "SOLIC", TEST_CITIZEN_ID, "Referencia", schema_name=TEST_SCHEMA,
                    embedded_files=[{"file_name": "notas.txt", "content_base64": _VALID_TXT_B64}],
                )

        mock_create.assert_not_called()

    async def test_sign_and_number_promueve_embedded_files_a_oficial(self):
        from services.documents.signing import citizen_signing as cs_module

        signer_info = {
            "is_numerator": True, "signer_status": "pending", "doc_status": "sent_to_sign",
            "reference": "Referencia", "content": {"html": "<p>x</p>"}, "document_type_id": 1,
            "resume": None, "document_type_acronym": "SOLIC", "source_type": "HTML",
            "special_numbering": False,
        }

        with patch.object(cs_module, "fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
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
             patch("services.documents.lifecycle.embedded_files.promote_embedded_files_to_official", new_callable=AsyncMock) as mock_promote, \
             patch.object(cs_module, "transaction") as mock_tx:
            mock_fetch_one.return_value = signer_info
            mock_reserve.return_value = ("SOLIC-2026-00000001-MUNI-TAD", "dept-id", 1, "reservation-id")
            mock_r2.return_value = MagicMock()

            import httpx as _httpx  # noqa: F401 (solo para claridad del mock de abajo)

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

            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _fake_tx(*args, **kwargs):
                conn = AsyncMock()
                conn.execute = AsyncMock()
                conn.fetch = AsyncMock(return_value=[{"id": TEST_DOC_ID}])
                yield conn
            mock_tx.side_effect = _fake_tx

            with patch("httpx.AsyncClient", return_value=_FakeHttpxClient()):
                result = await cs_module.sign_and_number_citizen_document(
                    TEST_DOC_ID, TEST_CITIZEN_ID, schema_name=TEST_SCHEMA,
                )

        assert result["success"] is True
        mock_promote.assert_awaited_once_with(TEST_DOC_ID, TEST_DOC_ID, schema_name=TEST_SCHEMA)
