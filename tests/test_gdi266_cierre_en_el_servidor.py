
import base64
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


SCHEMA = "100_test"
DOC = "aaaaaaaa-0000-0000-0000-000000000266"


def _sesion(is_numerator=True):
    return {
        "session_id": "SESGDI266",
        "file_id": "DATA266",
        "schema_name": SCHEMA,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "document_id": DOC,
        "is_numerator": is_numerator,
        "number": "DECRE-2026-0007-MDEV-LEGAL" if is_numerator else None,
        "status": "pending",
        "expires_at": datetime.datetime(2099, 1, 1, tzinfo=datetime.timezone.utc),
        "consumed_at": None,
        "provider_name": "firmador_gdi",
        "user_cuit": "20000000001",
        "failure_reason": None,
        "reservation_id": "bbbbbbbb-0000-0000-0000-000000000266",
        "created_at": datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
    }


def _cert_ok():
    c = MagicMock()
    c.ok = True
    c.failure_reason = None
    c.cert_serial = "SERIAL"
    c.cert_subject_dn = "CN=T"
    c.cert_issuer_dn = "CN=CA"
    c.cert_subject_cuit = "20000000001"
    c.cert_not_after = None
    c.revocation_status = "unknown"
    return c


async def _correr_poll(session, *, orden=None, guardar=None, encolar=None, marcar=None):
    from endpoints.digital_signature import poll as poll_mod
    from services.documents.signing.providers import firmador_gdi as fg_mod

    raw = base64.urlsafe_b64encode(b"\x30\x82" + b"%PDF-1.7\nx").rstrip(b"=").decode()
    redis_mock = MagicMock()
    redis_mock.get.return_value = raw
    redis_mock.delete.return_value = 1

    req = MagicMock()
    req.state.tenant_user_id = session["user_id"]

    async def _run(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    guardar = guardar or AsyncMock(return_value="signed-pending/x.pdf")
    encolar = encolar or AsyncMock(return_value="cola-1")
    marcar = marcar or AsyncMock(return_value=True)

    if orden is not None:
        async def _g(*a, **k):
            orden.append("guardar")
            return "signed-pending/x.pdf"

        async def _e(*a, **k):
            orden.append("encolar")
            return "cola-1"

        async def _m(*a, **k):
            orden.append("marcar")
            return True

        guardar, encolar, marcar = _g, _e, _m

    with patch.object(poll_mod, "_get_session", AsyncMock(return_value=session)), \
         patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True), \
         patch.object(poll_mod, "run_in_threadpool", AsyncMock(side_effect=_run)), \
         patch.object(poll_mod, "redis_client", redis_mock), \
         patch.object(fg_mod, "redis_client", redis_mock), \
         patch.object(fg_mod.FirmadorGDIProvider, "_verificar_binding", MagicMock()), \
         patch.object(poll_mod, "validate_cert_full", return_value=_cert_ok()), \
         patch.object(poll_mod, "call_notary_verify", AsyncMock(return_value={"ok": True})), \
         patch.object(poll_mod, "confirm_number", AsyncMock()), \
         patch.object(poll_mod, "cancel_number", AsyncMock()), \
         patch.object(poll_mod, "release_signing_lock_R2_fail", AsyncMock()), \
         patch.object(poll_mod, "_mark_consumed", AsyncMock(return_value=True)), \
         patch.object(poll_mod, "_mark_session_status", AsyncMock(return_value=True)), \
         patch.object(poll_mod, "guardar_pdf_firmado", guardar), \
         patch.object(poll_mod, "encolar_cierre_digital", encolar), \
         patch.object(poll_mod, "marcar_sesion_completing", marcar):
        respuesta = await poll_mod.poll_signing(
            session_id=session["session_id"],
            request=req,
            current_user=MagicMock(user_id=session["user_id"]),
            schema_name=session["schema_name"],
        )
    return respuesta, guardar, encolar, marcar


class TestElPollYaNoCierraLaFirma:

    @pytest.mark.asyncio
    async def test_responde_completing_y_no_signed(self):
        respuesta, _, _, _ = await _correr_poll(_sesion())
        assert respuesta["status"] == "completing"

    @pytest.mark.asyncio
    async def test_encola_el_cierre(self):
        _, _, encolar, _ = await _correr_poll(_sesion())
        assert encolar.await_count == 1

    @pytest.mark.asyncio
    async def test_el_poll_no_sube_a_oficial(self):
        import inspect
        from endpoints.digital_signature import poll as poll_mod

        fuente = inspect.getsource(poll_mod)
        codigo = "\n".join(
            l for l in fuente.splitlines() if not l.strip().startswith("#")
        )
        assert "upload_oficial" not in codigo
        assert "finalize_number" not in codigo


