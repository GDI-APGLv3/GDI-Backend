
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

SCHEMA   = "100_test"
DOC_ID   = str(uuid4())
DEPT_ID  = str(uuid4())


def _make_cancelled(global_seq: int = 5) -> dict:
    return {
        "id":              str(uuid4()),
        "global_sequence": global_seq,
        "official_number": f"SEC-2025-{global_seq:04d}-OF-ABC",
        "department_id":   DEPT_ID,
        "year":            2025,
        "reservation_id":  str(uuid4()),
    }


def _tenant_data() -> dict:
    return {
        "tst_type_id":   1,
        "tst_type_name": "Documento de Prueba",
        "signer_name":   "Sistema TEST",
        "signer_seal":   "SIS",
        "dept_acronym":  "SEC",
        "dept_name":     "Secretaría",
        "municipality":  "Municipalidad del Futuro",
        "city":          "LATAM",
    }


def _make_rejection_doc(status: str = "sent_to_sign") -> dict:
    return {
        "id":         DOC_ID,
        "status":     status,
        "created_by": str(uuid4()),
    }


@pytest.mark.asyncio
async def test_reject_expires_pending_sessions():
    from services.documents.lifecycle.rejection import reject_document

    doc = _make_rejection_doc()

    with (
        patch("services.documents.lifecycle.rejection.validate_document_id",  AsyncMock()),
        patch("services.documents.lifecycle.rejection.validate_user_id",       AsyncMock()),
        patch("services.documents.lifecycle.rejection.validate_rejection_reason"),
        patch("services.documents.lifecycle.rejection.fetch_one",
              AsyncMock(side_effect=[doc, None])),
        patch("services.documents.lifecycle.rejection.fetch_val", AsyncMock(return_value=0)),
        patch("services.documents.lifecycle.rejection._check_user_permissions",
              AsyncMock(return_value={"allowed": True, "reason": "creator"})),
        patch("services.documents.lifecycle.rejection._cleanup_pdf_from_r2",
              AsyncMock(return_value={"attempted": False, "success": False, "error": None, "note": ""})),
        patch("services.documents.lifecycle.rejection.transaction") as mock_tx,
        patch("services.documents.lifecycle.rejection.execute", AsyncMock(return_value="UPDATE 1")) as mock_exec,
        patch("services.documents.lifecycle.rejection.fetch_one", AsyncMock(return_value=None)),
        patch("services.documents.signing.r2_lock.delete_inprocess_on_reject", AsyncMock()),
    ):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=None)
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_tx.return_value.__aexit__  = AsyncMock(return_value=False)

        with patch("services.documents.lifecycle.rejection.fetch_one",
                   AsyncMock(side_effect=[doc, None, None])):
            await reject_document(DOC_ID, str(uuid4()), "test reason", schema_name=SCHEMA)

    exec_calls = mock_exec.call_args_list
    session_update = any(
        "signing_sessions" in str(c) and "expired" in str(c)
        for c in exec_calls
    )
    assert session_update, (
        "Se esperaba un UPDATE de signing_sessions→expired en las llamadas a execute. "
        f"Calls: {exec_calls}"
    )


@pytest.mark.asyncio
async def test_reject_cancels_reserved_number():
    from services.documents.lifecycle.rejection import _cancel_reserved_number_if_exists

    reserved_row = {"id": DOC_ID}

    with (
        patch("services.documents.lifecycle.rejection.fetch_one",
              AsyncMock(return_value=reserved_row)),
        patch("shared.numbering.cancel_number", AsyncMock()) as mock_cancel,
    ):
        with patch("services.documents.lifecycle.rejection.fetch_one",
                   AsyncMock(return_value=reserved_row)):
            from services.documents.lifecycle import rejection as rej_mod
            with patch.object(rej_mod, "fetch_one", AsyncMock(return_value=reserved_row)):
                with patch("shared.numbering.cancel_number", AsyncMock()) as mock_cn:
                    await _cancel_reserved_number_if_exists(DOC_ID, schema_name=SCHEMA)

    mock_cn.assert_called_once_with(
        DOC_ID,
        schema_name=SCHEMA,
        reason="document_rejected",
    )


@pytest.mark.asyncio
async def test_reject_no_cancel_if_confirmed():
    from services.documents.lifecycle import rejection as rej_mod

    with (
        patch.object(rej_mod, "fetch_one", AsyncMock(return_value=None)),
        patch("shared.numbering.cancel_number", AsyncMock()) as mock_cn,
    ):
        await rej_mod._cancel_reserved_number_if_exists(DOC_ID, schema_name=SCHEMA)

    mock_cn.assert_not_called()


@pytest.mark.asyncio
async def test_reject_soft_fail_session_expire():
    from services.documents.lifecycle import rejection as rej_mod

    with patch.object(rej_mod, "execute", AsyncMock(side_effect=Exception("BD error"))):
        await rej_mod._expire_pending_signing_sessions(DOC_ID, schema_name=SCHEMA)


def _make_claim_conn(deleted: str = "DELETE 1") -> tuple:
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=[None, None, deleted, None])
    mock_conn.transaction = MagicMock()
    mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value.__aexit__  = AsyncMock(return_value=False)

    mock_gc = MagicMock()
    mock_gc.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_gc.return_value.__aexit__  = AsyncMock(return_value=False)
    return mock_conn, mock_gc


