
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestEscriMailsNoExcDetail:

    def _bodies(self):
        import inspect, re
        from workers.escri import EscriWorker

        source = inspect.getsource(EscriWorker._process_job)
        return [source[m.start():m.start() + 800]
                for m in re.finditer(r"send_alert_mail\(", source)]

    def test_ningun_mail_interpola_una_excepcion_cruda(self):
        for fragment in self._bodies():
            for prohibido in ("{_dts_err}", "{exc}", "{_marker_err}", "{_e}", "{err}"):
                assert prohibido not in fragment, f"{prohibido} interpolado en un mail"

    def test_los_mails_llevan_correlation_id(self):
        bodies = self._bodies()
        assert bodies, "no quedó ningún send_alert_mail en _process_job"
        for fragment in bodies:
            assert "correlationId" in fragment or "session_id" in fragment


class TestPollSigningFailedDiscreteCode:

    @pytest.mark.asyncio
    async def test_provider_failed_usa_error_code_no_error_message(self):
        from services.documents.signing.providers import PollSigningFailed
        from endpoints.digital_signature.poll import poll_signing

        SCHEMA = "100_test"
        SESSION_ID = "sess0001abc"

        session = {
            "session_id": SESSION_ID,
            "file_id": "file0001abc",
            "schema_name": SCHEMA,
            "user_id": "user0001abc",
            "document_id": str(uuid.uuid4()),
            "is_numerator": False,
            "number": None,
            "status": "pending",
            "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "consumed_at": None,
            "provider_name": "autofirma",
            "user_cuit": None,
            "failure_reason": None,
            "reservation_id": str(uuid.uuid4()),
        }

        failed_result = PollSigningFailed(
            error_code="BASE64_DECODE_FAIL",
            error_message="Traceback: CUIT=20123456789 — decode error xyz",
        )

        mock_mark = AsyncMock()
        from fastapi import Request as FastAPIRequest
        from models.schemas import AuthenticatedUser

        mock_request = MagicMock(spec=FastAPIRequest)
        mock_request.state.tenant_user_id = "user0001abc"
        mock_request.client.host = "127.0.0.1"
        mock_user = MagicMock(spec=AuthenticatedUser)
        mock_user.user_id = "user0001abc"

        with patch("endpoints.digital_signature.poll._get_session",
                   new=AsyncMock(return_value=session)), \
             patch("endpoints.digital_signature.poll._mark_consumed",
                   new=AsyncMock(return_value=True)), \
             patch("endpoints.digital_signature.poll.run_in_threadpool",
                   new=AsyncMock(return_value=failed_result)), \
             patch("endpoints.digital_signature.poll._mark_session_status",
                   new=mock_mark), \
             patch("endpoints.digital_signature.poll.release_signing_lock_R2_fail",
                   new=AsyncMock()), \
             patch("endpoints.digital_signature.poll._poll_rate_limit_ok",
                   new=MagicMock(return_value=True)):
            response = await poll_signing(
                session_id=SESSION_ID,
                request=mock_request,
                current_user=mock_user,
                schema_name=SCHEMA,
            )

        assert response["failure_reason"] == "base64_decode_fail"
        mock_mark.assert_awaited_once_with(SESSION_ID, "failed", "base64_decode_fail")
        assert "CUIT" not in str(response)
        assert "decode error xyz" not in str(response)


class TestCertFailureReasonNormalization:

    def _cert_code_from_source(self, raw_reason: str) -> str:
        code = raw_reason or "cert_unknown"
        if code.startswith("cert_parse_error"):
            return "cert_parse_error"
        if code.startswith("cert_field_error"):
            return "cert_field_error"
        return code

    def test_cert_parse_error_con_exc_truncado(self):
        raw = "cert_parse_error: ValueError('invalid DER — serialNumber=CUIT 20-12345678-9')"
        assert self._cert_code_from_source(raw) == "cert_parse_error"

    def test_cert_field_error_con_exc_truncado(self):
        raw = "cert_field_error: AttributeError('OID 2.5.4.5 no encontrado DN=CN=Firma')"
        assert self._cert_code_from_source(raw) == "cert_field_error"

    def test_cert_expired_intacto(self):
        assert self._cert_code_from_source("cert_expired") == "cert_expired"

    def test_cert_not_yet_valid_intacto(self):
        assert self._cert_code_from_source("cert_not_yet_valid") == "cert_not_yet_valid"

    def test_none_devuelve_cert_unknown(self):
        assert self._cert_code_from_source(None) == "cert_unknown"

    def test_poll_source_aplica_normalizacion(self):
        import inspect
        from endpoints.digital_signature import poll as poll_mod
        source = inspect.getsource(poll_mod)
        assert "cert_parse_error" in source
        assert "cert_field_error" in source
        assert "_cert_reason" in source


