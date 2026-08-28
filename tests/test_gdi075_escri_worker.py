
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_session(
    is_confirming: bool = False,
    official_number: str = "IF-2026-00001",
    reservation_id: str | None = None,
    user_id: str | None = None,
    schema: str = "100_test",
) -> dict:
    sid = str(uuid.uuid4())
    rid = reservation_id or str(uuid.uuid4())
    uid = user_id or str(uuid.uuid4())
    payload: dict = {"official_number": official_number}
    if is_confirming:
        payload["is_confirming"] = True
    return {
        "session_id": sid,
        "schema_name": schema,
        "document_id": str(uuid.uuid4()),
        "reservation_id": rid,
        "user_id": uid,
        "payload": payload,
        "job_type": "sign",
    }


def _make_signer_data() -> dict:
    return {
        "full_name": "Carlos Pérez",
        "seal": "Sello Municipal",
        "department_name": "Secretaría General",
        "municipality_name": "Municipalidad del Futuro",
    }


def _make_r2_mock(pdf_url: str = "http://r2/test.pdf") -> MagicMock:
    r2 = MagicMock()
    r2.get_tosign_url = MagicMock(return_value=pdf_url)
    r2.upload_oficial = MagicMock()
    return r2


def _make_httpx_patch(pdf_bytes: bytes = b"%PDF signed"):
    resp = MagicMock()
    resp.content = pdf_bytes
    resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_transaction_mock():
    conn = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


async def _run_in_threadpool(fn, *args, **kwargs):
    return fn(*args, **kwargs)


class TestClaimSkipLocked:

    def test_claim_sql_contains_skip_locked(self):
        import inspect
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._claim_one)
        assert "SKIP LOCKED" in source

    def test_claim_sql_updates_expires_at(self):
        import inspect
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._claim_one)
        assert "expires_at" in source
        assert "NOW()" in source

    def test_claim_sql_sets_processing(self):
        import inspect
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._claim_one)
        assert "'processing'" in source

    def test_claim_sql_filters_pending_and_sign(self):
        import inspect
        from workers.escri import EscriWorker
        source = inspect.getsource(EscriWorker._claim_one)
        assert "'pending'" in source
        assert "'sign'" in source

    @pytest.mark.asyncio
    async def test_claim_returns_none_when_empty_queue(self):
        from workers.escri import EscriWorker
        worker = EscriWorker()
        with patch("workers.escri.fetch_one", new_callable=AsyncMock, return_value=None):
            result = await worker._claim_one()
        assert result is None

    @pytest.mark.asyncio
    async def test_claim_returns_dict_when_job_found(self):
        session = _make_session()
        from workers.escri import EscriWorker
        worker = EscriWorker()
        with patch("workers.escri.fetch_one", new_callable=AsyncMock, return_value=session):
            result = await worker._claim_one()
        assert result is not None
        assert "session_id" in result


