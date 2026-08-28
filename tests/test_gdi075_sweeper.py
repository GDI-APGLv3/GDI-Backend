
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _r(
    doc_id: str | None = None,
    reservation_id: str | None = None,
    official_number: str = "IF-2026-00001",
    updated_at: datetime | None = None,
) -> dict:
    return {
        "doc_id": doc_id or str(uuid.uuid4()),
        "reservation_id": reservation_id or str(uuid.uuid4()),
        "official_number": official_number,
        "updated_at": updated_at or datetime.now(timezone.utc),
    }


def _last_session(doc_id: str | None = None, user_id: str | None = None) -> dict:
    return {
        "document_id": doc_id or str(uuid.uuid4()),
        "user_id": user_id or str(uuid.uuid4()),
    }


class TestProcessingExpiredRequeue:

    @pytest.mark.asyncio
    async def test_requeues_processing_session_to_pending(self):
        sid    = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        rows = [{"session_id": sid, "document_id": doc_id, "payload": {}}]

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=rows), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock, return_value=None), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute:
            from workers.sweeper_escri import _handle_processing_expired
            await _handle_processing_expired("100_test")

        assert m_execute.await_count >= 2

        first_call_sql = m_execute.await_args_list[0].args[0]
        assert "pending" in first_call_sql.lower()
        assert "processing" in first_call_sql.lower()

    @pytest.mark.asyncio
    async def test_no_action_when_no_expired_processing(self):
        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[]) as m_fetch_all, \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute:
            from workers.sweeper_escri import _handle_processing_expired
            await _handle_processing_expired("100_test")

        m_execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notify_sent_after_requeue(self):
        sid    = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        rows = [{"session_id": sid, "document_id": doc_id, "payload": {}}]
        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=rows), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock, return_value=None), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute:
            from workers.sweeper_escri import _handle_processing_expired
            await _handle_processing_expired("100_test")

        all_sqls = [str(call.args[0]) for call in m_execute.await_args_list if call.args]
        assert any("pg_notify" in sql or "pg_notify" in sql.lower() for sql in all_sqls)

    @pytest.mark.asyncio
    async def test_payload_double_encoded_str_no_explota_y_requeue_dict(self):
        import json as _json
        sid    = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        corrupted = _json.dumps(_json.dumps({"official_number": "IF-2026-00003797-MDEV"}))
        corrupted = _json.loads(corrupted)
        rows = [{"session_id": sid, "document_id": doc_id, "payload": corrupted}]

        od_row = {"reservation_status": "CONFIRMING", "official_number": "IF-2026-00003797-MDEV"}
        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=rows), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock, return_value=od_row), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute:
            from workers.sweeper_escri import _handle_processing_expired
            await _handle_processing_expired("100_test")

        assert m_execute.await_count >= 2
        update_call = m_execute.await_args_list[0]
        assert "pending" in update_call.args[0].lower()
        sent_payload = update_call.args[2]
        assert isinstance(sent_payload, dict)
        assert sent_payload["official_number"] == "IF-2026-00003797-MDEV"
        assert sent_payload["is_confirming"] is True


class TestPayloadAsDict:

    def test_normal_dict_pasa_directo(self):
        from shared.utils import payload_as_dict
        assert payload_as_dict({"a": 1}) == {"a": 1}

    def test_none_y_vacio(self):
        from shared.utils import payload_as_dict
        assert payload_as_dict(None) == {}
        assert payload_as_dict("") == {}

    def test_single_encoded_str(self):
        from shared.utils import payload_as_dict
        assert payload_as_dict('{"a": 1}') == {"a": 1}

    def test_double_encoded_str(self):
        import json as _json
        from shared.utils import payload_as_dict
        val = _json.dumps(_json.dumps({"official_number": "X-1"}))
        assert payload_as_dict(_json.loads(val)) == {"official_number": "X-1"}

    def test_basura_no_json_da_dict_vacio(self):
        from shared.utils import payload_as_dict
        assert payload_as_dict("no-es-json") == {}
        assert payload_as_dict(42) == {}
        assert payload_as_dict(["lista"]) == {}


