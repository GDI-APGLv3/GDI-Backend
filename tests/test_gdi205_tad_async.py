import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestElEndpointNoEsperaLaFirma:

    def test_el_alta_ya_no_firma_dentro_del_request(self):
        import inspect
        from services.documents.signing import citizen_signing

        from tests.test_gdi382_gdi385_pdf_fuera_del_request import solo_codigo

        src = solo_codigo(inspect.getsource(citizen_signing.create_and_sign_citizen_document))
        assert "enqueue_citizen_signing(" in src
        assert "sign_and_number_citizen_document(" not in src, (
            "si el endpoint sigue firmando, el portal sigue esperando"
        )
        assert "start_document_signing_process(" not in src, (
            "start_document_signing_process genera el PDF: si vuelve al request, "
            "el 202 deja de ser instantáneo (GDI-385)"
        )
        assert "call_pdfcomposer_import(" not in src, (
            "el /import/ del PDF que sube el portal también es PDFComposer: "
            "va al worker, no al request (GDI-385)"
        )

    def test_la_respuesta_no_promete_un_numero_que_todavia_no_existe(self):
        import inspect
        from services.documents.signing import citizen_signing

        src = inspect.getsource(citizen_signing.enqueue_citizen_signing)
        assert '"status": "queued"' in src
        assert "official_number" not in src

    def test_el_gateway_devuelve_202_no_200(self):
        import inspect
        from api_gateway import rest_api_tad

        src = inspect.getsource(rest_api_tad)
        i = src.index("create_and_sign_citizen_document(")
        assert "status_code=202" in src[i:i + 1200]


class TestIdempotencia:

    def test_el_encolado_usa_on_conflict(self):
        import inspect
        from services.documents.signing import citizen_signing

        src = inspect.getsource(citizen_signing.enqueue_citizen_signing)
        assert "ON CONFLICT DO NOTHING" in src

    def test_si_pierde_la_carrera_devuelve_la_sesion_ganadora(self):
        import inspect
        from services.documents.signing import citizen_signing

        src = inspect.getsource(citizen_signing.enqueue_citizen_signing)
        i = src.index("if row is None:")
        fragmento = src[i:i + 1500]
        assert "SELECT session_id" in fragmento
        assert "status IN ('pending', 'processing')" in fragmento

    def test_el_indice_unico_existe_en_la_migracion(self):
        from pathlib import Path

        ruta = (Path(__file__).resolve().parents[2] / "GDI-BD" / "sql" /
                "migrations" / "110_gdi205_idx_citizen_unique_concurrently.sql")
        if not ruta.exists():
            pytest.skip("GDI-BD no está en el worktree")
        sql = ruta.read_text(encoding="utf-8")
        assert "idx_signing_sessions_citizen_active_unique" in sql
        assert "job_type = 'sign_citizen'" in sql
        assert "CONCURRENTLY" in sql, (
            "sin CONCURRENTLY el índice bloquea las escrituras de signing_sessions, "
            "que es la tabla por la que pasa cada firma"
        )


def _job(citizen_id="c1000000-0000-0000-0000-00000000000a"):
    return {
        "session_id": "5e551000-0000-0000-0000-00000000000a",
        "schema_name": "100_test",
        "document_id": "d0c00000-0000-0000-0000-00000000000a",
        "citizen_id": citizen_id,
        "user_id": None,
        "job_type": "sign_citizen",
        "payload": {},
    }


class TestElWorkerFirmaYAvisa:

    def _worker(self):
        from workers.escri import EscriWorker
        w = EscriWorker()
        w._mark_session_signed = AsyncMock()
        w._mark_session_failed = AsyncMock()
        return w

    @pytest.mark.asyncio
    async def test_ca2_al_terminar_bien_encola_el_webhook_con_el_numero(self):
        worker = self._worker()
        enqueue = AsyncMock()

        with (
            patch("services.documents.signing.citizen_signing.sign_and_number_citizen_document",
                  AsyncMock(return_value={"official_number": "IF-2026-99"})),
            patch("services.webhooks.tad_notify.get_tad_webhook_config",
                  AsyncMock(return_value={"api_key_id": "a1000000-0000-0000-0000-00000000000a"})),
            patch("services.webhooks.tad_notify.enqueue_tad_webhook", enqueue),
        ):
            await worker._process_citizen_job(_job())

        worker._mark_session_signed.assert_awaited_once()
        enqueue.assert_awaited_once()
        kwargs = enqueue.call_args.kwargs
        assert kwargs["event_type"] == "documents.signed"
        assert kwargs["payload"]["official_number"] == "IF-2026-99"

    @pytest.mark.asyncio
    async def test_ca4_si_falla_definitivamente_el_webhook_avisa_el_fallo(self):
        worker = self._worker()
        enqueue = AsyncMock()

        with (
            patch("services.documents.signing.citizen_signing.sign_and_number_citizen_document",
                  AsyncMock(side_effect=RuntimeError("notary caído"))),
            patch("services.webhooks.tad_notify.get_tad_webhook_config",
                  AsyncMock(return_value={"api_key_id": "a1000000-0000-0000-0000-00000000000a"})),
            patch("services.webhooks.tad_notify.enqueue_tad_webhook", enqueue),
        ):
            await worker._process_citizen_job(_job())

        worker._mark_session_failed.assert_awaited_once()
        enqueue.assert_awaited_once()
        assert enqueue.call_args.kwargs["event_type"] == "documents.signature_failed"

    @pytest.mark.asyncio
    async def test_si_el_webhook_falla_no_rompe_el_job(self):
        worker = self._worker()

        with (
            patch("services.documents.signing.citizen_signing.sign_and_number_citizen_document",
                  AsyncMock(return_value={"official_number": "IF-2026-99"})),
            patch("services.webhooks.tad_notify.get_tad_webhook_config",
                  AsyncMock(side_effect=RuntimeError("BD caída"))),
        ):
            await worker._process_citizen_job(_job())

        worker._mark_session_signed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sin_webhook_configurado_la_firma_igual_se_completa(self):
        worker = self._worker()

        with (
            patch("services.documents.signing.citizen_signing.sign_and_number_citizen_document",
                  AsyncMock(return_value={"official_number": "IF-2026-99"})),
            patch("services.webhooks.tad_notify.get_tad_webhook_config",
                  AsyncMock(return_value=None)),
        ):
            await worker._process_citizen_job(_job())

        worker._mark_session_signed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_un_job_sin_ciudadano_falla_sin_reintentar(self):
        worker = self._worker()

        await worker._process_citizen_job(_job(citizen_id=None))

        worker._mark_session_failed.assert_awaited_once()
        assert "sin_citizen_id" in worker._mark_session_failed.call_args.args[1]


class TestElCarrilEntraALaCola:

    def test_el_claim_incluye_sign_citizen(self):
        import inspect
        from workers.escri import EscriWorker

        src = inspect.getsource(EscriWorker._claim_one)
        assert "'sign_citizen'" in src

    def test_el_claim_trae_el_citizen_id(self):
        import inspect
        from workers.escri import EscriWorker

        assert "citizen_id::text" in inspect.getsource(EscriWorker._claim_one)

    def test_el_dispatch_rutea_el_job_type(self):
        import inspect
        from workers.escri import EscriWorker

        src = inspect.getsource(EscriWorker._process_batch)
        assert "sign_citizen" in src
        assert "_process_citizen_job" in src


class TestCA5SpecialSigueRechazado:

    def test_el_ciudadano_no_puede_numerar_special(self):
        import inspect
        from shared import numbering

        src = inspect.getsource(numbering.reserve_citizen_number)
        assert "special" in src.lower()
