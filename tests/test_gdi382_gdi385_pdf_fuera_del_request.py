import ast
import inspect
from unittest.mock import AsyncMock, patch

import pytest


def solo_codigo(src: str) -> str:
    arbol = ast.parse(src.lstrip())
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(nodo, clean=False)
            if doc and nodo.body and isinstance(nodo.body[0], ast.Expr):
                nodo.body.pop(0)
                if not nodo.body:
                    nodo.body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(arbol))


DOC_ID = "d0c00000-0000-0000-0000-00000000000a"
USER_ID = "5e400000-0000-0000-0000-00000000000a"
OTRO_USER = "07200000-0000-0000-0000-00000000000b"


def _doc(status="sent_to_sign", sent_to_sign_at="2026-08-25T10:00:00", sent_by=USER_ID):
    return {
        "document_id": DOC_ID,
        "reference": "Solicitud de prueba",
        "status": status,
        "content": {"html": "<p>hola</p>"},
        "created_by": USER_ID,
        "created_by_citizen": None,
        "sent_to_sign_at": sent_to_sign_at,
        "sent_by": sent_by,
        "document_type_id": "d7000000-0000-0000-0000-00000000000a",
        "type_name": "Informe",
        "type_acronym": "IF",
        "source_type": "HTML",
        "has_fields": False,
    }


class TestElReintentoDejaDeMentir:

    @pytest.mark.asyncio
    async def test_si_ya_termino_y_lo_reintenta_el_mismo_actor_responde_idempotente(self):
        from services.documents.signing.signing import preparar_documento_para_firma

        with patch("services.documents.signing.signing.fetch_one",
                   AsyncMock(return_value=_doc())):
            resultado = await preparar_documento_para_firma(
                DOC_ID, USER_ID, schema_name="100_test",
            )

        assert resultado["estado"] == "ya_terminado"
        assert resultado["respuesta"]["success"] is True
        assert resultado["respuesta"]["api_mode"] == "idempotent"

    @pytest.mark.asyncio
    async def test_el_camino_idempotente_no_regenera_el_pdf(self):
        from services.documents.signing import signing

        pdf = AsyncMock()
        with (
            patch.object(signing, "fetch_one", AsyncMock(return_value=_doc())),
            patch.object(signing, "generate_final_document_pdf", pdf),
        ):
            await signing.start_document_signing_process(
                DOC_ID, USER_ID, schema_name="100_test",
            )

        pdf.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_si_esta_en_curso_rechaza_con_mensaje_propio_nunca_200(self):
        from services.documents.signing.signing import preparar_documento_para_firma
        from shared.exceptions import DocumentStateError
        from config.constants import START_SIGNING_EN_CURSO_ERROR

        with patch("services.documents.signing.signing.fetch_one",
                   AsyncMock(return_value=_doc(sent_to_sign_at=None, sent_by=None))):
            with pytest.raises(DocumentStateError) as exc:
                await preparar_documento_para_firma(
                    DOC_ID, USER_ID, schema_name="100_test",
                )

        assert str(exc.value) == START_SIGNING_EN_CURSO_ERROR
        assert "en curso" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_un_tercero_no_recibe_el_idempotente(self):
        from services.documents.signing.signing import preparar_documento_para_firma
        from shared.exceptions import DocumentStateError
        from config.constants import START_SIGNING_EN_CURSO_ERROR

        with patch("services.documents.signing.signing.fetch_one",
                   AsyncMock(return_value=_doc(sent_by=OTRO_USER))):
            with pytest.raises(DocumentStateError) as exc:
                await preparar_documento_para_firma(
                    DOC_ID, OTRO_USER + "-no", schema_name="100_test",
                )

        assert str(exc.value) != START_SIGNING_EN_CURSO_ERROR
        assert "no puede iniciarse para firma" in str(exc.value)

    def test_la_query_trae_el_discriminante(self):
        from services.documents.core.queries import get_document_for_signing_start_query

        sql = get_document_for_signing_start_query()
        assert "sent_to_sign_at" in sql
        assert "sent_by" in sql

    def test_solo_el_camino_de_exito_escribe_el_discriminante(self):
        from services.documents.signing.signing import preparar_documento_para_firma

        src = inspect.getsource(preparar_documento_para_firma)
        i = src.index("UPDATE document_draft SET status = 'sent_to_sign'")
        cas = src[i:i + 200]
        assert "sent_to_sign_at" not in cas
        assert "sent_by" not in cas


