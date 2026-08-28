
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.numbering import _es_final_normal, MOTIVOS_SIN_ALERTA


class TestQueMotivosNoAlertan:

    def test_los_dos_finales_normales(self):
        assert _es_final_normal("cancelled_by_user")
        assert _es_final_normal("digital_session_expired")

    def test_un_fallo_tecnico_si_alerta(self):
        assert not _es_final_normal("notary_timeout")
        assert not _es_final_normal("dispatch_digital_error: connection reset")
        assert not _es_final_normal("confirming_orphan_timeout")

    def test_una_tanda_abandonada_tampoco_alerta(self):
        assert _es_final_normal("la tanda venció sin completarse")
        assert _es_final_normal("la tanda cayó y quedó sin limpiar")

    def test_el_motivo_vacio_o_ausente_alerta(self):
        assert not _es_final_normal("")
        assert not _es_final_normal(None)

    def test_la_coincidencia_es_exacta_no_por_prefijo(self):
        assert not _es_final_normal("cancelled_by_user_agent_crash")
        assert not _es_final_normal("digital_session_expired_pero_algo_exploto")

    def test_los_espacios_no_cambian_la_decision(self):
        assert _es_final_normal("  cancelled_by_user  ")

    def test_la_lista_es_corta_a_proposito(self):
        assert MOTIVOS_SIN_ALERTA == {
            "cancelled_by_user",
            "digital_session_expired",
            "la tanda venció sin completarse",
            "la tanda cayó y quedó sin limpiar",
        }


DOC = "aaaaaaaa-1111-2222-3333-444444444444"
SCHEMA = "100_test"


def _conn_con_reserva():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={
        "id": DOC,
        "numbering_regime": "NORMAL",
        "document_type_id": "dt-1",
        "department_id": "dep-1",
        "year": 2026,
        "official_number": "TOKEN-2026-00002514-TXST-AMBIE",
    })
    conn.execute = AsyncMock(return_value="UPDATE 1")
    savepoint = MagicMock()
    savepoint.__aenter__ = AsyncMock(return_value=None)
    savepoint.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=savepoint)
    return conn


def _ctx(conn):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestElMailSeMandaONo:

    async def _cancelar(self, reason):
        from shared.numbering import cancel_number

        m_alert = AsyncMock()
        with patch("shared.numbering.get_conn", return_value=_ctx(_conn_con_reserva())), \
             patch("shared.alerts.send_alert_mail", m_alert):
            await cancel_number(DOC, schema_name=SCHEMA, reason=reason)
        return m_alert

    @pytest.mark.asyncio
    async def test_una_cancelacion_del_usuario_no_despierta_a_nadie(self):
        m_alert = await self._cancelar("cancelled_by_user")
        m_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_una_sesion_vencida_no_despierta_a_nadie(self):
        m_alert = await self._cancelar("digital_session_expired")
        m_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_un_fallo_de_notary_si_despierta(self):
        m_alert = await self._cancelar("notary_timeout")
        m_alert.assert_awaited_once()
        assert "Firma fallida" in m_alert.await_args.kwargs["subject"]

    @pytest.mark.asyncio
    async def test_el_numero_vuelve_al_pozo_igual_que_siempre(self):
        from shared.numbering import cancel_number

        for reason in ("cancelled_by_user", "notary_timeout"):
            conn = _conn_con_reserva()
            with patch("shared.numbering.get_conn", return_value=_ctx(conn)), \
                 patch("shared.alerts.send_alert_mail", AsyncMock()):
                filas = await cancel_number(DOC, schema_name=SCHEMA, reason=reason)
            assert filas == 1, f"{reason}: la reserva tiene que cancelarse igual"
