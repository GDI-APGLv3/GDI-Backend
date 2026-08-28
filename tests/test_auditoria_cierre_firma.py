
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


SCHEMA = "100_test"
DOC = "aaaaaaaa-1111-2222-3333-444444444444"
USER = "11111111-1111-1111-1111-111111111111"


def _sesion(status="pending", session_id="SES-1", is_numerator=False, number=None):
    return {
        "session_id": session_id,
        "document_id": DOC,
        "user_id": USER,
        "status": status,
        "is_numerator": is_numerator,
        "number": number,
        "reservation_id": None,
        "cancelled_at": None,
    }


class TestCierreDeTanda:

    @pytest.mark.asyncio
    async def test_una_tanda_que_cae_cierra_el_trail_de_cada_documento(self):
        import services.documents.signing.batch_digital as bd

        sesiones = [_sesion(session_id="SES-A"), _sesion(session_id="SES-B")]

        with patch.object(bd, "_sesiones_de_la_tanda", new_callable=AsyncMock,
                          return_value=sesiones), \
             patch.object(bd, "execute", new_callable=AsyncMock, return_value="UPDATE 1"), \
             patch.object(bd, "_limpiar_manifiesto", new_callable=AsyncMock), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.audit_logger.log_signature_event",
                   new_callable=AsyncMock) as m_audit:
            await bd.cancelar_tanda(
                "batch-1", schema_name=SCHEMA,
                motivo="la tanda venció sin completarse",
            )

        assert m_audit.await_count == 2
        for llamada in m_audit.await_args_list:
            kw = llamada.kwargs
            assert kw["result"] == "fail"
            assert kw["signature_method"] == "digital_token"
            assert kw["failure_reason"] == "la tanda venció sin completarse"
            assert kw["schema_name"] == SCHEMA
            assert kw["document_id"] == DOC
            assert kw["user_id"] == USER
        assert {c.kwargs["session_id"] for c in m_audit.await_args_list} == {"SES-A", "SES-B"}

    @pytest.mark.asyncio
    async def test_una_sesion_ya_caida_no_se_audita_de_nuevo(self):
        import services.documents.signing.batch_digital as bd

        with patch.object(bd, "_sesiones_de_la_tanda", new_callable=AsyncMock,
                          return_value=[_sesion(status="failed", session_id="SES-C")]), \
             patch.object(bd, "execute", new_callable=AsyncMock, return_value="UPDATE 1"), \
             patch.object(bd, "_limpiar_manifiesto", new_callable=AsyncMock), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.audit_logger.log_signature_event",
                   new_callable=AsyncMock) as m_audit:
            await bd.cancelar_tanda("batch-1", schema_name=SCHEMA, motivo="x")

        m_audit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_si_otro_gano_el_cas_no_se_escribe_el_cierre(self):
        import services.documents.signing.batch_digital as bd

        with patch.object(bd, "_sesiones_de_la_tanda", new_callable=AsyncMock,
                          return_value=[_sesion(session_id="SES-D")]), \
             patch.object(bd, "execute", new_callable=AsyncMock, return_value="UPDATE 0"), \
             patch.object(bd, "_limpiar_manifiesto", new_callable=AsyncMock), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.audit_logger.log_signature_event",
                   new_callable=AsyncMock) as m_audit:
            resultado = await bd.cancelar_tanda("batch-1", schema_name=SCHEMA, motivo="x")

        m_audit.assert_not_awaited()
        assert resultado["cancelled"] == 0

    @pytest.mark.asyncio
    async def test_una_sesion_firmada_no_se_toca(self):
        import services.documents.signing.batch_digital as bd

        with patch.object(bd, "_sesiones_de_la_tanda", new_callable=AsyncMock,
                          return_value=[_sesion(status="signed", session_id="SES-E")]), \
             patch.object(bd, "execute", new_callable=AsyncMock) as m_exec, \
             patch.object(bd, "_limpiar_manifiesto", new_callable=AsyncMock), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.audit_logger.log_signature_event",
                   new_callable=AsyncMock) as m_audit:
            await bd.cancelar_tanda("batch-1", schema_name=SCHEMA, motivo="x")

        m_audit.assert_not_awaited()
        m_exec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_si_falla_la_auditoria_igual_se_sueltan_los_recursos(self):
        import services.documents.signing.batch_digital as bd

        with patch.object(bd, "_sesiones_de_la_tanda", new_callable=AsyncMock,
                          return_value=[_sesion(session_id="SES-F")]), \
             patch.object(bd, "execute", new_callable=AsyncMock, return_value="UPDATE 1"), \
             patch.object(bd, "_limpiar_manifiesto", new_callable=AsyncMock), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail",
                   new_callable=AsyncMock) as m_lock, \
             patch("services.documents.signing.audit_logger.log_signature_event",
                   new_callable=AsyncMock, side_effect=RuntimeError("audit caida")):
            resultado = await bd.cancelar_tanda("batch-1", schema_name=SCHEMA, motivo="x")

        m_lock.assert_awaited_once()
        assert resultado["cancelled"] == 1


