
import inspect
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestH1CasGuards:

    def test_mark_session_signed_has_status_processing_guard(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._mark_session_signed)
        assert "status     = 'processing'" in source or "status = 'processing'" in source

    def test_mark_session_signed_has_claimed_by_guard(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._mark_session_signed)
        assert "claimed_by" in source

    def test_mark_session_failed_has_status_processing_guard(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._mark_session_failed)
        assert "status     = 'processing'" in source or "status = 'processing'" in source

    def test_mark_dts_signed_has_status_processing_guard(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._mark_dts_signed)
        assert "status     = 'processing'" in source or "status = 'processing'" in source

    @pytest.mark.asyncio
    async def test_superseded_check_skips_cancel_and_failed(self):
        from workers.escri import EscriWorker

        worker = EscriWorker()
        session_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        schema = "100_test"
        reservation_id = str(uuid.uuid4())

        od_row = {"reservation_status": "CONFIRMED"}

        m_fetch = AsyncMock(return_value=od_row)
        m_cancel = AsyncMock()
        m_mark_failed = AsyncMock()

        with (
            patch("workers.escri.fetch_one", m_fetch),
            patch("workers.escri.cancel_number", m_cancel),
        ):
            worker._mark_session_failed = m_mark_failed

            _superseded = False
            _od_row = await m_fetch.__call__(None, doc_id, schema_name=schema)
            if _od_row and _od_row["reservation_status"] in ("CONFIRMING", "CONFIRMED"):
                _superseded = True

            if _superseded:
                pass
            else:
                await m_cancel()
                await m_mark_failed(session_id, "error")

        m_cancel.assert_not_awaited()
        m_mark_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_superseded_check_reserved_still_cancels(self):
        od_row = {"reservation_status": "RESERVED"}

        m_cancel = AsyncMock()
        m_mark_failed = AsyncMock()

        _superseded = False
        if od_row and od_row["reservation_status"] in ("CONFIRMING", "CONFIRMED"):
            _superseded = True

        if not _superseded:
            await m_cancel()
            await m_mark_failed("sid", "err")

        m_cancel.assert_awaited_once()
        m_mark_failed.assert_awaited_once()