class TestPollAsyncRateLimitPosition:

    def test_rate_limit_despues_de_fetch_en_codigo(self):
        import inspect
        from endpoints.digital_signature import poll_async as pa_mod
        source = inspect.getsource(pa_mod.poll_async_signing)
        idx_fetch = source.find("get_async_poll_status")
        idx_rate  = source.find("_poll_rate_limit_ok")
        assert idx_fetch >= 0, "No encontré el fetch+auth (get_async_poll_status) en poll_async_signing"
        assert idx_rate  >= 0, "No encontré _poll_rate_limit_ok en poll_async_signing"
        assert idx_fetch < idx_rate, (
            "El rate-limit está ANTES del fetch+auth — viola M-3 (UUIDs ficticios "
            "rellenarían el bucket antes del 404)"
        )

    def test_rate_limit_despues_del_auth_check(self):
        import inspect
        from endpoints.digital_signature import poll_async as pa_mod
        source = inspect.getsource(pa_mod.poll_async_signing)
        idx_404  = source.find("no encontrada")
        idx_rate = source.find("_poll_rate_limit_ok")
        assert idx_404  >= 0, "No encontré el chequeo de auth (404) en poll_async_signing"
        assert idx_rate >= 0, "No encontré _poll_rate_limit_ok en poll_async_signing"
        assert idx_404 < idx_rate, (
            "El rate-limit está ANTES del chequeo de auth/404"
        )


class TestPollBucketPruning:

    def test_constantes_definidas(self):
        from endpoints.digital_signature.poll import _POLL_BUCKET_MAX, _POLL_BUCKET_TTL
        assert _POLL_BUCKET_MAX > 0
        assert _POLL_BUCKET_TTL > 0

    def test_bucket_poda_entradas_viejas(self):
        import time
        from endpoints.digital_signature import poll as poll_mod

        original_buckets = dict(poll_mod._poll_buckets)
        original_max = poll_mod._POLL_BUCKET_MAX
        original_ttl = poll_mod._POLL_BUCKET_TTL

        try:
            poll_mod._POLL_BUCKET_MAX = 5
            poll_mod._POLL_BUCKET_TTL = 0.0

            old_ts = time.monotonic() - 999.0
            for i in range(6):
                poll_mod._poll_buckets[f"user{i}:sess{i}"] = (5.0, old_ts)

            poll_mod._poll_rate_limit_ok("trigger_user", "trigger_sess")

            surviving = {k: v for k, v in poll_mod._poll_buckets.items()
                         if k.startswith("user")}
            assert len(surviving) == 0, (
                f"Entradas viejas no fueron podadas: {list(surviving.keys())}"
            )

        finally:
            poll_mod._poll_buckets.clear()
            poll_mod._poll_buckets.update(original_buckets)
            poll_mod._POLL_BUCKET_MAX = original_max
            poll_mod._POLL_BUCKET_TTL = original_ttl

    def test_bucket_sin_poda_cuando_bajo_max(self):
        import time
        from endpoints.digital_signature import poll as poll_mod

        original_buckets = dict(poll_mod._poll_buckets)
        original_max = poll_mod._POLL_BUCKET_MAX

        try:
            poll_mod._POLL_BUCKET_MAX = 1000
            poll_mod._poll_buckets.clear()

            old_ts = time.monotonic() - 999.0
            for i in range(3):
                poll_mod._poll_buckets[f"user{i}:sess{i}"] = (5.0, old_ts)

            poll_mod._poll_rate_limit_ok("u", "s")

            assert sum(1 for k in poll_mod._poll_buckets if k.startswith("user")) == 3

        finally:
            poll_mod._poll_buckets.clear()
            poll_mod._poll_buckets.update(original_buckets)
            poll_mod._POLL_BUCKET_MAX = original_max