class TestWorkerOneShotSuccess:

    @pytest.mark.asyncio
    async def test_success_path_calls_confirm_and_finalize(self):
        session = _make_session(is_confirming=False)
        from workers.escri import EscriWorker
        worker = EscriWorker()

        m_confirm = AsyncMock()
        m_finalize = AsyncMock()
        m_cancel = AsyncMock()

        with patch("workers.escri.get_tenant_r2_client", new_callable=AsyncMock, return_value=_make_r2_mock()), \
             patch("workers.escri.call_notary_sign_pdf", new_callable=AsyncMock, return_value=b"%PDF signed"), \
             patch("workers.escri.get_signer_data", new_callable=AsyncMock, return_value=_make_signer_data()), \
             patch("workers.escri.get_city_from_settings", new_callable=AsyncMock, return_value="Ciudad"), \
             patch("workers.escri.confirm_number", m_confirm), \
             patch("workers.escri.finalize_number", m_finalize), \
             patch("workers.escri.cancel_number", m_cancel), \
             patch("workers.escri.run_in_threadpool", side_effect=_run_in_threadpool), \
             patch("workers.escri.transaction", return_value=_make_transaction_mock()), \
             patch("workers.escri.execute", new_callable=AsyncMock), \
             patch("workers.escri.fetch_one", AsyncMock(return_value={"session_id": "fence_ok", "type": "HTML"})), \
             patch("httpx.AsyncClient", return_value=_make_httpx_patch()), \
             patch.object(worker, "_get_official_number", new=AsyncMock(return_value="IF-2026-00001")), \
             patch.object(worker, "_mark_document_signed", new=AsyncMock()), \
             patch.object(worker, "_mark_session_signed", new=AsyncMock()):
            await worker._process_job(session)

        m_confirm.assert_awaited_once()
        m_finalize.assert_awaited_once()
        m_cancel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_path_does_not_call_cancel_number(self):
        session = _make_session()
        from workers.escri import EscriWorker
        worker = EscriWorker()
        m_cancel = AsyncMock()

        with patch("workers.escri.get_tenant_r2_client", new_callable=AsyncMock, return_value=_make_r2_mock()), \
             patch("workers.escri.call_notary_sign_pdf", new_callable=AsyncMock, return_value=b"%PDF"), \
             patch("workers.escri.get_signer_data", new_callable=AsyncMock, return_value=_make_signer_data()), \
             patch("workers.escri.get_city_from_settings", new_callable=AsyncMock, return_value="Ciudad"), \
             patch("workers.escri.confirm_number", new_callable=AsyncMock), \
             patch("workers.escri.finalize_number", new_callable=AsyncMock), \
             patch("workers.escri.cancel_number", m_cancel), \
             patch("workers.escri.run_in_threadpool", side_effect=_run_in_threadpool), \
             patch("workers.escri.transaction", return_value=_make_transaction_mock()), \
             patch("workers.escri.execute", new_callable=AsyncMock), \
             patch("workers.escri.fetch_one", AsyncMock(return_value={"session_id": "fence_ok", "type": "HTML"})), \
             patch("httpx.AsyncClient", return_value=_make_httpx_patch()), \
             patch.object(worker, "_get_official_number", new=AsyncMock(return_value="IF-2026-00001")), \
             patch.object(worker, "_mark_document_signed", new=AsyncMock()), \
             patch.object(worker, "_mark_session_signed", new=AsyncMock()):
            await worker._process_job(session)

        m_cancel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_path_marks_session_signed(self):
        session = _make_session()
        from workers.escri import EscriWorker
        worker = EscriWorker()
        mark_signed = AsyncMock()

        with patch("workers.escri.get_tenant_r2_client", new_callable=AsyncMock, return_value=_make_r2_mock()), \
             patch("workers.escri.call_notary_sign_pdf", new_callable=AsyncMock, return_value=b"%PDF"), \
             patch("workers.escri.get_signer_data", new_callable=AsyncMock, return_value=_make_signer_data()), \
             patch("workers.escri.get_city_from_settings", new_callable=AsyncMock, return_value="Ciudad"), \
             patch("workers.escri.confirm_number", new_callable=AsyncMock), \
             patch("workers.escri.finalize_number", new_callable=AsyncMock), \
             patch("workers.escri.cancel_number", new_callable=AsyncMock), \
             patch("workers.escri.run_in_threadpool", side_effect=_run_in_threadpool), \
             patch("workers.escri.transaction", return_value=_make_transaction_mock()), \
             patch("workers.escri.execute", new_callable=AsyncMock), \
             patch("workers.escri.fetch_one", AsyncMock(return_value={"session_id": "fence_ok", "type": "HTML"})), \
             patch("httpx.AsyncClient", return_value=_make_httpx_patch()), \
             patch.object(worker, "_get_official_number", new=AsyncMock(return_value="IF-2026-00001")), \
             patch.object(worker, "_mark_document_signed", new=AsyncMock()), \
             patch.object(worker, "_mark_session_signed", mark_signed):
            await worker._process_job(session)

        mark_signed.assert_awaited_once()
        assert mark_signed.call_args.args[1] == "IF-2026-00001"


