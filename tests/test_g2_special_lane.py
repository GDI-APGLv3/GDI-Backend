import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from shared.exceptions import SpecialLaneBusyError, StaleReservationError


SCHEMA   = "test_tenant"
DOC_ID   = "aaaa0001-0000-0000-0000-000000000001"
OTHER_ID = "bbbb0002-0000-0000-0000-000000000002"
DT_ID    = "cccc0003-0000-0000-0000-000000000003"
DEPT_ID  = "dddd0004-0000-0000-0000-000000000004"
RES_ID   = "eeee0005-0000-0000-0000-000000000005"


def _savepoint_ctx():
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=None)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_conn(fetchrow_values):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=list(fetchrow_values))
    conn.execute  = AsyncMock(return_value="UPDATE 1")
    conn.transaction = MagicMock(return_value=_savepoint_ctx())
    return conn


def _conn_ctx(conn):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__  = AsyncMock(return_value=False)
    return ctx


class TestSpecialLaneBusy:

    @pytest.mark.asyncio
    async def test_terna_ocupada_por_otro_doc_lanza_busy_error(self):
        from shared.numbering import reserve_number

        conn = _make_conn([
            {"id": DT_ID, "special_numbering": True},
            {"last_number": 5, "active_reservation_document_id": OTHER_ID},
            {"id": OTHER_ID, "reserved_at": datetime.now(timezone.utc), "special_number": 6},
        ])

        with patch("shared.numbering._get_city_acronym",
                   AsyncMock(return_value="SMG")), \
             patch("shared.numbering._get_user_department",
                   AsyncMock(return_value=("SEC", DEPT_ID))), \
             patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)):

            with pytest.raises(SpecialLaneBusyError) as exc_info:
                await reserve_number(
                    "TIPO", "user-id", 2025,
                    schema_name=SCHEMA,
                    document_id=DOC_ID,
                    reference="ref",
                    document_type_id=DT_ID,
                    content={},
                )

        exc = exc_info.value
        assert exc.document_type_id == DT_ID
        assert exc.department_id    == DEPT_ID
        assert exc.year             == 2025

    @pytest.mark.asyncio
    async def test_terna_ocupada_nunca_cancela_reserva_ajena(self):
        from shared.numbering import reserve_number

        conn = _make_conn([
            {"id": DT_ID, "special_numbering": True},
            {"last_number": 5, "active_reservation_document_id": OTHER_ID},
            {"id": OTHER_ID, "reserved_at": datetime.now(timezone.utc), "special_number": 6},
        ])

        with patch("shared.numbering._get_city_acronym",
                   AsyncMock(return_value="SMG")), \
             patch("shared.numbering._get_user_department",
                   AsyncMock(return_value=("SEC", DEPT_ID))), \
             patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)):

            with pytest.raises(SpecialLaneBusyError):
                await reserve_number(
                    "TIPO", "user-id", 2025,
                    schema_name=SCHEMA,
                    document_id=DOC_ID,
                    reference="ref",
                    document_type_id=DT_ID,
                    content={},
                )

        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_busy_error_extiende_conflict_error(self):
        from shared.exceptions import ConflictError

        err = SpecialLaneBusyError(
            document_type_id=DT_ID,
            department_id=DEPT_ID,
            year=2025,
        )
        assert isinstance(err, ConflictError)
        assert DT_ID  in err.document_type_id
        assert "numeración en curso" in err.message

    @pytest.mark.asyncio
    async def test_mismo_doc_reutiliza_propia_reserva_sin_error(self):
        from shared.numbering import reserve_number

        conn = _make_conn([
            {"id": DT_ID, "special_numbering": True},
            {"last_number": 5, "active_reservation_document_id": DOC_ID},
            {"id": DOC_ID, "reserved_at": datetime.now(timezone.utc), "special_number": 6},
            {
                "official_number": "TIPO-2025-0006-SMG-SEC",
                "department_id": DEPT_ID,
                "special_number": 6,
                "reservation_id": "11111111-1111-1111-1111-111111111111",
            },
        ])

        with patch("shared.numbering._get_city_acronym",
                   AsyncMock(return_value="SMG")), \
             patch("shared.numbering._get_user_department",
                   AsyncMock(return_value=("SEC", DEPT_ID))), \
             patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)):

            result = await reserve_number(
                "TIPO", "user-id", 2025,
                schema_name=SCHEMA,
                document_id=DOC_ID,
                reference="ref",
                document_type_id=DT_ID,
                content={},
            )

        official_number, dept_id_out, seq, reservation_id = result
        assert official_number == "TIPO-2025-0006-SMG-SEC"
        assert seq == 6
        assert reservation_id == "11111111-1111-1111-1111-111111111111"


