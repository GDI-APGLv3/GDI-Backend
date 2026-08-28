
import inspect
import json
from unittest.mock import MagicMock, patch

import pytest


class TestUnaSolaSesionDeFirmaViva:

    def test_el_insert_usa_on_conflict(self):
        from services.documents.signing import unified_signing

        fuente = inspect.getsource(unified_signing._enqueue_sign_session)
        assert "ON CONFLICT" in fuente
        assert "DO NOTHING" in fuente

    def test_el_conflicto_matchea_el_indice_de_la_migracion(self):
        from services.documents.signing import unified_signing

        fuente = " ".join(inspect.getsource(unified_signing._enqueue_sign_session).split())
        assert "ON CONFLICT (schema_name, document_id, user_id)" in fuente
        assert "WHERE job_type = 'sign' AND status IN ('pending', 'processing')" in fuente

    def test_al_perder_la_carrera_devuelve_la_sesion_existente(self):
        from services.documents.signing import unified_signing

        fuente = inspect.getsource(unified_signing._enqueue_sign_session)
        assert "if row is None" in fuente
        codigo = "\n".join(
            l for l in fuente.splitlines() if not l.strip().startswith("#")
        )
        assert "await cancel_number(" not in codigo

    def test_la_migracion_crea_el_indice(self):
        from pathlib import Path

        ruta = (Path(__file__).resolve().parents[2] / "GDI-BD" / "sql" / "migrations"
                / "103_gdi271_signing_sessions_sign_unique.sql")
        if not ruta.exists():
            pytest.skip("GDI-BD no está en este worktree")
        sql = ruta.read_text(encoding="utf-8")
        assert "idx_signing_sessions_sign_active_unique" in sql
        assert "(schema_name, document_id, user_id)" in sql
        assert "job_type = 'sign'" in sql

    def test_la_migracion_resuelve_duplicados_previos(self):
        from pathlib import Path

        ruta = (Path(__file__).resolve().parents[2] / "GDI-BD" / "sql" / "migrations"
                / "103_gdi271_signing_sessions_sign_unique.sql")
        if not ruta.exists():
            pytest.skip("GDI-BD no está en este worktree")
        sql = ruta.read_text(encoding="utf-8")
        assert "duplicate_sign_session_gdi271" in sql
        assert "ROW_NUMBER()" in sql


class TestLaFirmaDigitalRespetaElTurno:

    def test_valida_el_turno(self):
        from services.documents.signing import dispatcher

        fuente = inspect.getsource(dispatcher.dispatch_digital_signing)
        assert "SignerTurnPendingError" in fuente
        assert "is_my_turn" in fuente

    def test_usa_la_condicion_canonica_y_no_otra_copia(self):
        from services.documents.signing import dispatcher

        fuente = inspect.getsource(dispatcher._get_signing_data)
        assert "_is_my_turn_condition" in fuente

    def test_rechaza_antes_de_tomar_el_lock_y_reservar_numero(self):
        fuente = inspect.getsource(
            __import__("services.documents.signing.dispatcher", fromlist=["d"]).dispatch_digital_signing
        )
        assert fuente.index("SignerTurnPendingError") < fuente.index("acquire_signing_lock_R2")

    def test_rechaza_a_quien_ya_firmo(self):
        from services.documents.signing import dispatcher

        fuente = inspect.getsource(dispatcher.dispatch_digital_signing)
        assert "signer_status" in fuente

    def test_el_error_es_el_mismo_del_carril_electronico(self):
        from services.documents.signing import dispatcher, unified_signing

        assert "SignerTurnPendingError" in inspect.getsource(dispatcher)
        assert "SignerTurnPendingError" in inspect.getsource(unified_signing)


