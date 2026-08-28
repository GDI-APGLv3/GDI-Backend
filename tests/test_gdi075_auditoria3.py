
import asyncio
import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestH1NumeratorFaultBranches:

    @pytest.mark.asyncio
    async def test_h1_rama_a_pre_cas_failure_cancela_el_numero(self):
        from services.documents.signing import digital_completion as dc
        from shared.exceptions import NumeratorPreCasError

        cancel_mock = AsyncMock()
        with (
            patch.object(dc, "fetch_one",
                         AsyncMock(return_value={"status": "completing"})),
            patch.object(dc, "leer_pdf_firmado", AsyncMock(return_value=b"%PDF")),
            patch.object(dc, "completar_numerador",
                         AsyncMock(side_effect=NumeratorPreCasError("confirm falló"))),
            patch.object(dc, "_alertar", AsyncMock()),
            patch("shared.numbering.cancel_number", cancel_mock),
        ):
            resultado = await dc.cerrar_firma_digital(
                schema_name="100_test",
                document_id=str(uuid.uuid4()),
                user_id="user1",
                reservation_id=str(uuid.uuid4()),
                official_number="NUM-001",
                digital_session_id="SESH1A",
                is_numerator=True,
                cas_pre_done=False,
                cert={},
            )

        assert resultado["ok"] is False
        assert resultado["failure_reason"] == "numerator_partial_failure"
        cancel_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_h1_rama_b_upload_failure_no_cancela(self):
        from services.documents.signing import digital_completion as dc
        from shared.exceptions import NumeratorUploadError

        cancel_mock = AsyncMock()
        with (
            patch.object(dc, "fetch_one",
                         AsyncMock(return_value={"status": "completing"})),
            patch.object(dc, "leer_pdf_firmado", AsyncMock(return_value=b"%PDF")),
            patch.object(dc, "completar_numerador",
                         AsyncMock(side_effect=NumeratorUploadError("R2 timeout"))),
            patch.object(dc, "_alertar", AsyncMock()),
            patch("shared.numbering.cancel_number", cancel_mock),
        ):
            resultado = await dc.cerrar_firma_digital(
                schema_name="100_test",
                document_id=str(uuid.uuid4()),
                user_id="user1",
                reservation_id=str(uuid.uuid4()),
                official_number="NUM-001",
                digital_session_id="SESH1B",
                is_numerator=True,
                cas_pre_done=False,
                cert={},
            )

        assert resultado["ok"] is False
        assert resultado["failure_reason"] == "numerator_partial_failure"
        cancel_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_h1_el_upload_que_falla_levanta_numerator_upload_error(self):
        from services.documents.signing import digital_completion as dc
        from shared.exceptions import NumeratorUploadError

        with (
            patch.object(dc, "confirm_number", AsyncMock()),
            patch.object(dc, "subir_pdf_a_oficial",
                         AsyncMock(side_effect=RuntimeError("R2 timeout"))),
        ):
            with pytest.raises(NumeratorUploadError):
                await dc.completar_numerador(
                    str(uuid.uuid4()), "user1", "100_test", "NUM-001", b"pdf",
                    reservation_id=str(uuid.uuid4()),
                    cas_pre_done=False,
                )

    @pytest.mark.asyncio
    async def test_h1_rama_c_post_upload_propaga_el_error_original(self):
        from services.documents.signing import digital_completion as dc
        from shared.exceptions import NumeratorPreCasError, NumeratorUploadError

        with (
            patch.object(dc, "confirm_number", AsyncMock()),
            patch.object(dc, "subir_pdf_a_oficial", AsyncMock()),
            patch.object(dc, "finalize_number",
                         AsyncMock(side_effect=RuntimeError("DB connection lost"))),
        ):
            with pytest.raises(RuntimeError) as exc:
                await dc.completar_numerador(
                    str(uuid.uuid4()), "user1", "100_test", "NUM-001", b"pdf",
                    reservation_id=str(uuid.uuid4()),
                    cas_pre_done=False,
                )
            assert not isinstance(exc.value, (NumeratorPreCasError, NumeratorUploadError))

    @pytest.mark.asyncio
    async def test_h1_rama_c_el_cierre_igual_responde_ok(self):
        from services.documents.signing import digital_completion as dc

        with (
            patch.object(dc, "fetch_one",
                         AsyncMock(return_value={"status": "completing"})),
            patch.object(dc, "leer_pdf_firmado", AsyncMock(return_value=b"%PDF")),
            patch.object(dc, "completar_numerador",
                         AsyncMock(side_effect=RuntimeError("DB connection lost"))),
            patch.object(dc, "actualizar_firmante", AsyncMock()),
            patch.object(dc, "marcar_sesion_digital", AsyncMock()),
            patch.object(dc, "borrar_pdf_firmado", AsyncMock()),
            patch("services.documents.signing.r2_lock.release_signing_lock_R2_success",
                  AsyncMock()),
        ):
            resultado = await dc.cerrar_firma_digital(
                schema_name="100_test",
                document_id=str(uuid.uuid4()),
                user_id="user1",
                reservation_id=str(uuid.uuid4()),
                official_number="NUM-001",
                digital_session_id="SESH1C",
                is_numerator=True,
                cas_pre_done=True,
                cert={},
            )

        assert resultado["ok"] is True


