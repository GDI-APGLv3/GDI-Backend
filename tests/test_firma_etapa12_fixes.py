import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from fastapi import HTTPException


DOC_ID = "aaaaaaaa-0000-0000-0000-000000000001"
SCHEMA = "100_test"
DT_ID = "cccc0003-0000-0000-0000-000000000003"
DEPT_ID = "dddd0004-0000-0000-0000-000000000004"
RES_ID = "eeee0005-0000-0000-0000-000000000005"


def _savepoint_ctx():
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=None)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _conn_ctx(conn):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_conn(fetchrow_values, execute_side_effect):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=list(fetchrow_values))
    conn.execute = AsyncMock(side_effect=list(execute_side_effect))
    conn.transaction = MagicMock(return_value=_savepoint_ctx())
    return conn


class TestUniqueViolationConstraintMatch:

    @pytest.mark.asyncio
    async def test_pk_violation_recupera_numero_del_request_paralelo(self):
        from shared.numbering import generate_official_number

        exc = asyncpg.UniqueViolationError("duplicate key")
        exc.constraint_name = "official_documents_pkey"

        existing_row = {
            "official_number": "CAEX-2025-00000001-SMG-ADGEN",
            "global_sequence": 1,
            "department_id": DEPT_ID,
        }

        conn = _make_conn(
            fetchrow_values=[
                None,
                {"next_number": 1},
                existing_row,
            ],
            execute_side_effect=[
                "SET",
                "SELECT",
                exc,
            ],
        )

        with patch("shared.numbering._get_city_acronym", AsyncMock(return_value="SMG")), \
             patch("shared.numbering._get_user_department", AsyncMock(return_value=("ADGEN", DEPT_ID))), \
             patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)):

            official_number, department_id, seq = await generate_official_number(
                "CAEX", "user-id", 2025,
                schema_name=SCHEMA, document_id=DOC_ID, reference="ref",
                document_type_id=DT_ID, content={},
            )

        assert official_number == "CAEX-2025-00000001-SMG-ADGEN"
        assert seq == 1

    @pytest.mark.asyncio
    async def test_sequence_index_violation_relanza_y_logea_critical(self):
        from shared.numbering import generate_official_number

        exc = asyncpg.UniqueViolationError("duplicate key")
        exc.constraint_name = f"idx_{SCHEMA}_official_docs_unique_global_number"

        conn = _make_conn(
            fetchrow_values=[
                None,
                {"next_number": 1},
            ],
            execute_side_effect=[
                "SET",
                "SELECT",
                exc,
            ],
        )

        with patch("shared.numbering._get_city_acronym", AsyncMock(return_value="SMG")), \
             patch("shared.numbering._get_user_department", AsyncMock(return_value=("ADGEN", DEPT_ID))), \
             patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)), \
             patch("shared.numbering.logger") as mock_logger:

            with pytest.raises(asyncpg.UniqueViolationError):
                await generate_official_number(
                    "CAEX", "user-id", 2025,
                    schema_name=SCHEMA, document_id=DOC_ID, reference="ref",
                    document_type_id=DT_ID, content={},
                )

        assert mock_logger.critical.call_count == 1
        critical_msg = mock_logger.critical.call_args[0][0]
        assert "numbering.duplicate_number_violation" in critical_msg
        assert exc.constraint_name in critical_msg
        assert SCHEMA in critical_msg
        assert DOC_ID in critical_msg

    @pytest.mark.asyncio
    async def test_reserve_number_global_sequence_index_violation_relanza(self):
        from shared.numbering import reserve_number

        exc = asyncpg.UniqueViolationError("duplicate key")
        exc.constraint_name = f"idx_{SCHEMA}_official_docs_unique_global_number"

        conn = _make_conn(
            fetchrow_values=[
                {"id": DT_ID, "special_numbering": False},
                None,
                None,
                {"next_number": 1},
            ],
            execute_side_effect=[
                "SET",
                "SELECT",
                "DELETE 0",
                "DELETE 0",
                exc,
            ],
        )

        with patch("shared.numbering._get_city_acronym", AsyncMock(return_value="SMG")), \
             patch("shared.numbering._get_user_department", AsyncMock(return_value=("ADGEN", DEPT_ID))), \
             patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)), \
             patch("shared.numbering.logger") as mock_logger:

            with pytest.raises(asyncpg.UniqueViolationError):
                await reserve_number(
                    "CAEX", "user-id", 2025,
                    schema_name=SCHEMA, document_id=DOC_ID, reference="ref",
                    document_type_id=DT_ID, content={},
                )

        assert mock_logger.critical.call_count == 1


def _make_request(user_id):
    req = MagicMock()
    req.state.tenant_user_id = user_id
    return req


def _session_row(**overrides):
    row = {
        "session_id": "abc123",
        "file_id": "file1",
        "schema_name": SCHEMA,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "document_id": DOC_ID,
        "is_numerator": True,
        "number": "CAEX-2025-00000001-SMG-ADGEN",
        "status": "pending",
        "reservation_id": RES_ID,
    }
    row.update(overrides)
    return row