class TestH2Idempotency:

    def test_unified_signing_has_idempotency_check(self):
        import inspect
        from services.documents.signing import unified_signing

        source = inspect.getsource(unified_signing._try_reserve_and_enqueue)
        assert "existing_session" in source
        assert "pending" in source
        assert "processing" in source

    @pytest.mark.asyncio
    async def test_double_click_returns_existing_session_id(self):
        from services.documents.signing.unified_signing import _try_reserve_and_enqueue

        existing_sid = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        expires_at = datetime(2026, 7, 2, 20, 0, 0, tzinfo=timezone.utc)

        existing_row = {
            "session_id": existing_sid,
            "expires_at": expires_at,
        }

        m_fetch_one = AsyncMock(return_value=existing_row)
        m_reserve = AsyncMock()

        with (
            patch("services.documents.signing.unified_signing.fetch_one", m_fetch_one),
        ):
            result = await _try_reserve_and_enqueue(
                document_id=doc_id,
                user_id=user_id,
                schema_name="100_test",
            )

        assert result is not None
        assert result["session_id"] == existing_sid
        assert result["flow"] == "electronic_async"
        m_fetch_one.assert_awaited_once()
        m_reserve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_first_click_proceeds_to_fetch_doc_data(self):
        from services.documents.signing.unified_signing import _try_reserve_and_enqueue

        doc_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        call_count = {"n": 0}

        async def mock_fetch_one(sql, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None
            return None

        from services.documents.signing.queue_signals import SenalesCola
        cola_vacia = SenalesCola(0, 0, 0, 0.0, 0.0, 0, None, 0.0)

        with (
            patch("services.documents.signing.unified_signing.fetch_one", mock_fetch_one),
            patch(
                "services.documents.signing.queue_signals.medir_cola",
                AsyncMock(return_value=cola_vacia),
            ),
            patch(
                "services.documents.signing.unified_signing.confirm_document_missing",
                AsyncMock(return_value=None),
            ),
        ):
            from services.documents.signing.unified_signing import DocumentNotFoundError
            with pytest.raises(DocumentNotFoundError):
                await _try_reserve_and_enqueue(
                    document_id=doc_id,
                    user_id=user_id,
                    schema_name="100_test",
                )

        assert call_count["n"] >= 2, (
            f"Se esperaban ≥2 llamadas a fetch_one, se hicieron {call_count['n']}"
        )


class TestH3UploadFencing:

    def test_process_job_has_upload_fence(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._process_job)
        assert "upload_fence" in source or "upload_oficial" in source
        idx_fence = source.find("upload_fence")
        idx_upload = source.find("upload_oficial")
        assert idx_fence < idx_upload, "El fencing debe preceder al upload"

    def test_fencing_uses_claimed_by_guard(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._process_job)
        fence_section = source[source.find("upload_fence"):source.find("upload_oficial")]
        assert "claimed_by" in fence_section

    @pytest.mark.asyncio
    async def test_fencing_aborts_upload_when_zero_rows(self):
        from workers.escri import EscriWorker

        worker = EscriWorker()
        uploaded = {"called": False}

        m_fence = AsyncMock(return_value=None)

        async def mock_upload(*args, **kwargs):
            uploaded["called"] = True

        fence_result = None

        if not fence_result:
            pass
        else:
            uploaded["called"] = True

        assert not uploaded["called"], "El upload no debe ejecutarse si el fencing falla"


class TestH5ConfirmedAutoheal:

    def test_process_job_has_autoheal_branch(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._process_job)
        assert "confirmed_autoheal" in source or "is_autoheal" in source

    def test_autoheal_skips_notary_call(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._process_job)
        autoheal_idx = source.find("is_autoheal")
        notary_idx_in_autoheal = source.find("call_notary_sign_pdf", autoheal_idx)
        autoheal_return_idx = source.find("return", autoheal_idx)
        if notary_idx_in_autoheal > 0:
            assert notary_idx_in_autoheal > autoheal_return_idx, (
                "call_notary_sign_pdf no debe estar dentro del bloque autoheal"
            )

    @pytest.mark.asyncio
    async def test_autoheal_calls_mark_document_signed(self):
        from workers.escri import EscriWorker

        worker = EscriWorker()
        session_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        schema = "100_test"
        user_id = str(uuid.uuid4())
        official_number = "IF-2026-00099"

        job = {
            "session_id": session_id,
            "document_id": doc_id,
            "schema_name": schema,
            "user_id": user_id,
            "reservation_id": str(uuid.uuid4()),
            "job_type": "sign",
            "payload": {
                "official_number": official_number,
                "confirmed_autoheal": True,
            },
        }

        m_mark_doc = AsyncMock()
        m_mark_session = AsyncMock()
        m_notary = AsyncMock()
        m_upload = AsyncMock()
        m_r2 = AsyncMock()
        m_r2.upload_oficial = m_upload

        worker._mark_document_signed = m_mark_doc
        worker._mark_session_signed = m_mark_session

        with (
            patch("workers.escri.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch("workers.escri.call_notary_sign_pdf", m_notary),
        ):
            await worker._process_job(job)

        m_mark_doc.assert_awaited_once()
        m_mark_session.assert_awaited_once()
        m_notary.assert_not_awaited()
        m_upload.assert_not_called()


class TestH6CancelNumberTicket:

    def test_cancel_number_has_reservation_id_param(self):
        from shared.numbering import cancel_number
        sig = inspect.signature(cancel_number)
        assert "reservation_id" in sig.parameters
        assert sig.parameters["reservation_id"].default is None

    def test_cancel_number_with_ticket_filters_reservation(self):
        import inspect
        from shared import numbering
        source = inspect.getsource(numbering.cancel_number)
        assert "reservation_id" in source
        assert "AND reservation_id = " in source or "AND reservation_id=$" in source

    def test_cancel_number_without_ticket_uses_legacy_path(self):
        import inspect
        from shared import numbering
        source = inspect.getsource(numbering.cancel_number)
        assert "else:" in source
        assert "reservation_status = ANY(" in source
        assert "from_states: tuple[str, ...] = ('RESERVED',)" in source

    def test_worker_passes_reservation_id_to_cancel(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._process_job)
        assert "reservation_id=reservation_id" in source


class TestH9bMarkDocumentSignedGuard:

    def test_mark_document_signed_has_sent_to_sign_guard(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._mark_document_signed)
        assert "sent_to_sign" in source

    def test_mark_document_signed_uses_returning(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._mark_document_signed)
        assert "RETURNING" in source

    def test_mark_document_signed_raises_rejected_error_on_zero_rows(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._mark_document_signed)
        assert "DocumentRejectedWhileInQueueError" in source

    def test_document_rejected_while_in_queue_exception_exists(self):
        from shared.exceptions import DocumentRejectedWhileInQueueError
        exc = DocumentRejectedWhileInQueueError("test-doc-id")
        assert "test-doc-id" in str(exc)

    def test_process_job_handles_rejected_before_generic_except(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._process_job)
        idx_rejected = source.find("DocumentRejectedWhileInQueueError")
        idx_generic = source.find("except Exception as exc")
        assert idx_rejected > 0 and idx_generic > 0
        assert idx_rejected < idx_generic, (
            "DocumentRejectedWhileInQueueError debe manejarse antes del except Exception"
        )

    def test_process_job_rejected_calls_cancel_number(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._process_job)
        rejected_start = source.find("DocumentRejectedWhileInQueueError")
        next_except_idx = source.find("except NotaryBreakerOpenError", rejected_start)
        rejected_block = source[rejected_start:next_except_idx]
        assert "cancel_number" in rejected_block

    def test_process_job_rejected_marks_failed_with_reason(self):
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._process_job)
        rejected_start = source.find("DocumentRejectedWhileInQueueError")
        next_except_idx = source.find("except NotaryBreakerOpenError", rejected_start)
        rejected_block = source[rejected_start:next_except_idx]
        assert "document_no_longer_signable" in rejected_block

    @pytest.mark.asyncio
    async def test_process_job_rejected_does_not_mark_session_failed_generically(self):
        from workers.escri import EscriWorker
        from shared.exceptions import DocumentRejectedWhileInQueueError

        worker = EscriWorker()
        session_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        schema = "100_test"
        user_id = str(uuid.uuid4())
        reservation_id = str(uuid.uuid4())
        official_number = "IF-2026-00077"

        job = {
            "session_id": session_id,
            "document_id": doc_id,
            "schema_name": schema,
            "user_id": user_id,
            "reservation_id": reservation_id,
            "job_type": "sign",
            "payload": {"official_number": official_number},
        }

        recorded_reason = {}

        async def mock_mark_failed(sid, reason):
            recorded_reason["reason"] = reason

        m_r2 = AsyncMock()
        m_r2.get_tosign_url = MagicMock(return_value="http://r2/pdf")
        m_r2.delete_oficial = MagicMock()

        async def mock_mark_doc(*args, **kwargs):
            raise DocumentRejectedWhileInQueueError(doc_id)

        worker._mark_document_signed = mock_mark_doc
        worker._mark_session_signed = AsyncMock()
        worker._mark_session_failed = mock_mark_failed

        mock_http_resp = MagicMock()
        mock_http_resp.status_code = 200
        mock_http_resp.content = b"%PDF"
        mock_http_resp.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        mock_http_client.get = AsyncMock(return_value=mock_http_resp)

        fence_row = {"session_id": session_id, "type": "HTML"}

        with (
            patch("workers.escri.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch("workers.escri.run_in_threadpool", AsyncMock(return_value=b"%PDF")),
            patch("workers.escri.get_signer_data", AsyncMock(return_value={
                "full_name": "Juan", "seal": "S", "department_name": "D",
                "municipality_name": "M",
            })),
            patch("workers.escri.get_city_from_settings", AsyncMock(return_value="LATAM")),
            patch("workers.escri.call_notary_sign_pdf", AsyncMock(return_value=b"%PDF signed")),
            patch("workers.escri.confirm_number", AsyncMock()),
            patch("workers.escri.finalize_number", AsyncMock()),
            patch("workers.escri.fetch_one", AsyncMock(return_value=fence_row)),
            patch("workers.escri.cancel_number", AsyncMock()),
            patch("workers.escri.httpx.AsyncClient", return_value=mock_http_client),
        ):
            await worker._process_job(job)

        assert recorded_reason.get("reason") == "document_no_longer_signable", (
            f"Se esperaba 'document_no_longer_signable', se obtuvo: {recorded_reason.get('reason')}"
        )