class TestReservedOrphanCancel:

    @pytest.mark.asyncio
    async def test_cancel_called_for_reserved_orphan(self):
        row = _r()
        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.cancel_number", new_callable=AsyncMock) as m_cancel, \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock):
            from workers.sweeper_escri import _handle_reserved_orphans
            await _handle_reserved_orphans("100_test")

        m_cancel.assert_awaited_once_with(
            row["doc_id"],
            schema_name="100_test",
            reason="sweeper_reserved_orphan",
            reservation_id=row["reservation_id"],
            alert=False,
        )

    @pytest.mark.asyncio
    async def test_cancel_not_called_when_no_orphans(self):
        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[]), \
             patch("workers.sweeper_escri.cancel_number", new_callable=AsyncMock) as m_cancel:
            from workers.sweeper_escri import _handle_reserved_orphans
            await _handle_reserved_orphans("100_test")

        m_cancel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_error_does_not_abort_other_orphans(self):
        rows = [_r(), _r()]
        cancel_call_count = 0

        async def _cancel_first_fails(doc_id, *, schema_name, reason, reservation_id, alert):
            nonlocal cancel_call_count
            cancel_call_count += 1
            if cancel_call_count == 1:
                raise RuntimeError("DB down")

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=rows), \
             patch("workers.sweeper_escri.cancel_number", side_effect=_cancel_first_fails), \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock):
            from workers.sweeper_escri import _handle_reserved_orphans
            await _handle_reserved_orphans("100_test")

        assert cancel_call_count == 2

    def test_query_excludes_live_sessions(self):
        import inspect
        from workers.sweeper_escri import _handle_reserved_orphans
        source = inspect.getsource(_handle_reserved_orphans)
        assert "NOT EXISTS" in source
        assert "pending" in source
        assert "processing" in source

    def test_query_excludes_live_digital_sessions(self):
        import inspect
        from workers.sweeper_escri import _handle_reserved_orphans
        source = inspect.getsource(_handle_reserved_orphans)
        assert "digital_signature_sessions" in source


class TestConfirmingExpiredRequeue:

    @pytest.mark.asyncio
    async def test_requeues_confirming_with_is_confirming_flag(self):
        row = _r()
        last = _last_session()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[None, last]), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute:
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        all_sqls = [str(call.args[0]) for call in m_execute.await_args_list if call.args]
        assert any("is_confirming" in sql for sql in all_sqls)

    @pytest.mark.asyncio
    async def test_never_calls_cancel_number_for_confirming(self):
        row = _r()
        last = _last_session()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[None, last]), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock), \
             patch("workers.sweeper_escri.cancel_number", new_callable=AsyncMock) as m_cancel:
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        m_cancel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_action_when_no_session_found(self):
        row = _r()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock, return_value=None), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute:
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        m_execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sends_pg_notify_after_requeue(self):
        row = _r()
        last = _last_session()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[None, last]), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute:
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        all_sqls = [str(call.args[0]) for call in m_execute.await_args_list if call.args]
        assert any("pg_notify" in sql for sql in all_sqls)


def _dss_row(user_id: str | None = None) -> dict:
    return {"user_id": user_id or str(uuid.uuid4())}


class TestConfirmingDigitalOrphan:

    @pytest.mark.asyncio
    async def test_confirming_con_dss_no_se_reencola(self):
        row = _r()
        dss = _dss_row()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[dss, None]), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute, \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock):
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        all_sqls = [str(call.args[0]) for call in m_execute.await_args_list if call.args]
        assert not any("is_confirming" in sql for sql in all_sqls), (
            "El sweeper re-encoló un CONFIRMING de origen digital"
        )

    @pytest.mark.asyncio
    async def test_confirming_con_dss_envia_alerta_primera_vez(self):
        row = _r()
        dss = _dss_row()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[dss, None]), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock), \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock) as m_alert:
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        m_alert.assert_awaited_once()
        subject = m_alert.call_args.kwargs.get("subject", "") or m_alert.call_args.args[0]
        assert "digital" in subject.lower() or "ESCRI" in subject

    @pytest.mark.asyncio
    async def test_confirming_con_dss_inserta_centinela(self):
        row = _r()
        dss = _dss_row()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[dss, None]), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute, \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock):
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        all_sqls = [str(call.args[0]) for call in m_execute.await_args_list if call.args]
        assert any("digital_confirming_orphan" in sql for sql in all_sqls), (
            "El centinela anti-spam no fue insertado"
        )

    @pytest.mark.asyncio
    async def test_confirming_con_dss_y_centinela_no_re_alerta(self):
        row = _r()
        dss = _dss_row()
        orphan_marker = {"found": 1}

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[dss, orphan_marker]), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute, \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock) as m_alert:
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        m_alert.assert_not_awaited()
        m_execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirming_sin_dss_re_encola_normal(self):
        row = _r()
        last = _last_session()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[None, last]), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute, \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock) as m_alert:
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        all_sqls = [str(call.args[0]) for call in m_execute.await_args_list if call.args]
        assert any("is_confirming" in sql for sql in all_sqls)
        m_alert.assert_not_awaited()


