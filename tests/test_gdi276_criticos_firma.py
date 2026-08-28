import asyncio
import base64
import hashlib
import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


SCHEMA = "100_test"
DOC_ID = "aaaaaaaa-0000-0000-0000-000000000001"
USER_ID = "11111111-1111-1111-1111-111111111111"
RES_ID = "eeee0005-0000-0000-0000-000000000005"


def _session_row(**overrides):
    row = {
        "session_id": "SESABC123",
        "file_id": "DATA1",
        "schema_name": SCHEMA,
        "user_id": USER_ID,
        "document_id": DOC_ID,
        "is_numerator": True,
        "number": "CAEX-2025-00000001-SMG-ADGEN",
        "status": "pending",
        "expires_at": __import__("datetime").datetime(
            2099, 1, 1, tzinfo=__import__("datetime").timezone.utc
        ),
        "consumed_at": None,
        "provider_name": "firmador_gdi",
        "user_cuit": "20000000001",
        "failure_reason": None,
        "reservation_id": RES_ID,
        "created_at": __import__("datetime").datetime(
            2025, 1, 1, tzinfo=__import__("datetime").timezone.utc
        ),
    }
    row.update(overrides)
    return row


def _make_request(user_id):
    req = MagicMock()
    req.state.tenant_user_id = user_id
    return req


def _cert_ok():
    r = MagicMock()
    r.ok = True
    r.failure_reason = None
    r.cert_serial = "SERIAL"
    r.cert_subject_dn = "CN=Test"
    r.cert_issuer_dn = "CN=CA"
    r.cert_subject_cuit = "20000000001"
    r.cert_not_after = None
    r.revocation_status = "unknown"
    return r


class TestCritico4BindingSeEjecutaDesdeElEndpoint:

    @pytest.mark.asyncio
    async def test_poll_delega_en_firmador_gdi_y_verifica_binding(self):
        from endpoints.digital_signature import poll as poll_mod
        from services.documents.signing.providers import (
            PollSigningSigned, firmador_gdi as fg_mod,
        )

        session = _session_row()
        signed_pdf = b"%PDF-1.7\nfirma-de-token"
        cert_der = b"\x30\x82"

        async def _run(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        redis_mock = MagicMock()
        raw = base64.urlsafe_b64encode(cert_der + signed_pdf).rstrip(b"=").decode()
        redis_mock.get.return_value = raw
        redis_mock.delete.return_value = 1
        redis_mock.setex.return_value = True

        binding_spy = MagicMock()

        with patch.object(poll_mod, "_get_session", AsyncMock(return_value=session)), \
             patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True), \
             patch.object(poll_mod, "run_in_threadpool", AsyncMock(side_effect=_run)), \
             patch.object(poll_mod, "redis_client", redis_mock), \
             patch.object(fg_mod, "redis_client", redis_mock), \
             patch.object(fg_mod.FirmadorGDIProvider, "_verificar_binding", binding_spy), \
             patch.object(poll_mod, "validate_cert_full", return_value=_cert_ok()), \
             patch.object(poll_mod, "call_notary_verify", AsyncMock(return_value={"ok": True, "tsa_url": None, "tsa_time": None})), \
             patch.object(poll_mod, "release_signing_lock_R2_fail", AsyncMock()), \
             patch.object(poll_mod, "confirm_number", AsyncMock()), \
             patch.object(poll_mod, "_mark_consumed", AsyncMock(return_value=True)), \
             patch.object(poll_mod, "_mark_session_status", AsyncMock(return_value=True)), \
             patch.object(poll_mod, "guardar_pdf_firmado", AsyncMock(return_value="k")), \
             patch.object(poll_mod, "encolar_cierre_digital", AsyncMock(return_value="c")), \
             patch.object(poll_mod, "marcar_sesion_completing", AsyncMock(return_value=True)), \
             patch.object(poll_mod, "log_signature_event", AsyncMock()):

            result = await poll_mod.poll_signing(
                session_id="SESABC123",
                request=_make_request(USER_ID),
                current_user=MagicMock(user_id=USER_ID),
                schema_name=SCHEMA,
            )

        assert result["status"] == "completing"
        assert binding_spy.call_count == 1, (
            "El endpoint NO ejecutó _verificar_binding — sigue usando el provider "
            "muerto (AutoFirmaProvider). Cablear FirmadorGDIProvider."
        )
        args, _ = binding_spy.call_args
        assert "SESABC123" in args

    @pytest.mark.asyncio
    async def test_binding_detecta_pdf_sustituido(self):
        from services.documents.signing.providers.firmador_gdi import FirmadorGDIProvider
        from services.documents.signing.providers import firmador_gdi as fg_mod
        from shared.exceptions import ValidationError

        original = b"%PDF-1.7\ncontenido-original-A"
        firmado_falso = b"%PDF-1.7\ncontenido-alterado-Z"
        assert len(firmado_falso) == len(original)
        assert firmado_falso != original

        meta = json.dumps({
            "unsigned_sha256": hashlib.sha256(original).hexdigest(),
            "unsigned_len": len(original),
        })
        redis_mock = MagicMock()
        redis_mock.get.return_value = meta

        with patch.object(fg_mod, "redis_client", redis_mock):
            with pytest.raises(ValidationError):
                FirmadorGDIProvider._verificar_binding(
                    "SESABC123", SCHEMA, firmado_falso
                )


