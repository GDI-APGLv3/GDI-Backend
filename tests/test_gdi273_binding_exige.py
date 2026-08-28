
import base64
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


SCHEMA = "100_test"


def _sesion_de_numerador():
    return {
        "session_id": "SESBINDING1",
        "file_id": "DATA1",
        "schema_name": SCHEMA,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "document_id": "aaaaaaaa-0000-0000-0000-000000000009",
        "is_numerator": True,
        "number": "DECRE-2026-0002-MDEV-LEGAL",
        "status": "pending",
        "expires_at": datetime.datetime(2099, 1, 1, tzinfo=datetime.timezone.utc),
        "consumed_at": None,
        "provider_name": "firmador_gdi",
        "user_cuit": "20000000001",
        "failure_reason": None,
        "reservation_id": "bbbbbbbb-0000-0000-0000-000000000009",
        "created_at": datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
    }


async def _correr_poll_con_binding_rechazando(session):
    from endpoints.digital_signature import poll as poll_mod
    from services.documents.signing.providers import firmador_gdi as fg_mod
    from shared.exceptions import ValidationError

    raw = base64.urlsafe_b64encode(b"\x30\x82" + b"%PDF-1.7\nx").rstrip(b"=").decode()
    redis_mock = MagicMock()
    redis_mock.get.return_value = raw
    redis_mock.delete.return_value = 1

    req = MagicMock()
    req.state.tenant_user_id = session["user_id"]

    async def _run(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def _rechaza(*args, **kwargs):
        raise ValidationError(
            "El documento firmado no coincide con el que se envió a firmar."
        )

    cancel_mock = AsyncMock()
    release_fail_mock = AsyncMock()
    mark_status_mock = AsyncMock(return_value=True)

    with patch.object(poll_mod, "_get_session", AsyncMock(return_value=session)), \
         patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True), \
         patch.object(poll_mod, "run_in_threadpool", AsyncMock(side_effect=_run)), \
         patch.object(poll_mod, "redis_client", redis_mock), \
         patch.object(fg_mod, "redis_client", redis_mock), \
         patch.object(fg_mod.FirmadorGDIProvider, "_verificar_binding", _rechaza), \
         patch.object(poll_mod, "cancel_number", cancel_mock), \
         patch.object(poll_mod, "release_signing_lock_R2_fail", release_fail_mock), \
         patch.object(poll_mod, "_mark_session_status", mark_status_mock), \
         patch.object(poll_mod, "log_signature_event", AsyncMock()):
        respuesta = await poll_mod.poll_signing(
            session_id=session["session_id"],
            request=req,
            current_user=MagicMock(user_id=session["user_id"]),
            schema_name=session["schema_name"],
        )

    return respuesta, cancel_mock, release_fail_mock, mark_status_mock


class TestElRechazoNoDejaNadaColgado:

    @pytest.mark.asyncio
    async def test_el_poll_responde_failed_en_vez_de_reventar(self):
        respuesta, _, _, _ = await _correr_poll_con_binding_rechazando(
            _sesion_de_numerador()
        )
        assert respuesta["status"] == "failed"
        assert respuesta["failure_reason"] == "binding_mismatch"

    @pytest.mark.asyncio
    async def test_libera_el_numero_reservado(self):
        _, cancel_mock, _, _ = await _correr_poll_con_binding_rechazando(
            _sesion_de_numerador()
        )
        assert cancel_mock.await_count == 1, "el número quedó reservado tras el rechazo"
        _, kwargs = cancel_mock.await_args
        assert kwargs["schema_name"] == SCHEMA
        assert kwargs["reason"] == "binding_mismatch"

    @pytest.mark.asyncio
    async def test_suelta_el_lock_de_r2(self):
        _, _, release_fail_mock, _ = await _correr_poll_con_binding_rechazando(
            _sesion_de_numerador()
        )
        assert release_fail_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_la_sesion_queda_en_estado_terminal(self):
        _, _, _, mark_status_mock = await _correr_poll_con_binding_rechazando(
            _sesion_de_numerador()
        )
        args, _ = mark_status_mock.await_args
        assert args[1] == "failed"
        assert args[2] == "binding_mismatch"

    @pytest.mark.asyncio
    async def test_sin_numero_no_intenta_cancelar(self):
        session = _sesion_de_numerador()
        session["is_numerator"] = False
        session["number"] = None
        respuesta, cancel_mock, _, _ = await _correr_poll_con_binding_rechazando(session)
        assert respuesta["status"] == "failed"
        assert cancel_mock.await_count == 0


class TestNoQuedaPerilla:

    def test_la_variable_no_aparece_en_el_codigo(self):
        import inspect
        from services.documents.signing.providers import firmador_gdi
        from endpoints.digital_signature import poll

        for modulo in (firmador_gdi, poll):
            fuente = inspect.getsource(modulo)
            codigo = "\n".join(
                l for l in fuente.splitlines() if not l.strip().startswith("#")
            )
            assert "DIGITAL_SIGNATURE_BINDING_ENFORCE" not in codigo, (
                f"{modulo.__name__} volvió a leer el flag que GDI-273 borró"
            )

    def test_el_mismatch_levanta_sin_importar_el_entorno(self):
        import hashlib
        import json
        import os
        from services.documents.signing.providers.firmador_gdi import FirmadorGDIProvider
        from services.documents.signing.providers import firmador_gdi as fg_mod
        from shared.exceptions import ValidationError

        original = b"%PDF-1.7\noriginal"
        meta = json.dumps({
            "unsigned_sha256": hashlib.sha256(original).hexdigest(),
            "unsigned_len": len(original),
        })
        redis_mock = MagicMock()
        redis_mock.get.return_value = meta

        with patch.object(fg_mod, "redis_client", redis_mock), \
             patch.dict(os.environ, {"DIGITAL_SIGNATURE_BINDING_ENFORCE": "false"}):
            with pytest.raises(ValidationError):
                FirmadorGDIProvider._verificar_binding(
                    "SESX", SCHEMA, b"%PDF-1.7\nOTRA-COSA"
                )


class TestNoPoderVerificarNoEsRechazar:

    def test_sin_meta_deja_pasar(self):
        from services.documents.signing.providers.firmador_gdi import FirmadorGDIProvider
        from services.documents.signing.providers import firmador_gdi as fg_mod

        redis_mock = MagicMock()
        redis_mock.get.return_value = None
        with patch.object(fg_mod, "redis_client", redis_mock):
            FirmadorGDIProvider._verificar_binding("SESX", SCHEMA, b"%PDF-x")

    def test_meta_sin_hash_deja_pasar_pero_avisa(self):
        import json
        from services.documents.signing.providers.firmador_gdi import FirmadorGDIProvider
        from services.documents.signing.providers import firmador_gdi as fg_mod

        redis_mock = MagicMock()
        redis_mock.get.return_value = json.dumps({"file_id": "DATAX"})
        log_mock = MagicMock()
        with patch.object(fg_mod, "redis_client", redis_mock), \
             patch.object(fg_mod, "log", log_mock):
            FirmadorGDIProvider._verificar_binding("SESX", SCHEMA, b"%PDF-x")
        assert log_mock.warning.call_count == 1
        assert "SIN_REFERENCIA" in log_mock.warning.call_args[0][0]