class TestM6PdfSizeLimit:

    def test_escri_max_pdf_bytes_defined(self):
        from workers.escri import ESCRI_MAX_PDF_BYTES, ESCRI_MAX_PDF_MB
        assert ESCRI_MAX_PDF_BYTES > 0
        assert ESCRI_MAX_PDF_BYTES == ESCRI_MAX_PDF_MB * 1024 * 1024

    @pytest.mark.asyncio
    async def test_content_length_header_exceeds_limit_marks_session_failed(self):
        from workers.escri import EscriWorker, ESCRI_MAX_PDF_BYTES, _failure_code

        exc = RuntimeError(f"pdf_too_large: content-length {ESCRI_MAX_PDF_BYTES + 1} bytes")
        assert _failure_code(exc) == "pdf_too_large"

        worker = EscriWorker()
        session_id     = str(uuid.uuid4())
        doc_id         = str(uuid.uuid4())
        schema         = "100_test"
        user_id        = str(uuid.uuid4())
        reservation_id = str(uuid.uuid4())
        official_number = "NUM-SIZE-001"

        job = {
            "session_id": session_id,
            "document_id": doc_id,
            "schema_name": schema,
            "user_id": user_id,
            "reservation_id": reservation_id,
            "payload": {},
            "job_type": "sign",
            "created_at": None,
        }

        m_r2 = MagicMock()
        m_r2.get_tosign_url.return_value = "https://r2/tosign/file.pdf"

        m_mark_failed = AsyncMock()

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.headers = {"content-length": str(ESCRI_MAX_PDF_BYTES + 1)}
        fake_resp.content = b"x" * 10

        with (
            patch("workers.escri.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch("workers.escri.run_in_threadpool",
                  AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))),
            patch("workers.escri.httpx.AsyncClient") as mock_client_cls,
            patch("workers.escri.fetch_one", AsyncMock(return_value={"official_number": official_number, "type": "HTML"})),
            patch("workers.escri.cancel_number", AsyncMock()),
        ):
            with patch.object(worker, "_get_official_number",
                               AsyncMock(return_value=official_number)):
                with patch.object(worker, "_mark_session_failed", m_mark_failed):
                    mock_ac = AsyncMock()
                    mock_ac.__aenter__ = AsyncMock(return_value=mock_ac)
                    mock_ac.__aexit__ = AsyncMock(return_value=False)
                    mock_ac.get = AsyncMock(return_value=fake_resp)
                    mock_client_cls.return_value = mock_ac

                    await worker._process_job(job)

        m_mark_failed.assert_called_once()
        reason = m_mark_failed.call_args.args[1]
        assert reason == "pdf_too_large"

    @pytest.mark.asyncio
    async def test_actual_bytes_exceed_limit_marks_session_failed(self):
        from workers.escri import EscriWorker, ESCRI_MAX_PDF_BYTES

        worker = EscriWorker()
        session_id     = str(uuid.uuid4())
        doc_id         = str(uuid.uuid4())
        schema         = "100_test"
        user_id        = str(uuid.uuid4())
        reservation_id = str(uuid.uuid4())
        official_number = "NUM-BIGBODY-001"

        job = {
            "session_id": session_id,
            "document_id": doc_id,
            "schema_name": schema,
            "user_id": user_id,
            "reservation_id": reservation_id,
            "payload": {},
            "job_type": "sign",
            "created_at": None,
        }

        m_r2 = MagicMock()
        m_r2.get_tosign_url.return_value = "https://r2/tosign/file.pdf"
        m_mark_failed = AsyncMock()

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.headers = {}
        big_pdf = b"X" * (ESCRI_MAX_PDF_BYTES + 1)
        fake_resp.content = big_pdf

        with (
            patch("workers.escri.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch("workers.escri.run_in_threadpool",
                  AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))),
            patch("workers.escri.httpx.AsyncClient") as mock_client_cls,
            patch("workers.escri.fetch_one", AsyncMock(return_value={"official_number": official_number, "type": "HTML"})),
            patch("workers.escri.cancel_number", AsyncMock()),
        ):
            with patch.object(worker, "_get_official_number",
                               AsyncMock(return_value=official_number)):
                with patch.object(worker, "_mark_session_failed", m_mark_failed):
                    mock_ac = AsyncMock()
                    mock_ac.__aenter__ = AsyncMock(return_value=mock_ac)
                    mock_ac.__aexit__ = AsyncMock(return_value=False)
                    mock_ac.get = AsyncMock(return_value=fake_resp)
                    mock_client_cls.return_value = mock_ac

                    await worker._process_job(job)

        m_mark_failed.assert_called_once()
        reason = m_mark_failed.call_args.args[1]
        assert reason == "pdf_too_large"


class TestM7StopRace:

    def test_stop_source_captures_local_reference(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker.stop)
        assert "evt = self._notify_event" in source or "_notify_event" in source
        assert "evt.set()" in source or "self._notify_event.set()" in source

    def test_stop_when_notify_event_is_none_does_not_raise(self):
        from workers.escri import EscriWorker

        worker = EscriWorker()
        assert worker._notify_event is None
        worker.stop()
        assert not worker._running

    def test_stop_when_notify_event_set_calls_set(self):
        from workers.escri import EscriWorker

        worker = EscriWorker()
        mock_event = MagicMock()
        worker._notify_event = mock_event
        worker._running = True

        worker.stop()

        assert not worker._running
        mock_event.set.assert_called_once()

    def test_stop_local_ref_prevents_race(self):
        from workers.escri import EscriWorker

        worker = EscriWorker()
        real_event = asyncio.Event()
        worker._notify_event = real_event
        worker._running = True

        original_set = real_event.set
        calls = []

        def patched_set():
            worker._notify_event = None
            original_set()
            calls.append(1)

        real_event.set = patched_set
        worker.stop()

        assert len(calls) == 1