@pytest.mark.asyncio
async def test_tst_claim_before_notary():
    from services.documents.creation.tst_creator import create_tst_document_signed_by_system

    cancelled = _make_cancelled(global_seq=9)
    call_order: list[str] = []

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=[None, None, "DELETE 1", None])
    mock_conn.transaction = MagicMock()
    mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value.__aexit__  = AsyncMock(return_value=False)

    class _FakeConnCM:
        async def __aenter__(self):
            call_order.append("get_conn")
            return mock_conn
        async def __aexit__(self, *a):
            return False

    def _fake_get_conn(**kwargs):
        return _FakeConnCM()

    async def _fake_pdf(*args, **kwargs):
        call_order.append("pdfcomposer")
        return b"%PDF"

    async def _fake_notary(**kwargs):
        call_order.append("notary")
        return b"%PDF signed"

    with (
        patch("services.documents.creation.tst_creator._resolve_tenant_data",
              AsyncMock(return_value=_tenant_data())),
        patch("services.documents.creation.tst_creator.get_conn",
              side_effect=_fake_get_conn),
        patch("services.documents.creation.tst_creator.call_pdfcomposer_preview_pdf",
              side_effect=_fake_pdf),
        patch("services.documents.creation.tst_creator.call_notary_sign_pdf",
              side_effect=_fake_notary),
        patch("services.documents.creation.tst_creator.execute",
              AsyncMock(return_value="UPDATE 1")),
        patch("services.storage.cloudflare.get_tenant_r2_client",
              AsyncMock(return_value=MagicMock(upload_oficial=MagicMock()))),
        patch("services.documents.creation.tst_creator.run_in_threadpool",
              AsyncMock(return_value=None)),
        patch("services.documents.creation.tst_creator.log_signature_event", AsyncMock()),
    ):
        await create_tst_document_signed_by_system(cancelled, schema_name=SCHEMA)

    assert "get_conn"    in call_order, f"get_conn no fue llamado. Orden: {call_order}"
    assert "pdfcomposer" in call_order, f"pdfcomposer no fue llamado. Orden: {call_order}"
    assert "notary"      in call_order, f"notary no fue llamado. Orden: {call_order}"
    assert call_order.index("get_conn") < call_order.index("pdfcomposer"), (
        f"get_conn debe preceder a pdfcomposer. Orden: {call_order}"
    )
    assert call_order.index("get_conn") < call_order.index("notary"), (
        f"get_conn debe preceder a notary. Orden: {call_order}"
    )


@pytest.mark.asyncio
async def test_tst_notary_failure_reverts_to_cancelled():
    from services.documents.creation.tst_creator import create_tst_document_signed_by_system

    class _NotaryDown(Exception):
        pass

    cancelled = _make_cancelled(global_seq=3)
    _, mock_gc = _make_claim_conn(deleted="DELETE 1")

    execute_calls: list[str] = []

    async def _fake_execute(sql, *args, **kwargs):
        execute_calls.append(sql)
        return "UPDATE 1"

    with (
        patch("services.documents.creation.tst_creator._resolve_tenant_data",
              AsyncMock(return_value=_tenant_data())),
        patch("services.documents.creation.tst_creator.get_conn", mock_gc),
        patch("services.documents.creation.tst_creator.call_pdfcomposer_preview_pdf",
              AsyncMock(return_value=b"%PDF")),
        patch("services.documents.creation.tst_creator.call_notary_sign_pdf",
              AsyncMock(side_effect=_NotaryDown("Notary timeout"))),
        patch("services.documents.creation.tst_creator.execute",
              side_effect=_fake_execute),
        patch("services.documents.creation.tst_creator.log_signature_event", AsyncMock()),
    ):
        with pytest.raises(_NotaryDown):
            await create_tst_document_signed_by_system(cancelled, schema_name=SCHEMA)

    revert_calls = [s for s in execute_calls if "CANCELLED" in s]
    assert revert_calls, (
        "Se esperaba un UPDATE SET reservation_status='CANCELLED' en execute. "
        f"Calls SQL: {execute_calls}"
    )


@pytest.mark.asyncio
async def test_tst_recycler_wins_first():
    from services.documents.creation.tst_creator import create_tst_document_signed_by_system

    cancelled = _make_cancelled(global_seq=2)
    _, mock_gc = _make_claim_conn(deleted="DELETE 0")

    with (
        patch("services.documents.creation.tst_creator._resolve_tenant_data",
              AsyncMock(return_value=_tenant_data())),
        patch("services.documents.creation.tst_creator.get_conn", mock_gc),
        patch("services.documents.creation.tst_creator.call_notary_sign_pdf",
              AsyncMock()) as mock_notary,
        patch("services.documents.creation.tst_creator.call_pdfcomposer_preview_pdf",
              AsyncMock()) as mock_pdf,
        patch("services.documents.creation.tst_creator.log_signature_event", AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="ya no existe"):
            await create_tst_document_signed_by_system(cancelled, schema_name=SCHEMA)

    mock_notary.assert_not_called()
    mock_pdf.assert_not_called()