class TestElAltaTadNoArmaElPdf:

    def test_el_request_solo_llama_a_la_mitad_barata(self):
        from services.documents.signing import citizen_signing

        src = solo_codigo(inspect.getsource(citizen_signing.create_and_sign_citizen_document))
        assert "preparar_documento_para_firma(" in src
        assert "start_document_signing_process(" not in src

    def test_el_pdf_del_portal_se_sube_crudo_y_no_se_procesa_en_el_request(self):
        from services.documents.signing import citizen_signing

        src = solo_codigo(inspect.getsource(citizen_signing.create_and_sign_citizen_document))
        assert "call_pdfcomposer_import(" not in src
        assert "_raw.pdf" in src
        assert "import_pendiente" in src

    def test_el_cas_se_queda_en_el_request(self):
        from services.documents.signing.signing import preparar_documento_para_firma

        src = inspect.getsource(preparar_documento_para_firma)
        assert "UPDATE document_draft SET status = 'sent_to_sign'" in src

    def test_el_payload_viaja_con_lo_que_el_worker_necesita(self):
        from services.documents.signing import citizen_signing

        src = inspect.getsource(citizen_signing.create_and_sign_citizen_document)
        assert '"pdf_pendiente": True' in src
        assert '"original_status"' in src

    def test_el_encolado_persiste_el_payload(self):
        from services.documents.signing import citizen_signing

        src = inspect.getsource(citizen_signing.enqueue_citizen_signing)
        assert "$6::jsonb" in src, "el payload tiene que llegar a la fila, no perderse"
        assert "'{}'::jsonb" not in src

    def test_si_falla_el_encolado_se_revierte_el_cas(self):
        from services.documents.signing import citizen_signing

        src = inspect.getsource(citizen_signing.create_and_sign_citizen_document)
        i = src.index("enqueue_citizen_signing(")
        cola = src[i:]
        assert "UPDATE document_draft SET status = $1" in cola