class TestCancelCasRace:

    @pytest.mark.asyncio
    async def test_cas_pierde_carrera_signed_devuelve_409_sin_tocar_numero(self):
        from endpoints.digital_signature import cancel as cancel_mod

        session = _session_row()
        fetch_one_mock = AsyncMock(side_effect=[
            session,
            None,
            {"status": "signed"},
        ])
        cancel_number_mock = AsyncMock()
        release_mock = AsyncMock()
        audit_mock = AsyncMock()

        with patch.object(cancel_mod, "fetch_one", fetch_one_mock), \
             patch.object(cancel_mod, "cancel_number", cancel_number_mock), \
             patch.object(cancel_mod, "FirmadorGDIProvider") as provider_cls, \
             patch.object(cancel_mod, "release_signing_lock_R2_fail", release_mock), \
             patch.object(cancel_mod, "log_signature_event", audit_mock):

            with pytest.raises(HTTPException) as exc_info:
                await cancel_mod.cancel_signing(
                    body=cancel_mod.CancelRequest(session_id="abc123"),
                    request=_make_request(session["user_id"]),
                    current_user=MagicMock(),
                    schema_name=SCHEMA,
                )

        assert exc_info.value.status_code == 409
        cancel_number_mock.assert_not_awaited()
        release_mock.assert_not_awaited()
        audit_mock.assert_not_awaited()
        provider_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_cas_pierde_carrera_otro_estado_devuelve_status_sin_409(self):
        from endpoints.digital_signature import cancel as cancel_mod

        session = _session_row()
        fetch_one_mock = AsyncMock(side_effect=[
            session,
            None,
            {"status": "cancelled"},
        ])
        cancel_number_mock = AsyncMock()

        with patch.object(cancel_mod, "fetch_one", fetch_one_mock), \
             patch.object(cancel_mod, "cancel_number", cancel_number_mock), \
             patch.object(cancel_mod, "FirmadorGDIProvider") as provider_cls, \
             patch.object(cancel_mod, "release_signing_lock_R2_fail", AsyncMock()), \
             patch.object(cancel_mod, "log_signature_event", AsyncMock()):

            result = await cancel_mod.cancel_signing(
                body=cancel_mod.CancelRequest(session_id="abc123"),
                request=_make_request(session["user_id"]),
                current_user=MagicMock(),
                schema_name=SCHEMA,
            )

        assert result == {"status": "cancelled"}
        cancel_number_mock.assert_not_awaited()
        provider_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_cas_gana_carrera_procede_normal(self):
        from endpoints.digital_signature import cancel as cancel_mod

        session = _session_row()
        fetch_one_mock = AsyncMock(side_effect=[
            session,
            {"session_id": "abc123"},
        ])
        cancel_number_mock = AsyncMock()
        provider_instance = MagicMock()
        provider_instance.cancel_signing = MagicMock()

        async def _run_sync(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch.object(cancel_mod, "fetch_one", fetch_one_mock), \
             patch.object(cancel_mod, "cancel_number", cancel_number_mock), \
             patch.object(cancel_mod, "FirmadorGDIProvider", return_value=provider_instance), \
             patch.object(cancel_mod, "release_signing_lock_R2_fail", AsyncMock()), \
             patch.object(cancel_mod, "log_signature_event", AsyncMock()), \
             patch.object(cancel_mod, "run_in_threadpool", AsyncMock(side_effect=_run_sync)):

            result = await cancel_mod.cancel_signing(
                body=cancel_mod.CancelRequest(session_id="abc123"),
                request=_make_request(session["user_id"]),
                current_user=MagicMock(),
                schema_name=SCHEMA,
            )

        assert result == {"status": "cancelled"}
        cancel_number_mock.assert_awaited_once()


class TestCancelNumberSpecialLaneGuard:

    @pytest.mark.asyncio
    async def test_ticket_viejo_no_libera_el_carril(self):
        from shared.numbering import cancel_number

        od_row = {
            "id": DOC_ID,
            "numbering_regime": "SPECIAL",
            "document_type_id": DT_ID,
            "department_id": DEPT_ID,
            "year": 2025,
            "official_number": "TIPO-2025-0006-SMG-SEC",
        }
        conn = _make_conn(
            fetchrow_values=[od_row],
            execute_side_effect=["UPDATE 0"],
        )

        with patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)), \
             patch("shared.alerts.send_alert_mail", AsyncMock()):
            await cancel_number(
                DOC_ID, schema_name=SCHEMA, reason="cancel_rezagado",
                reservation_id="ticket-viejo-uuid",
            )

        assert conn.execute.call_count == 1
        sql_called = conn.execute.call_args_list[0][0][0]
        assert "document_number_counters" not in sql_called

    @pytest.mark.asyncio
    async def test_cancel_exitoso_special_libera_el_carril(self):
        from shared.numbering import cancel_number

        od_row = {
            "id": DOC_ID,
            "numbering_regime": "SPECIAL",
            "document_type_id": DT_ID,
            "department_id": DEPT_ID,
            "year": 2025,
            "official_number": "TIPO-2025-0006-SMG-SEC",
        }
        conn = _make_conn(
            fetchrow_values=[od_row],
            execute_side_effect=["UPDATE 1", "UPDATE 1"],
        )

        with patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)), \
             patch("shared.alerts.send_alert_mail", AsyncMock()):
            await cancel_number(
                DOC_ID, schema_name=SCHEMA, reason="cancelado_por_test",
                reservation_id=RES_ID,
            )

        assert conn.execute.call_count == 2
        counter_call = str(conn.execute.call_args_list[1])
        assert "active_reservation_document_id = NULL" in counter_call

    @pytest.mark.asyncio
    async def test_global_no_toca_counter_incluso_sin_ticket(self):
        from shared.numbering import cancel_number

        od_row = {
            "id": DOC_ID,
            "numbering_regime": "GLOBAL",
            "document_type_id": DT_ID,
            "department_id": DEPT_ID,
            "year": 2025,
            "official_number": "CAEX-2025-00000001-SMG-ADGEN",
        }
        conn = _make_conn(
            fetchrow_values=[od_row],
            execute_side_effect=["UPDATE 0"],
        )

        with patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)), \
             patch("shared.alerts.send_alert_mail", AsyncMock()):
            await cancel_number(
                DOC_ID, schema_name=SCHEMA, reason="cancel_rezagado",
                reservation_id="ticket-viejo-uuid",
            )

        assert conn.execute.call_count == 1