class TestResumeConfirming:

    @pytest.mark.asyncio
    async def test_resume_skips_confirm_number(self):
        session = _make_session(is_confirming=True)
        from workers.escri import EscriWorker
        worker = EscriWorker()
        m_confirm = AsyncMock()
        m_finalize = AsyncMock()

        with patch("workers.escri.get_tenant_r2_client", new_callable=AsyncMock, return_value=_make_r2_mock()), \
             patch("workers.escri.call_notary_sign_pdf", new_callable=AsyncMock, return_value=b"%PDF"), \
             patch("workers.escri.get_signer_data", new_callable=AsyncMock, return_value=_make_signer_data()), \
             patch("workers.escri.get_city_from_settings", new_callable=AsyncMock, return_value="Ciudad"), \
             patch("workers.escri.confirm_number", m_confirm), \
             patch("workers.escri.finalize_number", m_finalize), \
             patch("workers.escri.cancel_number", new_callable=AsyncMock), \
             patch("workers.escri.run_in_threadpool", side_effect=_run_in_threadpool), \
             patch("workers.escri.transaction", return_value=_make_transaction_mock()), \
             patch("workers.escri.execute", new_callable=AsyncMock), \
             patch("workers.escri.fetch_one", AsyncMock(return_value={"session_id": "fence_ok", "type": "HTML"})), \
             patch("httpx.AsyncClient", return_value=_make_httpx_patch()), \
             patch.object(worker, "_get_official_number", new=AsyncMock(return_value="IF-2026-00001")), \
             patch.object(worker, "_mark_document_signed", new=AsyncMock()), \
             patch.object(worker, "_mark_session_signed", new=AsyncMock()):
            await worker._process_job(session)

        m_confirm.assert_not_awaited()
        m_finalize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resume_still_calls_finalize(self):
        session = _make_session(is_confirming=True)
        from workers.escri import EscriWorker
        worker = EscriWorker()
        m_finalize = AsyncMock()

        with patch("workers.escri.get_tenant_r2_client", new_callable=AsyncMock, return_value=_make_r2_mock()), \
             patch("workers.escri.call_notary_sign_pdf", new_callable=AsyncMock, return_value=b"%PDF"), \
             patch("workers.escri.get_signer_data", new_callable=AsyncMock, return_value=_make_signer_data()), \
             patch("workers.escri.get_city_from_settings", new_callable=AsyncMock, return_value="Ciudad"), \
             patch("workers.escri.confirm_number", new_callable=AsyncMock), \
             patch("workers.escri.finalize_number", m_finalize), \
             patch("workers.escri.cancel_number", new_callable=AsyncMock), \
             patch("workers.escri.run_in_threadpool", side_effect=_run_in_threadpool), \
             patch("workers.escri.transaction", return_value=_make_transaction_mock()), \
             patch("workers.escri.execute", new_callable=AsyncMock), \
             patch("workers.escri.fetch_one", AsyncMock(return_value={"session_id": "fence_ok", "type": "HTML"})), \
             patch("httpx.AsyncClient", return_value=_make_httpx_patch()), \
             patch.object(worker, "_get_official_number", new=AsyncMock(return_value="IF-2026-00001")), \
             patch.object(worker, "_mark_document_signed", new=AsyncMock()), \
             patch.object(worker, "_mark_session_signed", new=AsyncMock()):
            await worker._process_job(session)

        m_finalize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resume_on_error_does_not_cancel_number(self):
        session = _make_session(is_confirming=True)
        from workers.escri import EscriWorker
        worker = EscriWorker()
        m_cancel = AsyncMock()
        m_finalize = AsyncMock(side_effect=RuntimeError("DB down"))
        mark_failed = AsyncMock()

        fetch_responses = [
            {"session_id": "fence_ok"},
            {"reservation_status": "RESERVED"},
        ]
        fetch_call_count = {"n": -1}
        async def multi_fetch_one(*args, **kwargs):
            fetch_call_count["n"] += 1
            idx = min(fetch_call_count["n"], len(fetch_responses) - 1)
            return fetch_responses[idx]

        with patch("workers.escri.get_tenant_r2_client", new_callable=AsyncMock, return_value=_make_r2_mock()), \
             patch("workers.escri.call_notary_sign_pdf", new_callable=AsyncMock, return_value=b"%PDF"), \
             patch("workers.escri.get_signer_data", new_callable=AsyncMock, return_value=_make_signer_data()), \
             patch("workers.escri.get_city_from_settings", new_callable=AsyncMock, return_value="Ciudad"), \
             patch("workers.escri.confirm_number", new_callable=AsyncMock), \
             patch("workers.escri.finalize_number", m_finalize), \
             patch("workers.escri.cancel_number", m_cancel), \
             patch("workers.escri.run_in_threadpool", side_effect=_run_in_threadpool), \
             patch("workers.escri.execute", new_callable=AsyncMock), \
             patch("workers.escri.fetch_one", side_effect=multi_fetch_one), \
             patch("httpx.AsyncClient", return_value=_make_httpx_patch()), \
             patch.object(worker, "_get_official_number", new=AsyncMock(return_value="IF-2026-00001")), \
             patch.object(worker, "_mark_document_signed", new=AsyncMock()), \
             patch.object(worker, "_mark_session_failed", mark_failed):
            await worker._process_job(session)

        m_cancel.assert_not_awaited()
        mark_failed.assert_awaited_once()