class _FakeTransaction:

    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _old_row(**kwargs) -> dict:
    kwargs.setdefault(
        "updated_at",
        datetime.now(timezone.utc) - timedelta(hours=99),
    )
    return _r(**kwargs)


class TestConfirmingDigitalOrphanGraceResolution:

    @pytest.mark.asyncio
    async def test_caso_a_pdf_en_oficial_completa_y_no_cancela(self):
        row = _old_row()
        dss = _dss_row()
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": row["doc_id"]})

        r2_mock = MagicMock()
        r2_mock.exists_oficial.return_value = True
        r2_mock.get_oficial_bytes.return_value = None

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[dss]), \
             patch("workers.sweeper_escri.cancel_number", new_callable=AsyncMock) as m_cancel, \
             patch("workers.sweeper_escri.finalize_number", new_callable=AsyncMock) as m_finalize, \
             patch("workers.sweeper_escri.db_transaction", return_value=_FakeTransaction(conn)), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute, \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock) as m_alert, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock,
                   return_value=r2_mock):
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        m_finalize.assert_awaited_once_with(row["doc_id"], row["reservation_id"], schema_name="100_test")
        m_cancel.assert_not_awaited()
        assert conn.execute.await_count == 1
        od_sql = str(conn.execute.await_args_list[0].args[0])
        assert "official_documents" in od_sql and "signed_at" in od_sql
        assert conn.fetchrow.await_count == 1
        draft_sql = str(conn.fetchrow.await_args_list[0].args[0])
        assert "document_draft" in draft_sql and "'signed'" in draft_sql
        assert "RETURNING id" in draft_sql
        all_sqls = [str(call.args[0]) for call in m_execute.await_args_list if call.args]
        assert not any("is_confirming" in sql for sql in all_sqls)
        assert not any("digital_signature_sessions" in sql for sql in all_sqls)
        m_alert.assert_awaited_once()
        subject = m_alert.call_args.kwargs.get("subject", "") or m_alert.call_args.args[0]
        assert "AUTO-COMPLETADO" in subject

    @pytest.mark.asyncio
    async def test_caso_b_pdf_ausente_cancela_y_marca_sesion_failed(self):
        row = _old_row()
        dss = _dss_row()

        r2_mock = MagicMock()
        r2_mock.exists_oficial.return_value = False

        _marcada = {"session_id": "SES-CASO-B",
                    "user_id": "11111111-1111-1111-1111-111111111111"}

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[dss, _marcada]) as m_fetch_one, \
             patch("workers.sweeper_escri.cancel_number", new_callable=AsyncMock) as m_cancel, \
             patch("workers.sweeper_escri.finalize_number", new_callable=AsyncMock) as m_finalize, \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute, \
             patch("services.documents.signing.audit_logger.log_signature_event",
                   new_callable=AsyncMock) as m_audit, \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock) as m_alert, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock,
                   return_value=r2_mock):
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        m_finalize.assert_not_awaited()
        m_cancel.assert_awaited_once()
        _, cancel_kwargs = m_cancel.call_args
        assert cancel_kwargs["reason"] == "confirming_orphan_timeout"
        assert cancel_kwargs["reservation_id"] == row["reservation_id"]

        all_sqls = [str(call.args[0]) for call in m_fetch_one.await_args_list if call.args]
        assert any("digital_signature_sessions" in sql and "'failed'" in sql for sql in all_sqls)
        assert any("RETURNING" in sql for sql in all_sqls)

        m_audit.assert_awaited_once()
        _kw = m_audit.await_args.kwargs
        assert _kw["result"] == "fail"
        assert _kw["failure_reason"] == "confirming_orphan_timeout"
        assert _kw["signature_method"] == "digital_token"
        assert _kw["session_id"] == "SES-CASO-B"

        m_alert.assert_awaited_once()
        subject = m_alert.call_args.kwargs.get("subject", "") or m_alert.call_args.args[0]
        assert "AUTO-CANCELADO" in subject
        _, cancel_kwargs = m_cancel.call_args
        assert cancel_kwargs["from_states"] == ('RESERVED', 'CONFIRMING')

    @pytest.mark.asyncio
    async def test_caso_b_cancel_0_filas_no_declara_exito(self):
        row = _old_row()
        dss = _dss_row()

        r2_mock = MagicMock()
        r2_mock.exists_oficial.return_value = False

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[dss]), \
             patch("workers.sweeper_escri.cancel_number", new_callable=AsyncMock,
                   return_value=0) as m_cancel, \
             patch("workers.sweeper_escri.finalize_number", new_callable=AsyncMock) as m_finalize, \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute, \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock) as m_alert, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock,
                   return_value=r2_mock):
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        m_cancel.assert_awaited_once()
        m_finalize.assert_not_awaited()
        all_sqls = [str(call.args[0]) for call in m_execute.await_args_list if call.args]
        assert not any("digital_signature_sessions" in sql and "'failed'" in sql for sql in all_sqls)
        for call in m_alert.await_args_list:
            subject = call.kwargs.get("subject", "") or (call.args[0] if call.args else "")
            assert "AUTO-CANCELADO" not in subject

    @pytest.mark.asyncio
    async def test_r2_error_transitorio_no_toca_nada(self):
        row = _old_row()
        dss = _dss_row()

        r2_mock = MagicMock()
        r2_mock.exists_oficial.side_effect = Exception("R2 timeout")

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[dss]), \
             patch("workers.sweeper_escri.cancel_number", new_callable=AsyncMock) as m_cancel, \
             patch("workers.sweeper_escri.finalize_number", new_callable=AsyncMock) as m_finalize, \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute, \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock) as m_alert, \
             patch("services.storage.cloudflare.get_tenant_r2_client", new_callable=AsyncMock,
                   return_value=r2_mock):
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        m_finalize.assert_not_awaited()
        m_cancel.assert_not_awaited()
        m_execute.assert_not_awaited()
        m_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_huerfano_joven_no_resuelve_solo_alerta(self):
        row = _r()
        dss = _dss_row()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock,
                   side_effect=[dss, None]), \
             patch("workers.sweeper_escri.cancel_number", new_callable=AsyncMock) as m_cancel, \
             patch("workers.sweeper_escri.finalize_number", new_callable=AsyncMock) as m_finalize, \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock), \
             patch("workers.sweeper_escri.send_alert_mail", new_callable=AsyncMock) as m_alert:
            from workers.sweeper_escri import _handle_confirming_expired
            await _handle_confirming_expired("100_test")

        m_finalize.assert_not_awaited()
        m_cancel.assert_not_awaited()
        m_alert.assert_awaited_once()
        subject = m_alert.call_args.kwargs.get("subject", "") or m_alert.call_args.args[0]
        assert "AUTO-COMPLETADO" not in subject and "AUTO-CANCELADO" not in subject