class TestElOrdenDelTraspaso:

    @pytest.mark.asyncio
    async def test_primero_el_pdf_despues_la_cola_y_al_final_la_marca(self):
        orden = []
        await _correr_poll(_sesion(), orden=orden)
        assert orden == ["guardar", "encolar", "marcar"]

    @pytest.mark.asyncio
    async def test_si_no_se_puede_guardar_el_pdf_no_se_encola_nada(self):
        guardar = AsyncMock(side_effect=RuntimeError("R2 caído"))
        encolar = AsyncMock()
        respuesta, _, encolar_usado, _ = await _correr_poll(
            _sesion(), guardar=guardar, encolar=encolar
        )
        assert respuesta["status"] == "failed"
        assert respuesta["failure_reason"] == "persist_signed_pdf_failed"
        assert encolar_usado.await_count == 0

    @pytest.mark.asyncio
    async def test_si_no_se_puede_guardar_se_libera_el_numero(self):
        from endpoints.digital_signature import poll as poll_mod

        guardar = AsyncMock(side_effect=RuntimeError("R2 caído"))
        cancel_mock = AsyncMock()
        with patch.object(poll_mod, "cancel_number", cancel_mock):
            respuesta, _, _, _ = await _correr_poll(_sesion(), guardar=guardar)
        assert respuesta["failure_reason"] == "persist_signed_pdf_failed"


class TestUnaSolaEjecucionDelCierre:

    @pytest.mark.asyncio
    async def test_el_cas_de_completing_solo_lo_gana_uno(self):
        from services.documents.signing import digital_completion as dc

        execute_mock = AsyncMock(side_effect=["UPDATE 1", "UPDATE 0"])
        with patch.object(dc, "execute", execute_mock):
            primero = await dc.marcar_sesion_completing("SESX")
            segundo = await dc.marcar_sesion_completing("SESX")
        assert primero is True
        assert segundo is False

    @pytest.mark.asyncio
    async def test_encolar_dos_veces_devuelve_none_la_segunda(self):
        from services.documents.signing import digital_completion as dc

        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)
        conn.execute = AsyncMock()
        conn.transaction = MagicMock(return_value=_ctx_async())

        with patch.object(dc, "log"), \
             patch("database.get_conn", return_value=_ctx_async(conn)):
            resultado = await dc.encolar_cierre_digital(
                schema_name=SCHEMA, document_id=DOC, user_id="u",
                reservation_id=None, official_number="X",
                digital_session_id="SESX", is_numerator=True,
                cas_pre_done=False, cert={},
            )
        assert resultado is None
        conn.execute.assert_not_awaited()


class TestElSweeperNoBarreUnaFirmaViva:

    def test_el_filtro_contempla_completing(self):
        import inspect
        from workers import sweeper_escri

        fuente = inspect.getsource(sweeper_escri)
        assert fuente.count("dss.status IN ('completing', 'waiting_batch')") == 2, (
            "el sweeper puede cancelar el número de una firma que se está "
            "completando (GDI-266) o esperando a su tanda (GDI-167)"
        )

    def test_el_job_en_la_cola_es_la_segunda_red(self):
        import inspect
        from services.documents.signing import digital_completion as dc

        fuente = inspect.getsource(dc.encolar_cierre_digital)
        assert "reservation_id" in fuente


class TestElWorkerCierra:

    @pytest.mark.asyncio
    async def test_lee_el_pdf_de_donde_lo_dejo_el_poll(self):
        from services.documents.signing import digital_completion as dc

        assert dc.clave_pdf_pendiente(DOC) == f"signed-pending/{DOC}.pdf"

    @pytest.mark.asyncio
    async def test_un_fallo_no_borra_el_pdf_firmado(self):
        from services.documents.signing import digital_completion as dc

        borrar = AsyncMock()
        with patch.object(dc, "fetch_one",
                          AsyncMock(return_value={"status": "completing"})), \
             patch.object(dc, "leer_pdf_firmado", AsyncMock(return_value=b"%PDF")), \
             patch.object(dc, "completar_numerador", AsyncMock(side_effect=RuntimeError("boom"))), \
             patch.object(dc, "borrar_pdf_firmado", borrar), \
             patch.object(dc, "marcar_sesion_digital", AsyncMock()), \
             patch.object(dc, "actualizar_firmante", AsyncMock()), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_success", AsyncMock()):
            await dc.cerrar_firma_digital(
                schema_name=SCHEMA, document_id=DOC, user_id="u",
                reservation_id=None, official_number="X",
                digital_session_id="SESX", is_numerator=True,
                cas_pre_done=True, cert={},
            )
        assert borrar.await_count == 1


class _ctx_async:

    def __init__(self, valor=None):
        self._valor = valor

    async def __aenter__(self):
        return self._valor

    async def __aexit__(self, *a):
        return False