class TestWorkerErrorHandling:

    @pytest.mark.asyncio
    async def test_notary_failure_calls_cancel_and_marks_failed(self):
        session = _make_session(is_confirming=False)
        from workers.escri import EscriWorker
        worker = EscriWorker()
        m_cancel = AsyncMock()
        mark_failed = AsyncMock()

        with patch("workers.escri.get_tenant_r2_client", new_callable=AsyncMock, return_value=_make_r2_mock()), \
             patch("workers.escri.call_notary_sign_pdf", new_callable=AsyncMock, side_effect=RuntimeError("notary timeout")), \
             patch("workers.escri.get_signer_data", new_callable=AsyncMock, return_value=_make_signer_data()), \
             patch("workers.escri.get_city_from_settings", new_callable=AsyncMock, return_value="Ciudad"), \
             patch("workers.escri.confirm_number", new_callable=AsyncMock), \
             patch("workers.escri.finalize_number", new_callable=AsyncMock), \
             patch("workers.escri.cancel_number", m_cancel), \
             patch("workers.escri.fetch_one", new_callable=AsyncMock, return_value={"reservation_status": "RESERVED"}), \
             patch("workers.escri.run_in_threadpool", side_effect=_run_in_threadpool), \
             patch("workers.escri.execute", new_callable=AsyncMock), \
             patch("httpx.AsyncClient", return_value=_make_httpx_patch()), \
             patch.object(worker, "_get_official_number", new=AsyncMock(return_value="IF-2026-00001")), \
             patch.object(worker, "_mark_session_failed", mark_failed):
            await worker._process_job(session)

        m_cancel.assert_awaited_once()
        mark_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_number_failure_still_marks_session_failed(self):
        session = _make_session(is_confirming=False)
        from workers.escri import EscriWorker
        worker = EscriWorker()
        mark_failed = AsyncMock()

        with patch("workers.escri.get_tenant_r2_client", new_callable=AsyncMock, return_value=_make_r2_mock()), \
             patch("workers.escri.call_notary_sign_pdf", new_callable=AsyncMock, side_effect=RuntimeError("notary down")), \
             patch("workers.escri.get_signer_data", new_callable=AsyncMock, return_value=_make_signer_data()), \
             patch("workers.escri.get_city_from_settings", new_callable=AsyncMock, return_value="Ciudad"), \
             patch("workers.escri.confirm_number", new_callable=AsyncMock), \
             patch("workers.escri.finalize_number", new_callable=AsyncMock), \
             patch("workers.escri.cancel_number", new_callable=AsyncMock, side_effect=RuntimeError("DB down")), \
             patch("workers.escri.fetch_one", new_callable=AsyncMock, return_value={"reservation_status": "RESERVED"}), \
             patch("workers.escri.run_in_threadpool", side_effect=_run_in_threadpool), \
             patch("workers.escri.execute", new_callable=AsyncMock), \
             patch("httpx.AsyncClient", return_value=_make_httpx_patch()), \
             patch.object(worker, "_get_official_number", new=AsyncMock(return_value="IF-2026-00001")), \
             patch.object(worker, "_mark_session_failed", mark_failed):
            await worker._process_job(session)

        mark_failed.assert_awaited_once()