class TestConfirmedAutoHeal:

    @pytest.mark.asyncio
    async def test_autoheal_payload_contains_confirmed_autoheal(self):
        row = _r()
        last = _last_session()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock, return_value=last), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute:
            from workers.sweeper_escri import _handle_confirmed_not_signed
            await _handle_confirmed_not_signed("100_test")

        all_sqls = [str(call.args[0]) for call in m_execute.await_args_list if call.args]
        assert any("confirmed_autoheal" in sql for sql in all_sqls)

    @pytest.mark.asyncio
    async def test_autoheal_no_action_without_last_session(self):
        row = _r()

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[row]), \
             patch("workers.sweeper_escri.fetch_one", new_callable=AsyncMock, return_value=None), \
             patch("workers.sweeper_escri.execute", new_callable=AsyncMock) as m_execute:
            from workers.sweeper_escri import _handle_confirmed_not_signed
            await _handle_confirmed_not_signed("100_test")

        m_execute.assert_not_awaited()


class TestSweeperScheduler:

    def test_schedule_sweeper_adds_job_to_scheduler(self):
        from unittest.mock import MagicMock
        scheduler = MagicMock()
        from workers.sweeper_escri import schedule_sweeper_escri
        schedule_sweeper_escri(scheduler)
        scheduler.add_job.assert_called_once()
        kwargs = scheduler.add_job.call_args.kwargs
        assert kwargs.get("max_instances") == 1
        assert kwargs.get("coalesce") is True
        assert kwargs.get("id") == "sweeper_escri"

    def test_schedule_sweeper_uses_interval_trigger(self):
        from unittest.mock import MagicMock
        scheduler = MagicMock()
        from workers.sweeper_escri import schedule_sweeper_escri
        schedule_sweeper_escri(scheduler)
        positional_args = scheduler.add_job.call_args.args
        assert positional_args[1] == "interval"