class TestCritico2FalloNoStaleNoDiceSigned:

    @pytest.mark.asyncio
    async def test_confirm_number_falla_no_stale_devuelve_failed_y_cancela_numero(self):
        from endpoints.digital_signature import poll as poll_mod
        from services.documents.signing.providers import PollSigningSigned

        session = _session_row()

        async def _run(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider_instance = MagicMock()
        provider_instance.poll_signing = MagicMock(
            return_value=PollSigningSigned(signed_pdf_bytes=b"%PDF-1.7\nx", cert_der=b"\x30\x82")
        )

        cancel_number_mock = AsyncMock()
        mark_failed_mock = AsyncMock(return_value=True)

        with patch.object(poll_mod, "_get_session", AsyncMock(return_value=session)), \
             patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True), \
             patch.object(poll_mod, "run_in_threadpool", AsyncMock(side_effect=_run)), \
             patch.object(poll_mod, "FirmadorGDIProvider", return_value=provider_instance), \
             patch.object(poll_mod, "validate_cert_full", return_value=_cert_ok()), \
             patch.object(poll_mod, "call_notary_verify", AsyncMock(return_value={"ok": True})), \
             patch.object(poll_mod, "_mark_consumed", AsyncMock(return_value=True)), \
             patch.object(poll_mod, "_mark_session_status", mark_failed_mock), \
             patch.object(poll_mod, "release_signing_lock_R2_fail", AsyncMock()), \
             patch.object(poll_mod, "log_signature_event", AsyncMock()), \
             patch.object(poll_mod, "cancel_number", cancel_number_mock), \
             patch.object(poll_mod, "redis_client", MagicMock()), \
             patch.object(poll_mod, "confirm_number", AsyncMock(side_effect=RuntimeError("pool timeout"))):

            result = await poll_mod.poll_signing(
                session_id=session["session_id"],
                request=_make_request(USER_ID),
                current_user=MagicMock(user_id=USER_ID),
                schema_name=SCHEMA,
            )

        assert result["status"] == "failed", result
        assert result.get("failure_reason") == "cas_confirm_failure"
        cancel_number_mock.assert_awaited_once()
        mark_failed_mock.assert_awaited()
        _, kwargs = mark_failed_mock.await_args
        assert "failed" in mark_failed_mock.await_args.args or kwargs.get("status") == "failed"

    @pytest.mark.asyncio
    async def test_segundo_poll_no_afirma_signed_por_inferencia(self):
        from endpoints.digital_signature import poll as poll_mod
        from services.documents.signing.providers import PollSigningSigned

        session = _session_row(
            consumed_at=__import__("datetime").datetime(
                2025, 1, 1, tzinfo=__import__("datetime").timezone.utc
            ),
        )

        async def _run(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider_instance = MagicMock()
        provider_instance.poll_signing = MagicMock(
            return_value=PollSigningSigned(signed_pdf_bytes=b"%PDF-1.7\nx", cert_der=b"\x30\x82")
        )

        fetch_one_mock = AsyncMock(return_value={
            "status": "failed", "failure_reason": "cas_confirm_failure",
        })

        with patch.object(poll_mod, "_get_session", AsyncMock(return_value=session)), \
             patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True), \
             patch.object(poll_mod, "run_in_threadpool", AsyncMock(side_effect=_run)), \
             patch.object(poll_mod, "FirmadorGDIProvider", return_value=provider_instance), \
             patch.object(poll_mod, "validate_cert_full", return_value=_cert_ok()), \
             patch.object(poll_mod, "call_notary_verify", AsyncMock(return_value={"ok": True})), \
             patch.object(poll_mod, "_mark_consumed", AsyncMock(return_value=False)), \
             patch.object(poll_mod, "fetch_one", fetch_one_mock):

            result = await poll_mod.poll_signing(
                session_id=session["session_id"],
                request=_make_request(USER_ID),
                current_user=MagicMock(user_id=USER_ID),
                schema_name=SCHEMA,
            )

        assert result["status"] == "failed", (
            f"La rama anti-replay respondió {result!r}. Antes del fix devolvía "
            "'signed' por inferencia — ese era el bug de fondo del CRÍTICO 2."
        )
        assert result.get("failure_reason") == "cas_confirm_failure"

    @pytest.mark.asyncio
    async def test_cancelled_error_hace_cleanup_y_relanza(self):
        from endpoints.digital_signature import poll as poll_mod
        from services.documents.signing.providers import PollSigningSigned

        session = _session_row()

        async def _run(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider_instance = MagicMock()
        provider_instance.poll_signing = MagicMock(
            return_value=PollSigningSigned(signed_pdf_bytes=b"%PDF-1.7\nx", cert_der=b"\x30\x82")
        )

        cleanup_spy = AsyncMock()

        with patch.object(poll_mod, "_get_session", AsyncMock(return_value=session)), \
             patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True), \
             patch.object(poll_mod, "run_in_threadpool", AsyncMock(side_effect=_run)), \
             patch.object(poll_mod, "FirmadorGDIProvider", return_value=provider_instance), \
             patch.object(poll_mod, "validate_cert_full", return_value=_cert_ok()), \
             patch.object(poll_mod, "call_notary_verify", AsyncMock(return_value={"ok": True})), \
             patch.object(poll_mod, "_mark_consumed", AsyncMock(return_value=True)), \
             patch.object(poll_mod, "_cleanup_after_consume_failure", cleanup_spy), \
             patch.object(poll_mod, "cancel_number", AsyncMock()), \
             patch.object(poll_mod, "release_signing_lock_R2_fail", AsyncMock()), \
             patch.object(poll_mod, "redis_client", MagicMock()), \
             patch.object(poll_mod, "confirm_number", AsyncMock(side_effect=asyncio.CancelledError())):

            with pytest.raises(asyncio.CancelledError):
                await poll_mod.poll_signing(
                    session_id=session["session_id"],
                    request=_make_request(USER_ID),
                    current_user=MagicMock(user_id=USER_ID),
                    schema_name=SCHEMA,
                )

        cleanup_spy.assert_awaited_once()
        _, kwargs = cleanup_spy.await_args
        assert kwargs.get("reason") == "cas_confirm_cancelled"

    @pytest.mark.asyncio
    async def test_segundo_poll_signed_real_sigue_devolviendo_signed(self):
        from endpoints.digital_signature import poll as poll_mod
        from services.documents.signing.providers import PollSigningSigned

        session = _session_row(
            consumed_at=__import__("datetime").datetime(
                2025, 1, 1, tzinfo=__import__("datetime").timezone.utc
            ),
        )

        async def _run(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider_instance = MagicMock()
        provider_instance.poll_signing = MagicMock(
            return_value=PollSigningSigned(signed_pdf_bytes=b"%PDF-1.7\nx", cert_der=b"\x30\x82")
        )

        fetch_one_mock = AsyncMock(return_value={"status": "signed", "failure_reason": None})

        with patch.object(poll_mod, "_get_session", AsyncMock(return_value=session)), \
             patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True), \
             patch.object(poll_mod, "run_in_threadpool", AsyncMock(side_effect=_run)), \
             patch.object(poll_mod, "FirmadorGDIProvider", return_value=provider_instance), \
             patch.object(poll_mod, "validate_cert_full", return_value=_cert_ok()), \
             patch.object(poll_mod, "call_notary_verify", AsyncMock(return_value={"ok": True})), \
             patch.object(poll_mod, "_mark_consumed", AsyncMock(return_value=False)), \
             patch.object(poll_mod, "fetch_one", fetch_one_mock), \
             patch.object(poll_mod, "_rebuild_auto_link_results", AsyncMock(return_value=[])):

            result = await poll_mod.poll_signing(
                session_id=session["session_id"],
                request=_make_request(USER_ID),
                current_user=MagicMock(user_id=USER_ID),
                schema_name=SCHEMA,
            )

        assert result["status"] == "signed"
        assert result["official_number"] == session["number"]