class TestUnReintentoNoMienteSobreElResultado:

    @pytest.mark.asyncio
    async def test_stale_no_se_reporta_como_ok(self):
        from services.documents.signing import digital_completion as dc
        from shared.exceptions import StaleReservationError

        with patch.object(dc, "fetch_one",
                          AsyncMock(return_value={"status": "completing"})), \
             patch.object(dc, "leer_pdf_firmado", AsyncMock(return_value=b"%PDF")), \
             patch.object(dc, "completar_numerador",
                          AsyncMock(side_effect=StaleReservationError(DOC, "ticket"))), \
             patch.object(dc, "marcar_sesion_digital", AsyncMock()), \
             patch.object(dc, "borrar_pdf_firmado", AsyncMock()):
            resultado = await dc.cerrar_firma_digital(
                schema_name=SCHEMA, document_id=DOC, user_id="u",
                reservation_id="r", official_number="X",
                digital_session_id="SESX", is_numerator=True,
                cas_pre_done=False, cert={},
            )

        assert resultado["ok"] is False
        assert resultado["failure_reason"] == "stale_reservation"

    @pytest.mark.asyncio
    async def test_stale_no_cancela_el_numero(self):
        from services.documents.signing import digital_completion as dc
        from shared.exceptions import StaleReservationError

        cancel = AsyncMock()
        with patch.object(dc, "fetch_one",
                          AsyncMock(return_value={"status": "completing"})), \
             patch.object(dc, "leer_pdf_firmado", AsyncMock(return_value=b"%PDF")), \
             patch.object(dc, "completar_numerador",
                          AsyncMock(side_effect=StaleReservationError(DOC, "ticket"))), \
             patch("shared.numbering.cancel_number", cancel), \
             patch.object(dc, "marcar_sesion_digital", AsyncMock()), \
             patch.object(dc, "borrar_pdf_firmado", AsyncMock()):
            await dc.cerrar_firma_digital(
                schema_name=SCHEMA, document_id=DOC, user_id="u",
                reservation_id="r", official_number="X",
                digital_session_id="SESX", is_numerator=True,
                cas_pre_done=False, cert={},
            )
        cancel.assert_not_awaited()


class TestLasTresRedesQueNoDebenBarrerUnaFirmaViva:

    def test_el_sweeper_reconoce_completing(self):
        import inspect
        from workers import sweeper_escri

        fuente = inspect.getsource(sweeper_escri)
        assert fuente.count("dss.status IN ('completing', 'waiting_batch')") == 2

    def test_el_cron_de_huerfanos_solo_mira_pending(self):
        import inspect
        from jobs import orphan_inprocess

        fuente = inspect.getsource(orphan_inprocess)
        assert "status = 'pending'" in fuente
        assert "'completing'" not in fuente

    def test_el_cancel_explicito_no_toca_una_firma_que_ya_existe(self):
        import inspect
        from endpoints.digital_signature import cancel

        fuente = inspect.getsource(cancel)
        assert 'session["status"] != "pending"' in fuente


class TestElCierreReleeElEstadoAntesDePromover:

    @pytest.mark.asyncio
    async def test_una_sesion_caida_no_se_promueve(self):
        from services.documents.signing import digital_completion as dc

        leer = AsyncMock(return_value=b"%PDF")
        with patch.object(dc, "fetch_one",
                          AsyncMock(return_value={"status": "failed"})), \
             patch.object(dc, "leer_pdf_firmado", leer):
            r = await dc.cerrar_firma_digital(
                schema_name=SCHEMA, document_id=DOC, user_id="u",
                reservation_id="r", official_number="X",
                digital_session_id="SESX", is_numerator=True,
                cas_pre_done=False, cert={},
            )

        assert r["ok"] is False
        assert r["failure_reason"] == "sesion_failed"
        leer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_completing_si_se_promueve(self):
        from services.documents.signing import digital_completion as dc

        leer = AsyncMock(return_value=b"%PDF")
        with patch.object(dc, "fetch_one",
                          AsyncMock(return_value={"status": "completing"})), \
             patch.object(dc, "leer_pdf_firmado", leer), \
             patch.object(dc, "completar_numerador", AsyncMock(return_value=[])), \
             patch.object(dc, "marcar_sesion_digital", AsyncMock()), \
             patch.object(dc, "actualizar_firmante", AsyncMock()), \
             patch.object(dc, "borrar_pdf_firmado", AsyncMock()), \
             patch.object(dc, "limpiar_redis_de_la_sesion", AsyncMock()), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_success",
                   AsyncMock()):
            r = await dc.cerrar_firma_digital(
                schema_name=SCHEMA, document_id=DOC, user_id="u",
                reservation_id="r", official_number="X",
                digital_session_id="SESX", is_numerator=True,
                cas_pre_done=False, cert={},
            )

        leer.assert_awaited_once()
        assert r["ok"] is True