class TestSweeperSchemaIteration:

    @pytest.mark.asyncio
    async def test_sweeper_skips_when_no_schemas(self):
        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=[]):
            from workers.sweeper_escri import _run_sweeper_body as _run_sweeper
            await _run_sweeper()

    @pytest.mark.asyncio
    async def test_sweeper_processes_each_schema(self):
        schemas = [{"schema_name": "100_abc"}, {"schema_name": "101_def"}]
        swept: list[str] = []

        async def _mock_sweep(schema: str) -> None:
            swept.append(schema)

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=schemas), \
             patch("workers.sweeper_escri._sweep_schema", side_effect=_mock_sweep):
            from workers.sweeper_escri import _run_sweeper_body as _run_sweeper
            await _run_sweeper()

        assert swept == ["100_abc", "101_def"]

    @pytest.mark.asyncio
    async def test_sweeper_continues_on_schema_error(self):
        schemas = [{"schema_name": "100_abc"}, {"schema_name": "101_def"}]
        swept: list[str] = []
        error_count = 0

        async def _mock_sweep(schema: str) -> None:
            if schema == "100_abc":
                raise RuntimeError("error en 100_abc")
            swept.append(schema)

        with patch("workers.sweeper_escri.fetch_all", new_callable=AsyncMock, return_value=schemas), \
             patch("workers.sweeper_escri._sweep_schema", side_effect=_mock_sweep):
            from workers.sweeper_escri import _run_sweeper_body as _run_sweeper
            await _run_sweeper()

        assert "101_def" in swept


