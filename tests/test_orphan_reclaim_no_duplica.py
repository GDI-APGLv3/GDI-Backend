
import pytest
from unittest.mock import AsyncMock, patch


SCHEMA = "100_test"
DOC = "aaaaaaaa-1111-2222-3333-444444444444"
USER = "11111111-1111-1111-1111-111111111111"
SESSION = "SESHUERFANA01"


def _fila():
    return {
        "session_id": SESSION,
        "schema_name": SCHEMA,
        "document_id": DOC,
        "user_id": USER,
    }


async def _correr(update_devuelve):
    from jobs import orphan_inprocess as job

    fetch_one = AsyncMock(side_effect=[{"?column?": 1}, update_devuelve])

    with patch("database.fetch_one", fetch_one), \
         patch("database.fetch_all", new_callable=AsyncMock, return_value=[_fila()]), \
         patch("database.execute", new_callable=AsyncMock), \
         patch("services.documents.signing.r2_lock.reclaim_orphan_inprocess",
               new_callable=AsyncMock) as m_reclaim, \
         patch("services.documents.signing.audit_logger.log_signature_event",
               new_callable=AsyncMock) as m_audit:
        await job._reclaim_async()

    return m_audit, m_reclaim


class TestNoDuplicaElCierre:

    @pytest.mark.asyncio
    async def test_si_marco_la_sesion_escribe_su_fila(self):
        m_audit, m_reclaim = await _correr({"session_id": SESSION})

        m_reclaim.assert_awaited_once()
        m_audit.assert_awaited_once()
        kw = m_audit.await_args.kwargs
        assert kw["result"] == "fail"
        assert kw["failure_reason"] == "session_expired_orphan_reclaimed"
        assert kw["session_id"] == SESSION

    @pytest.mark.asyncio
    async def test_si_otro_la_cerro_primero_no_escribe_nada(self):
        m_audit, m_reclaim = await _correr(None)

        m_reclaim.assert_awaited_once()
        m_audit.assert_not_awaited()
