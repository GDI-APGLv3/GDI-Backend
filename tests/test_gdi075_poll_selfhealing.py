
import uuid

import pytest

from endpoints.digital_signature import poll_async as pa_mod
from services.documents.signing import async_poll_status as aps_mod
from workers import escri as escri_mod


class _FakeUser:
    user_id = "a1000000-0000-0000-0000-000000000100"


def _session_row(status: str) -> dict:
    return {
        "session_id": "11111111-2222-3333-4444-555555555555",
        "schema_name": "100_test",
        "document_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "user_id": _FakeUser.user_id,
        "status": status,
        "failure_reason": None,
        "payload": {},
    }


def _make_poll_mocks(monkeypatch, session_status: str, doc_row):
    executed: list[str] = []

    async def fake_fetch_one(sql, *params, schema_name):
        if "signing_sessions" in sql:
            return _session_row(session_status)
        if "document_draft" in sql:
            assert schema_name == "100_test", "el doc debe leerse en el schema de la sesión"
            return doc_row
        raise AssertionError(f"query inesperada: {sql}")

    async def fake_execute(sql, *params, schema_name, **kwargs):
        executed.append(sql)
        return "UPDATE 1"

    monkeypatch.setattr(aps_mod, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(aps_mod, "execute", fake_execute)
    monkeypatch.setattr(pa_mod, "_poll_rate_limit_ok", lambda user, session: True)
    return executed


class TestPollSelfHealing:
    async def test_sesion_processing_con_doc_firmado_responde_signed(self, monkeypatch):
        executed = _make_poll_mocks(
            monkeypatch,
            "processing",
            {"status": "signed", "document_number": "AINSP-2026-0000001-MDEV-INTE344"},
        )
        resp = await pa_mod.poll_async_signing(
            session_id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
            current_user=_FakeUser(),
            schema_name="100_test",
        )
        assert resp.status == "signed"
        assert resp.official_number == "AINSP-2026-0000001-MDEV-INTE344"
        assert any("signing_sessions" in sql and "'signed'" in sql for sql in executed)

    async def test_sesion_pending_con_doc_firmado_responde_signed(self, monkeypatch):
        _make_poll_mocks(
            monkeypatch,
            "pending",
            {"status": "signed", "document_number": "AINSP-2026-0000002-MDEV-INTE344"},
        )
        resp = await pa_mod.poll_async_signing(
            session_id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
            current_user=_FakeUser(),
            schema_name="100_test",
        )
        assert resp.status == "signed"
        assert resp.official_number == "AINSP-2026-0000002-MDEV-INTE344"

    async def test_doc_todavia_en_firma_sigue_processing(self, monkeypatch):
        executed = _make_poll_mocks(
            monkeypatch,
            "processing",
            {"status": "sent_to_sign", "document_number": None},
        )
        resp = await pa_mod.poll_async_signing(
            session_id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
            current_user=_FakeUser(),
            schema_name="100_test",
        )
        assert resp.status == "processing"
        assert resp.official_number is None
        assert executed == [], "no debe reconciliar nada si el doc no está firmado"

    async def test_sesion_signed_no_consulta_documento(self, monkeypatch):
        row = _session_row("signed")
        row["payload"] = {"official_number": "AINSP-2026-0000003-MDEV-INTE344"}

        async def fake_fetch_one(sql, *params, schema_name):
            assert "signing_sessions" in sql, "con sesión signed no debe tocar document_draft"
            return row

        monkeypatch.setattr(aps_mod, "fetch_one", fake_fetch_one)
        monkeypatch.setattr(pa_mod, "_poll_rate_limit_ok", lambda user, session: True)
        resp = await pa_mod.poll_async_signing(
            session_id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
            current_user=_FakeUser(),
            schema_name="100_test",
        )
        assert resp.status == "signed"
        assert resp.official_number == "AINSP-2026-0000003-MDEV-INTE344"


class TestMarkSessionSignedRecovery:
    async def test_cas_0_filas_reintenta_con_fence(self, monkeypatch):
        calls: list[str] = []

        async def fake_execute(sql, *params, schema_name, **kwargs):
            calls.append(sql)
            return "UPDATE 0" if len(calls) == 1 else "UPDATE 1"

        monkeypatch.setattr(escri_mod, "execute", fake_execute)
        worker = escri_mod.EscriWorker()
        await worker._mark_session_signed("11111111-2222-3333-4444-555555555555", "NUM-1")

        assert len(calls) == 2
        assert "claimed_by = $3" in calls[0] and "status     = 'processing'" in calls[0]
        assert "status = 'pending'" in calls[1]
        assert "claimed_by IS NULL OR claimed_by = $3" in calls[1]

    async def test_cas_ok_no_reintenta(self, monkeypatch):
        calls: list[str] = []

        async def fake_execute(sql, *params, schema_name, **kwargs):
            calls.append(sql)
            return "UPDATE 1"

        monkeypatch.setattr(escri_mod, "execute", fake_execute)
        worker = escri_mod.EscriWorker()
        await worker._mark_session_signed("11111111-2222-3333-4444-555555555555", "NUM-2")

        assert len(calls) == 1
