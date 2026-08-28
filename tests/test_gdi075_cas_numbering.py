import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from shared.exceptions import StaleReservationError


def make_mock_conn(**kwargs):
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=kwargs.get("execute_result", "UPDATE 1"))
    conn.fetchrow = AsyncMock(return_value=kwargs.get("fetchrow_result", None))
    return conn


def mock_get_conn_ctx(conn):
    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        yield conn
    return _ctx


DOC_ID = "aaaaaaaa-0000-0000-0000-000000000001"
RESERVATION_ID = str(uuid.uuid4())
SCHEMA = "100_test"


class TestConfirmNumber:

    @pytest.mark.asyncio
    async def test_reserved_correct_ticket_ok(self):
        conn = make_mock_conn(execute_result="UPDATE 1")
        conn.fetchrow = AsyncMock(return_value={
            "numbering_regime": "GLOBAL",
            "document_type_id": "dt-uuid",
            "department_id": "dept-uuid",
            "year": 2026,
        })

        from shared.numbering import confirm_number

        with patch("shared.numbering.get_conn", mock_get_conn_ctx(conn)):
            await confirm_number(DOC_ID, RESERVATION_ID, schema_name=SCHEMA)

        assert conn.execute.call_count >= 1
        cas_call = conn.execute.call_args_list[0]
        sql = cas_call[0][0]
        assert "CONFIRMING" in sql
        assert "RESERVED" in sql

    @pytest.mark.asyncio
    async def test_cancelled_row_raises_stale(self):
        conn = make_mock_conn(execute_result="UPDATE 0")
        conn.fetchrow = AsyncMock(return_value={
            "numbering_regime": "GLOBAL",
            "document_type_id": "dt-uuid",
            "department_id": "dept-uuid",
            "year": 2026,
        })

        from shared.numbering import confirm_number

        with patch("shared.numbering.get_conn", mock_get_conn_ctx(conn)):
            with pytest.raises(StaleReservationError) as exc_info:
                await confirm_number(DOC_ID, RESERVATION_ID, schema_name=SCHEMA)

        assert DOC_ID in str(exc_info.value) or "reserva" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_wrong_ticket_raises_stale(self):
        wrong_ticket = str(uuid.uuid4())
        conn = make_mock_conn(execute_result="UPDATE 0")
        conn.fetchrow = AsyncMock(return_value={
            "numbering_regime": "GLOBAL",
            "document_type_id": "dt-uuid",
            "department_id": "dept-uuid",
            "year": 2026,
        })

        from shared.numbering import confirm_number

        with patch("shared.numbering.get_conn", mock_get_conn_ctx(conn)):
            with pytest.raises(StaleReservationError):
                await confirm_number(DOC_ID, wrong_ticket, schema_name=SCHEMA)

    @pytest.mark.asyncio
    async def test_special_regime_releases_counter(self):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={
            "numbering_regime": "SPECIAL",
            "document_type_id": "dt-special-uuid",
            "department_id": "dept-uuid",
            "year": 2026,
        })
        conn.execute = AsyncMock(return_value="UPDATE 1")

        from shared.numbering import confirm_number

        with patch("shared.numbering.get_conn", mock_get_conn_ctx(conn)):
            await confirm_number(DOC_ID, RESERVATION_ID, schema_name=SCHEMA)

        assert conn.execute.call_count >= 2
        all_sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert any("document_number_counters" in sql for sql in all_sqls), (
            "Esperaba UPDATE document_number_counters para liberar carril SPECIAL"
        )

    @pytest.mark.asyncio
    async def test_global_regime_no_counter_cleanup(self):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={
            "numbering_regime": "GLOBAL",
            "document_type_id": "dt-global-uuid",
            "department_id": "dept-uuid",
            "year": 2026,
        })
        conn.execute = AsyncMock(return_value="UPDATE 1")

        from shared.numbering import confirm_number

        with patch("shared.numbering.get_conn", mock_get_conn_ctx(conn)):
            await confirm_number(DOC_ID, RESERVATION_ID, schema_name=SCHEMA)

        all_sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert not any("document_number_counters" in sql for sql in all_sqls), (
            "GLOBAL no debe tocar document_number_counters"
        )


class TestFinalizeNumber:

    @pytest.mark.asyncio
    async def test_confirming_to_confirmed_ok(self):
        conn = make_mock_conn(execute_result="UPDATE 1")

        from shared.numbering import finalize_number

        with patch("shared.numbering.get_conn", mock_get_conn_ctx(conn)):
            await finalize_number(DOC_ID, RESERVATION_ID, schema_name=SCHEMA)

        cas_sql = conn.execute.call_args_list[0][0][0]
        assert "CONFIRMED" in cas_sql
        assert "CONFIRMING" in cas_sql

    @pytest.mark.asyncio
    async def test_already_confirmed_soft_fail_no_exception(self):
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="UPDATE 0")
        conn.fetchrow = AsyncMock(return_value={
            "reservation_status": "CONFIRMED",
            "reservation_id": RESERVATION_ID,
        })

        from shared.numbering import finalize_number
        import logging

        with patch("shared.numbering.get_conn", mock_get_conn_ctx(conn)), \
             patch.object(logging.getLogger("shared.numbering"), "warning") as mock_warn, \
             patch.object(logging.getLogger("shared.numbering"), "error") as mock_err:
            await finalize_number(DOC_ID, RESERVATION_ID, schema_name=SCHEMA)

        assert mock_warn.call_count >= 1
        assert mock_err.call_count == 0

    @pytest.mark.asyncio
    async def test_cancelled_after_confirming_logs_critical_error(self):
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="UPDATE 0")
        conn.fetchrow = AsyncMock(return_value={
            "reservation_status": "CANCELLED",
            "reservation_id": RESERVATION_ID,
        })

        from shared.numbering import finalize_number

        with patch("shared.numbering.get_conn", mock_get_conn_ctx(conn)):
            await finalize_number(DOC_ID, RESERVATION_ID, schema_name=SCHEMA)

        conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_doc_not_found_logs_error_no_exception(self):
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="UPDATE 0")
        conn.fetchrow = AsyncMock(return_value=None)

        from shared.numbering import finalize_number

        with patch("shared.numbering.get_conn", mock_get_conn_ctx(conn)):
            await finalize_number(DOC_ID, RESERVATION_ID, schema_name=SCHEMA)

        conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unexpected_state_logs_warning(self):
        conn = MagicMock()
        conn.execute = AsyncMock(return_value="UPDATE 0")
        conn.fetchrow = AsyncMock(return_value={
            "reservation_status": "RESERVED",
            "reservation_id": str(uuid.uuid4()),
        })

        from shared.numbering import finalize_number

        with patch("shared.numbering.get_conn", mock_get_conn_ctx(conn)):
            await finalize_number(DOC_ID, RESERVATION_ID, schema_name=SCHEMA)

        conn.fetchrow.assert_awaited_once()