class TestExpiracionDigitalLiberaElNumero:

    def test_la_rama_expirada_cancela_el_numero(self):
        from endpoints.digital_signature import poll

        fuente = inspect.getsource(poll)
        i = fuente.index('await _mark_session_status(session_id, "expired")')
        ventana = fuente[i:i + 1200]
        assert "cancel_number" in ventana

    def test_pasa_el_reservation_id(self):
        from endpoints.digital_signature import poll

        fuente = inspect.getsource(poll)
        i = fuente.index('await _mark_session_status(session_id, "expired")')
        ventana = fuente[i:i + 1200]
        assert "reservation_id" in ventana

    def test_es_soft_fail(self):
        from endpoints.digital_signature import poll

        fuente = inspect.getsource(poll)
        i = fuente.index('await _mark_session_status(session_id, "expired")')
        ventana = fuente[i:i + 1200]
        assert "soft-fail" in ventana or "except Exception" in ventana


class TestEntropiaDeLosIdentificadoresPublicos:

    def test_128_bits(self):
        from services.documents.signing.providers import firmador_gdi

        fuente = inspect.getsource(firmador_gdi)
        assert "token_hex(6)" not in fuente
        assert "token_hex(16)" in fuente


class TestBindingDelDocumentoFirmado:

    def test_se_guarda_el_hash_del_pdf_enviado(self):
        from services.documents.signing.providers import firmador_gdi

        fuente = inspect.getsource(firmador_gdi.FirmadorGDIProvider.start_signing)
        assert "unsigned_sha256" in fuente

    def test_se_verifica_al_volver(self):
        from services.documents.signing.providers import firmador_gdi

        fuente = inspect.getsource(firmador_gdi.FirmadorGDIProvider.poll_signing)
        assert "_verificar_binding" in fuente

    @pytest.mark.asyncio
    async def test_el_endpoint_realmente_lo_ejecuta(self):
        import base64
        from unittest.mock import AsyncMock, patch as _patch
        from endpoints.digital_signature import poll as poll_mod
        from services.documents.signing.providers import firmador_gdi as fg_mod

        session = {
            "session_id": "SESABC123",
            "file_id": "DATA1",
            "schema_name": "100_test",
            "user_id": "11111111-1111-1111-1111-111111111111",
            "document_id": "aaaaaaaa-0000-0000-0000-000000000001",
            "is_numerator": False,
            "number": None,
            "status": "pending",
            "expires_at": __import__("datetime").datetime(
                2099, 1, 1, tzinfo=__import__("datetime").timezone.utc
            ),
            "consumed_at": None,
            "provider_name": "firmador_gdi",
            "user_cuit": "20000000001",
            "failure_reason": None,
            "reservation_id": None,
            "created_at": __import__("datetime").datetime(
                2025, 1, 1, tzinfo=__import__("datetime").timezone.utc
            ),
        }

        raw = base64.urlsafe_b64encode(b"\x30\x82" + b"%PDF-1.7\nx").rstrip(b"=").decode()
        redis_mock = MagicMock()
        redis_mock.get.return_value = raw
        redis_mock.delete.return_value = 1

        cert_result = MagicMock()
        cert_result.ok = True
        cert_result.failure_reason = None
        cert_result.cert_serial = "SERIAL"
        cert_result.cert_subject_dn = "CN=T"
        cert_result.cert_issuer_dn = "CN=CA"
        cert_result.cert_subject_cuit = "20000000001"
        cert_result.cert_not_after = None
        cert_result.revocation_status = "unknown"

        req = MagicMock()
        req.state.tenant_user_id = session["user_id"]

        async def _run(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        binding_spy = MagicMock()

        with _patch.object(poll_mod, "_get_session", AsyncMock(return_value=session)), \
             _patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True), \
             _patch.object(poll_mod, "run_in_threadpool", AsyncMock(side_effect=_run)), \
             _patch.object(poll_mod, "redis_client", redis_mock), \
             _patch.object(fg_mod, "redis_client", redis_mock), \
             _patch.object(fg_mod.FirmadorGDIProvider, "_verificar_binding", binding_spy), \
             _patch.object(poll_mod, "validate_cert_full", return_value=cert_result), \
             _patch.object(poll_mod, "call_notary_verify", AsyncMock(return_value={"ok": True})), \
             _patch.object(poll_mod, "release_signing_lock_R2_fail", AsyncMock()), \
             _patch.object(poll_mod, "_mark_consumed", AsyncMock(return_value=True)), \
             _patch.object(poll_mod, "_mark_session_status", AsyncMock(return_value=True)), \
             _patch.object(poll_mod, "_update_document_signer", AsyncMock()), \
             _patch.object(poll_mod, "guardar_pdf_firmado", AsyncMock()) as guardar_spy, \
             _patch.object(
                 poll_mod, "encolar_cierre_digital", AsyncMock(return_value="COLA-1")
             ) as encolar_spy, \
             _patch.object(poll_mod, "marcar_sesion_completing", AsyncMock()), \
             _patch.object(poll_mod, "log_signature_event", AsyncMock()):
            resultado = await poll_mod.poll_signing(
                session_id=session["session_id"],
                request=req,
                current_user=MagicMock(user_id=session["user_id"]),
                schema_name=session["schema_name"],
            )

        assert binding_spy.call_count == 1, (
            "El endpoint NO ejecutó _verificar_binding. El fix del CRÍTICO 4 "
            "(GDI-276) fue revertido o el provider volvió a ser AutoFirmaProvider."
        )

        assert resultado["status"] == "completing", (
            f"El poll no llegó al final del flujo GDI-266: {resultado}"
        )
        assert guardar_spy.await_count == 1, "No se persistió el PDF firmado"
        assert encolar_spy.await_count == 1, "No se encoló el cierre digital"

    def test_por_defecto_rechaza(self):
        from services.documents.signing.providers.firmador_gdi import FirmadorGDIProvider
        from shared.exceptions import ValidationError

        meta = json.dumps({"unsigned_sha256": "a" * 64, "unsigned_len": 10})
        fake_redis = MagicMock()
        fake_redis.get.return_value = meta
        with patch.object(
            __import__("services.documents.signing.providers.firmador_gdi", fromlist=["x"]),
            "redis_client", fake_redis,
        ):
            with pytest.raises(ValidationError):
                FirmadorGDIProvider._verificar_binding("SESX", "100_test", b"0123456789extra")

    def test_no_hay_perilla_que_lo_apague(self):
        from services.documents.signing.providers.firmador_gdi import FirmadorGDIProvider
        from shared.exceptions import ValidationError

        meta = json.dumps({"unsigned_sha256": "a" * 64, "unsigned_len": 10})
        fake_redis = MagicMock()
        fake_redis.get.return_value = meta
        with (
            patch.object(
                __import__("services.documents.signing.providers.firmador_gdi", fromlist=["x"]),
                "redis_client", fake_redis,
            ),
            patch.dict("os.environ", {"DIGITAL_SIGNATURE_BINDING_ENFORCE": "false"}),
        ):
            with pytest.raises(ValidationError):
                FirmadorGDIProvider._verificar_binding("SESX", "100_test", b"0123456789extra")

    def test_una_firma_incremental_legitima_pasa(self):
        import hashlib
        from services.documents.signing.providers.firmador_gdi import FirmadorGDIProvider

        original = b"%PDF-1.7 documento original"
        firmado = original + b"<<apendice con la firma>>"
        meta = json.dumps({
            "unsigned_sha256": hashlib.sha256(original).hexdigest(),
            "unsigned_len": len(original),
        })
        fake_redis = MagicMock()
        fake_redis.get.return_value = meta
        with (
            patch.object(
                __import__("services.documents.signing.providers.firmador_gdi", fromlist=["x"]),
                "redis_client", fake_redis,
            ),
        ):
            FirmadorGDIProvider._verificar_binding("SESX", "100_test", firmado)

    def test_sesion_vieja_sin_hash_no_rompe(self):
        from services.documents.signing.providers.firmador_gdi import FirmadorGDIProvider

        fake_redis = MagicMock()
        fake_redis.get.return_value = json.dumps({"file_id": "DATAX"})
        with (
            patch.object(
                __import__("services.documents.signing.providers.firmador_gdi", fromlist=["x"]),
                "redis_client", fake_redis,
            ),
        ):
            FirmadorGDIProvider._verificar_binding("SESX", "100_test", b"%PDF-x")
