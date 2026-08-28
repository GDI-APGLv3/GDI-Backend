
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobs.retry_failed_publications import (
    retry_failed_publications,
    _run_retry_publications,
)


class TestRetryFailedPublications:

    @pytest.mark.asyncio
    async def test_republica_y_limpia_flag(self):
        rows = [{
            "session_id": "sess-1",
            "document_id": "doc-1",
            "payload": {"official_number": "IF-2026-00001", "publish_failed": True},
        }]

        m_execute = AsyncMock()
        with (
            patch("database.fetch_all", AsyncMock(return_value=rows)),
            patch("database.execute", m_execute),
            patch("services.storage.cloudflare.get_tenant_r2_client",
                  AsyncMock(return_value=MagicMock())),
            patch("services.storage.publish_public.maybe_publish_official_pdf",
                  AsyncMock(return_value=True)) as m_publish,
            patch("fastapi.concurrency.run_in_threadpool",
                  AsyncMock(return_value=b"%PDF oficial")),
        ):
            republicados = await retry_failed_publications("100_test")

        assert republicados == 1
        m_publish.assert_awaited_once()
        m_execute.assert_awaited_once()
        persisted_payload = m_execute.await_args.args[0]
        assert "publish_failed" not in persisted_payload

    @pytest.mark.asyncio
    async def test_sin_filas_no_hace_nada(self):
        with patch("database.fetch_all", AsyncMock(return_value=[])):
            republicados = await retry_failed_publications("100_test")
        assert republicados == 0

    @pytest.mark.asyncio
    async def test_publish_sigue_fallando_no_limpia_flag(self):
        rows = [{
            "session_id": "sess-1",
            "document_id": "doc-1",
            "payload": {"official_number": "IF-2026-00001", "publish_failed": True},
        }]
        m_execute = AsyncMock()
        with (
            patch("database.fetch_all", AsyncMock(return_value=rows)),
            patch("database.execute", m_execute),
            patch("services.storage.cloudflare.get_tenant_r2_client",
                  AsyncMock(return_value=MagicMock())),
            patch("services.storage.publish_public.maybe_publish_official_pdf",
                  AsyncMock(return_value=False)),
            patch("fastapi.concurrency.run_in_threadpool",
                  AsyncMock(return_value=b"%PDF oficial")),
        ):
            republicados = await retry_failed_publications("100_test")

        assert republicados == 0
        m_execute.assert_not_awaited()


class TestAlertaSoloCuandoHayAlgo:

    @pytest.mark.asyncio
    async def test_sin_republicaciones_no_manda_mail(self):
        m_mail = AsyncMock()
        with (
            patch("shared.tenant_validation.get_valid_schemas",
                  AsyncMock(return_value=["100_muni"])),
            patch("jobs.retry_failed_publications.retry_failed_publications",
                  AsyncMock(return_value=0)),
            patch("shared.alerts.send_alert_mail", m_mail),
        ):
            await _run_retry_publications()

        m_mail.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_con_republicaciones_manda_mail(self):
        m_mail = AsyncMock()
        with (
            patch("shared.tenant_validation.get_valid_schemas",
                  AsyncMock(return_value=["100_muni"])),
            patch("jobs.retry_failed_publications.retry_failed_publications",
                  AsyncMock(return_value=2)),
            patch("shared.alerts.send_alert_mail", m_mail),
        ):
            await _run_retry_publications()

        m_mail.assert_awaited_once()
        assert "2" in m_mail.await_args.kwargs["body"]

    @pytest.mark.asyncio
    async def test_un_tenant_que_explota_avisa(self):
        m_mail = AsyncMock()
        with (
            patch("shared.tenant_validation.get_valid_schemas",
                  AsyncMock(return_value=["100_muni"])),
            patch("jobs.retry_failed_publications.retry_failed_publications",
                  AsyncMock(side_effect=RuntimeError("R2 caido"))),
            patch("shared.alerts.send_alert_mail", m_mail),
        ):
            await _run_retry_publications()

        m_mail.assert_awaited_once()
        assert "REVISIÓN" in m_mail.await_args.kwargs["subject"]