class TestCierreDeHuerfanaCompletada:

    @pytest.mark.asyncio
    async def test_la_firma_que_el_sweeper_rescata_queda_auditada_con_su_numero(self):
        from workers import sweeper_escri as sw

        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": DOC})

        class _Tx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return False

        r2 = MagicMock()
        r2.get_oficial_bytes.return_value = b"%PDF-1.7 firmado"

        with patch.object(sw, "finalize_number", new_callable=AsyncMock), \
             patch.object(sw, "db_transaction", return_value=_Tx()), \
             patch.object(sw, "fetch_one", new_callable=AsyncMock,
                          return_value={"session_id": "SES-OK"}), \
             patch.object(sw, "send_alert_mail", new_callable=AsyncMock), \
             patch("services.shared.auto_link_trigger.collect_auto_link_results",
                   new_callable=AsyncMock), \
             patch("services.storage.publish_public.maybe_publish_official_pdf",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.audit_logger.log_signature_event",
                   new_callable=AsyncMock) as m_audit:
            ok = await sw._complete_digital_confirming_orphan(
                schema=SCHEMA, doc_id=DOC, reservation_id="res-1",
                official_number="TOKEN-2026-00000001-TXST-AMBIE",
                user_id=USER, r2=r2,
            )

        assert ok is True
        m_audit.assert_awaited_once()
        kw = m_audit.await_args.kwargs
        assert kw["result"] == "ok"
        assert kw["signature_method"] == "digital_token"
        assert kw["official_number"] == "TOKEN-2026-00000001-TXST-AMBIE"
        assert kw["user_id"] == USER
        assert kw["session_id"] == "SES-OK"

    @pytest.mark.asyncio
    async def test_si_el_draft_ya_no_estaba_en_circuito_no_se_audita(self):
        from workers import sweeper_escri as sw

        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        class _Tx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return False

        r2 = MagicMock()
        r2.get_oficial_bytes.return_value = None

        with patch.object(sw, "finalize_number", new_callable=AsyncMock), \
             patch.object(sw, "db_transaction", return_value=_Tx()), \
             patch.object(sw, "fetch_one", new_callable=AsyncMock), \
             patch.object(sw, "send_alert_mail", new_callable=AsyncMock) as m_alert, \
             patch("services.shared.auto_link_trigger.collect_auto_link_results",
                   new_callable=AsyncMock), \
             patch("services.documents.signing.audit_logger.log_signature_event",
                   new_callable=AsyncMock) as m_audit:
            await sw._complete_digital_confirming_orphan(
                schema=SCHEMA, doc_id=DOC, reservation_id="res-1",
                official_number="TOKEN-2026-00000002-TXST-AMBIE",
                user_id=USER, r2=r2,
            )

        m_audit.assert_not_awaited()
        m_alert.assert_awaited_once()
        subject = m_alert.await_args.kwargs.get("subject", "")
        assert "FUERA DE CIRCUITO" in subject