class TestElWorkerArmaElPdf:

    def _worker(self):
        from workers.escri import EscriWorker
        w = EscriWorker()
        w._mark_session_signed = AsyncMock()
        w._mark_session_failed = AsyncMock()
        return w

    def _job(self, payload):
        return {
            "session_id": "5e551000-0000-0000-0000-00000000000a",
            "schema_name": "100_test",
            "document_id": DOC_ID,
            "citizen_id": "c1000000-0000-0000-0000-00000000000a",
            "user_id": None,
            "job_type": "sign_citizen",
            "payload": payload,
        }

    @pytest.mark.asyncio
    async def test_arma_el_pdf_antes_de_firmar(self):
        worker = self._worker()
        armar = AsyncMock()
        worker._armar_pdf_del_ciudadano = armar

        with (
            patch("services.documents.signing.citizen_signing.sign_and_number_citizen_document",
                  AsyncMock(return_value={"official_number": "IF-2026-99"})),
            patch("services.webhooks.tad_notify.get_tad_webhook_config",
                  AsyncMock(return_value=None)),
        ):
            await worker._process_citizen_job(
                self._job({"pdf_pendiente": True, "original_status": "draft"})
            )

        armar.assert_awaited_once()
        worker._mark_session_signed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_si_el_pdf_falla_no_intenta_firmar_y_avisa_al_portal(self):
        worker = self._worker()
        worker._armar_pdf_del_ciudadano = AsyncMock(side_effect=RuntimeError("pdfcomposer caido"))
        firmar = AsyncMock()
        avisar = AsyncMock()
        worker._avisar_tad = avisar

        with patch("services.documents.signing.citizen_signing.sign_and_number_citizen_document",
                   firmar):
            await worker._process_citizen_job(
                self._job({"pdf_pendiente": True, "original_status": "draft"})
            )

        firmar.assert_not_awaited()
        worker._mark_session_failed.assert_awaited_once()
        avisar.assert_awaited_once()
        assert avisar.call_args.kwargs["exito"] is False

    @pytest.mark.asyncio
    async def test_un_job_viejo_sin_payload_sigue_funcionando(self):
        worker = self._worker()
        armar = AsyncMock()
        worker._armar_pdf_del_ciudadano = armar

        with (
            patch("services.documents.signing.citizen_signing.sign_and_number_citizen_document",
                  AsyncMock(return_value={"official_number": "IF-2026-99"})),
            patch("services.webhooks.tad_notify.get_tad_webhook_config",
                  AsyncMock(return_value=None)),
        ):
            await worker._process_citizen_job(self._job({}))

        armar.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_el_import_pendiente_pasa_por_pdfcomposer_y_borra_el_crudo(self):
        worker = self._worker()

        r2 = AsyncMock()
        r2.exists_tosign = lambda f: True
        r2.get_tosign_url = lambda f: "https://r2.example/crudo.pdf"
        subidos = []
        r2.upload_tosign = lambda b, f: subidos.append(f)
        borrados = []
        r2.delete_tosign = lambda f: borrados.append(f)

        class _Resp:
            content = b"%PDF-1.4 procesado"
            def raise_for_status(self): return None

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): return _Resp()

        importado = AsyncMock(return_value=b"%PDF-1.4 con hoja final")

        with (
            patch("services.storage.cloudflare.get_tenant_r2_client", AsyncMock(return_value=r2)),
            patch("services.shared.pdfcomposer_api.call_pdfcomposer_import", importado),
            patch("workers.escri.httpx.AsyncClient", lambda **kw: _Client()),
        ):
            await worker._procesar_pdf_importado(
                schema="100_test", document_id=DOC_ID,
                datos={"raw_filename": "abc_raw.pdf", "url_logo": "https://l",
                       "name_acrony_type": "IF", "document_type": "Informe",
                       "reference": "ref"},
                original_status="draft",
            )

        importado.assert_awaited_once()
        assert subidos == [DOC_ID.replace("-", "") + ".pdf"]
        assert borrados == ["abc_raw.pdf"]

    @pytest.mark.asyncio
    async def test_si_el_import_falla_revierte_el_documento(self):
        worker = self._worker()

        r2 = AsyncMock()
        r2.exists_tosign = lambda f: True
        r2.get_tosign_url = lambda f: "https://r2.example/crudo.pdf"

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): raise RuntimeError("R2 caido")

        ejecutados = []

        async def _execute(sql, *args, **kwargs):
            ejecutados.append((sql, args))

        with (
            patch("services.storage.cloudflare.get_tenant_r2_client", AsyncMock(return_value=r2)),
            patch("workers.escri.httpx.AsyncClient", lambda **kw: _Client()),
            patch("workers.escri.execute", _execute),
        ):
            with pytest.raises(Exception):
                await worker._procesar_pdf_importado(
                    schema="100_test", document_id=DOC_ID,
                    datos={"raw_filename": "abc_raw.pdf"},
                    original_status="draft",
                )

        assert ejecutados, "no se revirtió el estado del documento"
        sql, args = ejecutados[0]
        assert "UPDATE document_draft SET status" in sql
        assert args[0] == "draft"

    @pytest.mark.asyncio
    async def test_el_reintento_no_rompe_si_el_import_ya_se_hizo(self):
        worker = self._worker()

        r2 = AsyncMock()
        r2.exists_tosign = lambda f: not f.endswith("_raw.pdf")
        importado = AsyncMock()

        with (
            patch("services.storage.cloudflare.get_tenant_r2_client", AsyncMock(return_value=r2)),
            patch("services.shared.pdfcomposer_api.call_pdfcomposer_import", importado),
        ):
            await worker._procesar_pdf_importado(
                schema="100_test", document_id=DOC_ID,
                datos={"raw_filename": "abc_raw.pdf"},
                original_status="draft",
            )

        importado.assert_not_awaited()


class TestLaSegundaMitadSeBastaSola:

    def test_puede_correr_sin_la_fila_en_la_mano(self):
        from services.documents.signing.signing import generar_pdf_y_finalizar

        firma = inspect.signature(generar_pdf_y_finalizar)
        assert firma.parameters["document"].default is None
        assert firma.parameters["original_status"].default is inspect.Parameter.empty, (
            "original_status no puede tener default: adivinarlo es dejar el "
            "documento en el estado equivocado si el PDF falla"
        )

    def test_no_revalida_el_estado_editable(self):
        from services.documents.signing.signing import generar_pdf_y_finalizar

        src = inspect.getsource(generar_pdf_y_finalizar)
        assert "EDITABLE_DOCUMENT_STATES" not in src