class TestCarrilLiberado:

    @pytest.mark.asyncio
    async def test_confirm_number_libera_carril_special(self):
        from shared.numbering import confirm_number

        od_row = {
            "numbering_regime": "SPECIAL",
            "document_type_id": DT_ID,
            "department_id": DEPT_ID,
            "year": 2025,
        }
        conn = _make_conn([od_row])
        conn.execute = AsyncMock(side_effect=["UPDATE 1", "UPDATE 1"])

        with patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)):
            await confirm_number(DOC_ID, RES_ID, schema_name=SCHEMA)

        assert conn.execute.call_count == 2
        counter_call = str(conn.execute.call_args_list[1])
        assert "active_reservation_document_id = NULL" in counter_call

    @pytest.mark.asyncio
    async def test_cancel_number_libera_carril_special(self):
        from shared.numbering import cancel_number

        od_row = {
            "id": DOC_ID,
            "numbering_regime": "SPECIAL",
            "document_type_id": DT_ID,
            "department_id": DEPT_ID,
            "year": 2025,
            "official_number": "TIPO-2025-0006-SMG-SEC",
        }
        conn = _make_conn([od_row])
        conn.execute = AsyncMock(side_effect=["UPDATE 1", "UPDATE 1"])

        with patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)), \
             patch("shared.alerts.send_alert_mail", AsyncMock()):
            await cancel_number(DOC_ID, schema_name=SCHEMA, reason="cancelado_por_test")

        assert conn.execute.call_count == 2
        counter_call = str(conn.execute.call_args_list[1])
        assert "active_reservation_document_id = NULL" in counter_call

    @pytest.mark.asyncio
    async def test_confirm_stale_lanza_stale_reservation_error(self):
        from shared.numbering import confirm_number

        od_row = {
            "numbering_regime": "GLOBAL",
            "document_type_id": DT_ID,
            "department_id": DEPT_ID,
            "year": 2025,
        }
        conn = _make_conn([od_row])
        conn.execute = AsyncMock(side_effect=["UPDATE 0"])

        with patch("shared.numbering.get_conn", return_value=_conn_ctx(conn)):
            with pytest.raises(StaleReservationError) as exc_info:
                await confirm_number(DOC_ID, RES_ID, schema_name=SCHEMA)

        assert exc_info.value.document_id == DOC_ID
        assert exc_info.value.reservation_id == RES_ID