class TestSweeperAdvisoryLock:

    @pytest.mark.asyncio
    async def test_lock_tomado_ejecuta_body_y_libera(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from contextlib import asynccontextmanager
        from workers.sweeper_escri import _run_sweeper, SWEEPER_ADVISORY_LOCK_ID

        conn = MagicMock()
        conn.fetchval = AsyncMock(side_effect=[True, True])

        @asynccontextmanager
        async def _ctx(**kwargs):
            yield conn

        body = AsyncMock()
        with patch("shared.advisory_lock.get_conn", _ctx), \
             patch("workers.sweeper_escri._run_sweeper_body", body):
            await _run_sweeper()

        body.assert_awaited_once()
        calls = [c.args for c in conn.fetchval.await_args_list]
        assert ("SELECT pg_try_advisory_lock($1)", SWEEPER_ADVISORY_LOCK_ID) == calls[0]
        assert ("SELECT pg_advisory_unlock($1)", SWEEPER_ADVISORY_LOCK_ID) == calls[1]

    @pytest.mark.asyncio
    async def test_lock_ocupado_saltea_sin_barrer(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from contextlib import asynccontextmanager
        from workers.sweeper_escri import _run_sweeper

        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value=False)

        @asynccontextmanager
        async def _ctx(**kwargs):
            yield conn

        body = AsyncMock()
        with patch("shared.advisory_lock.get_conn", _ctx), \
             patch("workers.sweeper_escri._run_sweeper_body", body):
            await _run_sweeper()

        body.assert_not_awaited()
        assert conn.fetchval.await_count == 1

    @pytest.mark.asyncio
    async def test_body_explota_igual_libera_lock(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from contextlib import asynccontextmanager
        from workers.sweeper_escri import _run_sweeper

        conn = MagicMock()
        conn.fetchval = AsyncMock(side_effect=[True, True])

        @asynccontextmanager
        async def _ctx(**kwargs):
            yield conn

        body = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("shared.advisory_lock.get_conn", _ctx), \
             patch("workers.sweeper_escri._run_sweeper_body", body):
            with pytest.raises(RuntimeError):
                await _run_sweeper()

        assert conn.fetchval.await_count == 2


class TestPendingExpired:

    @pytest.mark.asyncio
    async def test_pending_expirada_se_marca_expired(self):
        from unittest.mock import AsyncMock, patch
        from workers.sweeper_escri import _handle_pending_expired

        rows = [{"session_id": "a" * 32, "document_id": "b" * 32}]
        fetch = AsyncMock(return_value=rows)
        with patch("workers.sweeper_escri.fetch_all", fetch):
            await _handle_pending_expired("100_test")

        sql = fetch.await_args.args[0]
        assert "status        = 'expired'" in sql or "'expired'" in sql
        assert "job_type    = 'sign'" in sql or "'sign'" in sql
        assert "expires_at  < NOW()" in sql or "expires_at" in sql
        assert "pending" in sql

    @pytest.mark.asyncio
    async def test_orden_pending_expired_corre_primero(self):
        from unittest.mock import patch
        from workers.sweeper_escri import _sweep_schema

        order = []
        def track(name):
            async def _f(schema):
                order.append(name)
            return _f

        with patch("workers.sweeper_escri._handle_tandas_huerfanas", track("tandas")), \
             patch("workers.sweeper_escri._handle_tandas_caidas_sin_limpiar", track("tandas_sucias")), \
             patch("workers.sweeper_escri._handle_pending_expired", track("pending")), \
             patch("workers.sweeper_escri._handle_processing_expired", track("processing")), \
             patch("workers.sweeper_escri._handle_dts_processing_expired", track("dts_processing")), \
             patch("workers.sweeper_escri._handle_common_pending_expired", track("common_pending")), \
             patch("workers.sweeper_escri._handle_common_processing_expired", track("common_processing")), \
             patch("workers.sweeper_escri._handle_reserved_orphans", track("reserved")), \
             patch("workers.sweeper_escri._handle_confirming_expired", track("confirming")), \
             patch("workers.sweeper_escri._handle_confirmed_not_signed", track("confirmed")), \
             patch("workers.sweeper_escri._handle_confirmed_rejected_conflict", track("conflict")):
            await _sweep_schema("100_test")

        assert order.index("pending") < order.index("reserved")
        assert order.index("tandas") < order.index("reserved")
        assert order.index("tandas_sucias") < order.index("reserved")

    @pytest.mark.asyncio
    async def test_dts_no_se_expira(self):
        from unittest.mock import AsyncMock, patch
        from workers.sweeper_escri import _handle_pending_expired

        fetch = AsyncMock(return_value=[])
        with patch("workers.sweeper_escri.fetch_all", fetch):
            await _handle_pending_expired("100_test")
        sql = fetch.await_args.args[0]
        assert "'sign'" in sql and "'dts'" not in sql


class TestBarridoDeTandasCaidasSinLimpiar:

    @pytest.mark.asyncio
    async def test_limpia_una_tanda_caida_que_nadie_dio_de_baja(self):
        from unittest.mock import patch, AsyncMock
        from workers import sweeper_escri as sw

        canceladas = []

        async def _cancelar(batch_id, *, schema_name, motivo):
            canceladas.append((batch_id, motivo))
            return {"cancelled": 2}

        filas = [{"batch_id": "batch-1", "sucias": 2, "motivo": "stale_reservation"}]
        with patch.object(sw, "fetch_all", AsyncMock(return_value=filas)), \
             patch("services.documents.signing.batch_digital.cancelar_tanda", _cancelar):
            await sw._handle_tandas_caidas_sin_limpiar("100_test")

        assert canceladas == [("batch-1", "stale_reservation")], (
            "se reusa el motivo real de la caída, que dice más que uno genérico"
        )

    @pytest.mark.asyncio
    async def test_solo_mira_las_que_no_se_limpiaron(self):
        from unittest.mock import patch, AsyncMock
        from workers import sweeper_escri as sw

        capturado = {}

        async def _fetch_all(sql, *args, **kw):
            capturado["sql"] = sql
            return []

        with patch.object(sw, "fetch_all", _fetch_all):
            await sw._handle_tandas_caidas_sin_limpiar("100_test")

        assert "cancelled_at IS NULL" in capturado["sql"]
        assert "LIMIT" in capturado["sql"], (
            "la primera pasada tras el deploy encuentra todo el pasado junto"
        )

    @pytest.mark.asyncio
    async def test_una_tanda_que_falla_no_frena_a_las_demas(self):
        from unittest.mock import patch, AsyncMock
        from workers import sweeper_escri as sw

        vistas = []

        async def _cancelar(batch_id, *, schema_name, motivo):
            vistas.append(batch_id)
            if batch_id == "batch-1":
                raise RuntimeError("R2 caido")
            return {"cancelled": 1}

        filas = [
            {"batch_id": "batch-1", "sucias": 1, "motivo": None},
            {"batch_id": "batch-2", "sucias": 1, "motivo": None},
        ]
        with patch.object(sw, "fetch_all", AsyncMock(return_value=filas)), \
             patch("services.documents.signing.batch_digital.cancelar_tanda", _cancelar):
            await sw._handle_tandas_caidas_sin_limpiar("100_test")

        assert vistas == ["batch-1", "batch-2"]