class TestEscriConfiguration:

    def test_constants_have_reasonable_defaults(self):
        from workers import escri
        assert escri.PENDING_TTL_MINUTES >= 1
        assert escri.PROCESSING_TTL_MINUTES >= 1
        assert escri.FALLBACK_POLL_SECONDS >= 10
        assert escri.HEARTBEAT_LOG_SECONDS >= 10

    def test_worker_id_contains_hostname_and_pid(self):
        import socket, os
        from workers.escri import EscriWorker
        worker = EscriWorker()
        assert socket.gethostname() in worker._worker_id
        assert str(os.getpid()) in worker._worker_id


class TestNotaryBreakerOpen:

    @pytest.mark.asyncio
    async def test_breaker_open_requeues_to_pending(self):
        from workers.escri import EscriWorker
        from shared.exceptions import NotaryBreakerOpenError

        job = _make_session()
        worker = EscriWorker()

        m_r2 = _make_r2_mock()
        m_signer = AsyncMock(return_value=_make_signer_data())
        m_city = AsyncMock(return_value="Ciudad Test")
        m_fetch_one = AsyncMock(return_value={"official_number": "IF-2026-99", "type": "HTML"})
        m_execute = AsyncMock()
        m_notary = AsyncMock(side_effect=NotaryBreakerOpenError(retry_after=45))
        m_requeue = AsyncMock()
        m_failed = AsyncMock()
        worker._requeue_session_pending = m_requeue
        worker._mark_session_failed = m_failed

        with (
            patch("workers.escri.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch("workers.escri.get_signer_data", m_signer),
            patch("workers.escri.get_city_from_settings", m_city),
            patch("workers.escri.fetch_one", m_fetch_one),
            patch("workers.escri.execute", m_execute),
            patch("workers.escri.call_notary_sign_pdf", m_notary),
            patch("workers.escri.run_in_threadpool", _run_in_threadpool),
            patch("httpx.AsyncClient", return_value=_make_httpx_patch()),
        ):
            await worker._process_job(job)

        m_requeue.assert_awaited_once()
        m_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_breaker_open_does_not_cancel_number(self):
        from workers.escri import EscriWorker
        from shared.exceptions import NotaryBreakerOpenError

        job = _make_session()
        worker = EscriWorker()

        m_r2 = _make_r2_mock()
        m_signer = AsyncMock(return_value=_make_signer_data())
        m_city = AsyncMock(return_value="Ciudad Test")
        m_fetch_one = AsyncMock(return_value={"official_number": "IF-2026-99", "type": "HTML"})
        m_execute = AsyncMock()
        m_cancel = AsyncMock()
        m_notary = AsyncMock(side_effect=NotaryBreakerOpenError(retry_after=30))
        worker._requeue_session_pending = AsyncMock()
        worker._mark_session_failed = AsyncMock()

        with (
            patch("workers.escri.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch("workers.escri.get_signer_data", m_signer),
            patch("workers.escri.get_city_from_settings", m_city),
            patch("workers.escri.fetch_one", m_fetch_one),
            patch("workers.escri.execute", m_execute),
            patch("workers.escri.call_notary_sign_pdf", m_notary),
            patch("workers.escri.cancel_number", m_cancel),
            patch("workers.escri.run_in_threadpool", _run_in_threadpool),
            patch("httpx.AsyncClient", return_value=_make_httpx_patch()),
        ):
            await worker._process_job(job)

        m_cancel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_breaker_open_resume_also_requeues(self):
        from workers.escri import EscriWorker
        from shared.exceptions import NotaryBreakerOpenError

        job = _make_session(is_confirming=True)
        worker = EscriWorker()

        m_r2 = _make_r2_mock()
        m_signer = AsyncMock(return_value=_make_signer_data())
        m_city = AsyncMock(return_value="Ciudad Test")
        m_fetch_one = AsyncMock(return_value={"official_number": "IF-2026-99", "type": "HTML"})
        m_execute = AsyncMock()
        m_cancel = AsyncMock()
        m_notary = AsyncMock(side_effect=NotaryBreakerOpenError(retry_after=30))
        m_requeue = AsyncMock()
        worker._requeue_session_pending = m_requeue
        worker._mark_session_failed = AsyncMock()

        with (
            patch("workers.escri.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch("workers.escri.get_signer_data", m_signer),
            patch("workers.escri.get_city_from_settings", m_city),
            patch("workers.escri.fetch_one", m_fetch_one),
            patch("workers.escri.execute", m_execute),
            patch("workers.escri.call_notary_sign_pdf", m_notary),
            patch("workers.escri.cancel_number", m_cancel),
            patch("workers.escri.run_in_threadpool", _run_in_threadpool),
            patch("httpx.AsyncClient", return_value=_make_httpx_patch()),
        ):
            await worker._process_job(job)

        m_requeue.assert_awaited_once()
        m_cancel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_requeue_pending_sql_sets_status_pending(self):
        import inspect
        from workers.escri import EscriWorker

        source = inspect.getsource(EscriWorker._requeue_session_pending)
        assert "'pending'" in source
        assert "claimed_by" in source
        assert "expires_at" in source

    @pytest.mark.asyncio
    async def test_real_notary_failure_marks_session_failed(self):
        from workers.escri import EscriWorker
        from shared.exceptions import NotaryUnavailableError

        job = _make_session()
        worker = EscriWorker()

        m_r2 = _make_r2_mock()
        m_signer = AsyncMock(return_value=_make_signer_data())
        m_city = AsyncMock(return_value="Ciudad Test")
        m_fetch_one = AsyncMock(return_value={"official_number": "IF-2026-99", "type": "HTML"})
        m_execute = AsyncMock()
        m_cancel = AsyncMock()
        m_failed = AsyncMock()
        m_notary = AsyncMock(side_effect=NotaryUnavailableError("Notary caído"))
        worker._mark_session_failed = m_failed
        worker._requeue_session_pending = AsyncMock()

        with (
            patch("workers.escri.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch("workers.escri.get_signer_data", m_signer),
            patch("workers.escri.get_city_from_settings", m_city),
            patch("workers.escri.fetch_one", m_fetch_one),
            patch("workers.escri.execute", m_execute),
            patch("workers.escri.call_notary_sign_pdf", m_notary),
            patch("workers.escri.cancel_number", m_cancel),
            patch("workers.escri.run_in_threadpool", _run_in_threadpool),
            patch("httpx.AsyncClient", return_value=_make_httpx_patch()),
        ):
            await worker._process_job(job)

        m_failed.assert_awaited_once()
        m_cancel.assert_awaited_once()
