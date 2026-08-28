
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

SCHEMA  = "100_test"
DEPT_ID = str(uuid4())


def _make_cancelled(global_seq: int = 5, year: int = 2025) -> dict:
    return {
        "id":              str(uuid4()),
        "global_sequence": global_seq,
        "official_number": f"SEC-{year}-{global_seq:04d}-OF-ABC",
        "department_id":   DEPT_ID,
        "year":            year,
        "reservation_id":  str(uuid4()),
    }


def _tenant_data_mock() -> dict:
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


def _make_conn_mock(deleted: str = "DELETE 1") -> tuple:
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=[None, None, deleted, None])
    mock_conn.transaction = MagicMock()
    mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value.__aexit__  = AsyncMock(return_value=False)

    mock_get_conn = MagicMock()
    mock_get_conn.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_get_conn.return_value.__aexit__  = AsyncMock(return_value=False)
    return mock_conn, mock_get_conn


@pytest.mark.asyncio
async def test_gap_detectado_y_tst_creado():
    from services.documents.creation.tst_creator import create_tst_document_signed_by_system

    cancelled = _make_cancelled(global_seq=7)
    _, mock_gc = _make_conn_mock(deleted="DELETE 1")

    with (
        patch(
            "services.documents.creation.tst_creator._resolve_tenant_data",
            AsyncMock(return_value=_tenant_data_mock()),
        ),
        patch("services.documents.creation.tst_creator.get_conn", mock_gc),
        patch(
            "services.documents.creation.tst_creator.call_pdfcomposer_preview_pdf",
            AsyncMock(return_value=b"%PDF minimal"),
        ),
        patch(
            "services.documents.creation.tst_creator.call_notary_sign_pdf",
            AsyncMock(return_value=b"%PDF signed"),
        ),
        patch(
            "services.documents.creation.tst_creator.execute",
            AsyncMock(return_value="UPDATE 1"),
        ),
        patch(
            "services.storage.cloudflare.get_tenant_r2_client",
            AsyncMock(return_value=MagicMock(upload_oficial=MagicMock())),
        ),
        patch(
            "services.documents.creation.tst_creator.run_in_threadpool",
            AsyncMock(return_value=None),
        ),
        patch(
            "services.documents.creation.tst_creator.log_signature_event",
            AsyncMock(),
        ),
    ):
        number = await create_tst_document_signed_by_system(
            cancelled,
            schema_name=SCHEMA,
        )

    assert "TST" in number
    assert f"-{cancelled['global_sequence']:04d}-" in number
    assert "SEC" in number


@pytest.mark.asyncio
async def test_max_tst_per_run_respetado():
    import jobs.fill_number_gaps_tst as sweep_mod
    from config.constants import MAX_TST_PER_RUN

    expected_gaps = [_make_cancelled(global_seq=i) for i in range(1, MAX_TST_PER_RUN + 1)]
    created: list[str] = []

    async def fake_creator(row, *, schema_name):
        num = f"SEC-2025-{row['global_sequence']:04d}-TST-SIS"
        created.append(num)
        return num

    with (
        patch.object(sweep_mod, "_find_gaps", AsyncMock(return_value=expected_gaps)),
        patch(
            "services.documents.creation.tst_creator.create_tst_document_signed_by_system",
            side_effect=fake_creator,
        ),
        patch(
            "services.shared.notary_breaker.breaker_status",
            AsyncMock(return_value={"state": "CLOSED"}),
        ),
        patch("jobs.fill_number_gaps_tst.asyncio.sleep", AsyncMock()),
    ):
        result = await sweep_mod._sweep_tenant(SCHEMA)

    assert result["tst_created"] == MAX_TST_PER_RUN
    assert result["gaps_found"]  == MAX_TST_PER_RUN
    assert result["tst_errors"]  == 0


@pytest.mark.asyncio
async def test_fallo_notary_deja_cancelled_para_reintento():
    from services.documents.creation.tst_creator import create_tst_document_signed_by_system

    class _NotaryDown(Exception):
        pass

    cancelled = _make_cancelled(global_seq=3)
    _, mock_gc = _make_conn_mock(deleted="DELETE 1")

    execute_sql_calls: list[str] = []

    async def _track_execute(sql, *args, **kwargs):
        execute_sql_calls.append(sql)
        return "UPDATE 1"

    with (
        patch(
            "services.documents.creation.tst_creator._resolve_tenant_data",
            AsyncMock(return_value=_tenant_data_mock()),
        ),
        patch("services.documents.creation.tst_creator.get_conn", mock_gc),
        patch(
            "services.documents.creation.tst_creator.call_pdfcomposer_preview_pdf",
            AsyncMock(return_value=b"%PDF minimal"),
        ),
        patch(
            "services.documents.creation.tst_creator.call_notary_sign_pdf",
            AsyncMock(side_effect=_NotaryDown("Notary timeout")),
        ),
        patch("services.documents.creation.tst_creator.execute", side_effect=_track_execute),
        patch("services.documents.creation.tst_creator.log_signature_event", AsyncMock()),
    ):
        with pytest.raises(_NotaryDown):
            await create_tst_document_signed_by_system(cancelled, schema_name=SCHEMA)

    mock_gc.assert_called_once()

    revert_calls = [s for s in execute_sql_calls if "CANCELLED" in s]
    assert revert_calls, (
        "Se esperaba un execute con 'CANCELLED' para revertir la fila RESERVED. "
        f"SQL calls: {execute_sql_calls}"
    )


@pytest.mark.asyncio
async def test_corrida_sin_huecos_alerta_igual():
    import jobs.fill_number_gaps_tst as sweep_mod

    with (
        patch(
            "shared.tenant_validation.get_valid_schemas",
            AsyncMock(return_value=[SCHEMA, "public"]),
        ),
        patch.object(sweep_mod, "_find_gaps", AsyncMock(return_value=[])),
        patch(
            "services.shared.notary_breaker.breaker_status",
            AsyncMock(return_value={"state": "CLOSED"}),
        ),
        patch("shared.alerts.send_alert_mail", AsyncMock()) as mock_mail,
    ):
        await sweep_mod._run_sweep()

    mock_mail.assert_called_once()
    kwargs = mock_mail.call_args.kwargs
    subject = kwargs.get("subject", "")
    assert "SIN HUECOS" in subject or "OK" in subject


@pytest.mark.asyncio
async def test_breaker_open_no_crea_pero_alerta():
    import jobs.fill_number_gaps_tst as sweep_mod

    gaps = [_make_cancelled(global_seq=i) for i in range(1, 4)]

    with (
        patch(
            "shared.tenant_validation.get_valid_schemas",
            AsyncMock(return_value=[SCHEMA, "public"]),
        ),
        patch.object(sweep_mod, "_find_gaps", AsyncMock(return_value=gaps)),
        patch(
            "services.shared.notary_breaker.breaker_status",
            AsyncMock(return_value={"state": "OPEN"}),
        ),
        patch(
            "services.documents.creation.tst_creator.create_tst_document_signed_by_system",
            AsyncMock(),
        ) as mock_creator,
        patch("shared.alerts.send_alert_mail", AsyncMock()) as mock_mail,
    ):
        await sweep_mod._run_sweep()

    mock_creator.assert_not_called()

    mock_mail.assert_called_once()
    kwargs = mock_mail.call_args.kwargs
    subject = kwargs.get("subject", "")
    body    = kwargs.get("body", "")
    assert "NOTARY" in subject.upper() or "NOTARY" in body.upper()