class TestPollStaleDiscardsPdf:

    def _make_session(self):
        return {
            "session_id": "sess0001abc",
            "file_id": "file0001abc",
            "schema_name": SCHEMA,
            "user_id": "user0001abc",
            "document_id": DOC_ID,
            "is_numerator": True,
            "number": "TIPO-2025-0006-SMG-SEC",
            "status": "pending",
            "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "consumed_at": None,
            "provider_name": "autofirma",
            "user_cuit": "20123456789",
            "failure_reason": None,
            "reservation_id": RES_ID,
        }

    @pytest.mark.asyncio
    async def test_stale_reservation_llama_release_r2_fail(self):
        from services.documents.signing.providers import PollSigningSigned

        session = self._make_session()
        session_id = session["session_id"]

        poll_result = PollSigningSigned(
            cert_der=b"fake-cert-der",
            signed_pdf_bytes=b"%PDF-firma",
        )
        cert_mock = MagicMock(ok=True, cert_serial="SN001",
                              cert_subject_dn="CN=Test", cert_issuer_dn="CN=CA",
                              cert_subject_cuit="20123456789")

        mock_release = AsyncMock()
        mock_redis = MagicMock()
        mock_redis.delete = MagicMock()
        mock_mark = AsyncMock()

        _rtp_calls: list = []

        async def _smart_rtp(fn, *args, **kwargs):
            _rtp_calls.append(fn)
            if len(_rtp_calls) == 1:
                return poll_result
            if len(_rtp_calls) == 2:
                return cert_mock
            return fn(*args, **kwargs)

        with patch(
            "endpoints.digital_signature.poll.release_signing_lock_R2_fail",
            new=mock_release
        ), patch(
            "endpoints.digital_signature.poll.redis_client",
            new=mock_redis
        ), patch(
            "endpoints.digital_signature.poll._mark_session_status",
            new=mock_mark
        ), patch(
            "endpoints.digital_signature.poll._get_session",
            new=AsyncMock(return_value=session)
        ), patch(
            "endpoints.digital_signature.poll._mark_consumed",
            new=AsyncMock(return_value=True)
        ), patch(
            "endpoints.digital_signature.poll.run_in_threadpool",
            new=_smart_rtp
        ), patch(
            "endpoints.digital_signature.poll.call_notary_verify",
            new=AsyncMock(return_value={"ok": True})
        ), patch(
            "endpoints.digital_signature.poll.confirm_number",
            new=AsyncMock(side_effect=StaleReservationError(DOC_ID, RES_ID))
        ), patch(
            "endpoints.digital_signature.poll.log_signature_event",
            new=AsyncMock()
        ), patch(
            "endpoints.digital_signature.poll._poll_rate_limit_ok",
            new=MagicMock(return_value=True)
        ):
            from fastapi import Request as FastAPIRequest
            from endpoints.digital_signature.poll import poll_signing
            from models.schemas import AuthenticatedUser

            mock_request = MagicMock(spec=FastAPIRequest)
            mock_request.state.tenant_user_id = "user0001abc"
            mock_request.client.host = "127.0.0.1"
            mock_user = MagicMock(spec=AuthenticatedUser)
            mock_user.user_id = "user0001abc"

            response = await poll_signing(
                session_id=session_id,
                request=mock_request,
                current_user=mock_user,
                schema_name=SCHEMA,
            )

        mock_mark.assert_called_once_with(session_id, "failed", reason="stale_reservation")
        assert response == {"status": "failed", "failure_reason": "stale_reservation"}

        mock_release.assert_called_once_with(
            schema_name=SCHEMA,
            doc_id=DOC_ID,
        )

        mock_redis.delete.assert_called_once_with(
            f"firma:storage:{SCHEMA}:{session['file_id']}",
            f"firma:storage:{SCHEMA}:{session_id}",
            f"firma:storage:meta:{SCHEMA}:{session_id}",
        )

    @pytest.mark.asyncio
    async def test_stale_reservation_soft_fail_si_r2_lanza(self):
        from services.documents.signing.providers import PollSigningSigned

        session = self._make_session()
        session_id = session["session_id"]

        poll_result = PollSigningSigned(
            cert_der=b"fake-cert-der",
            signed_pdf_bytes=b"%PDF-firma",
        )
        cert_mock = MagicMock(ok=True, cert_serial="SN001",
                              cert_subject_dn="CN=Test", cert_issuer_dn="CN=CA",
                              cert_subject_cuit="20123456789")

        with patch(
            "endpoints.digital_signature.poll.release_signing_lock_R2_fail",
            new=AsyncMock(side_effect=RuntimeError("R2 timeout"))
        ), patch(
            "endpoints.digital_signature.poll.redis_client",
            new=MagicMock(delete=MagicMock())
        ), patch(
            "endpoints.digital_signature.poll._mark_session_status",
            new=AsyncMock()
        ), patch(
            "endpoints.digital_signature.poll._get_session",
            new=AsyncMock(return_value=session)
        ), patch(
            "endpoints.digital_signature.poll._mark_consumed",
            new=AsyncMock(return_value=True)
        ), patch(
            "endpoints.digital_signature.poll.run_in_threadpool",
            new=AsyncMock(side_effect=[poll_result, cert_mock, None])
        ), patch(
            "endpoints.digital_signature.poll.call_notary_verify",
            new=AsyncMock(return_value={"ok": True})
        ), patch(
            "endpoints.digital_signature.poll.confirm_number",
            new=AsyncMock(side_effect=StaleReservationError(DOC_ID, RES_ID))
        ), patch(
            "endpoints.digital_signature.poll.log_signature_event",
            new=AsyncMock()
        ), patch(
            "endpoints.digital_signature.poll._poll_rate_limit_ok",
            new=MagicMock(return_value=True)
        ):
            from fastapi import Request as FastAPIRequest
            from endpoints.digital_signature.poll import poll_signing
            from models.schemas import AuthenticatedUser

            mock_request = MagicMock(spec=FastAPIRequest)
            mock_request.state.tenant_user_id = "user0001abc"
            mock_request.client.host = "127.0.0.1"
            mock_user = MagicMock(spec=AuthenticatedUser)
            mock_user.user_id = "user0001abc"

            response = await poll_signing(
                session_id=session_id,
                request=mock_request,
                current_user=mock_user,
                schema_name=SCHEMA,
            )

        assert response == {"status": "failed", "failure_reason": "stale_reservation"}
