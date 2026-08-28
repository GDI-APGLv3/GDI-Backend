
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.documents.signing import batch_digital as bd


SCHEMA = "100_test"
USUARIO = str(uuid.uuid4())
TANDA = str(uuid.uuid4())


def _sesion(doc, estado="pending", numerador=True, numero="DECRE-2026-0040-MDEV-LEGAL"):
    return {
        "session_id": "SES" + doc[:8].upper(),
        "file_id": "DATA" + doc[:8].upper(),
        "schema_name": SCHEMA,
        "user_id": USUARIO,
        "document_id": doc,
        "is_numerator": numerador,
        "number": numero,
        "status": estado,
        "expires_at": None,
        "consumed_at": None,
        "user_cuit": "20000000001",
        "failure_reason": None,
        "reservation_id": str(uuid.uuid4()),
        "batch_id": TANDA,
    }


DOCS = [str(uuid.uuid4()) for _ in range(3)]


class TestAbrirLaTanda:

    @pytest.mark.asyncio
    async def test_rechaza_mas_del_tope(self):
        from shared.exceptions import ValidationError

        with pytest.raises(ValidationError) as exc:
            await bd.abrir_tanda(
                [str(uuid.uuid4()) for _ in range(6)], USUARIO, schema_name=SCHEMA
            )
        assert "5" in str(exc.value)

    @pytest.mark.asyncio
    async def test_rechaza_una_tanda_vacia(self):
        from shared.exceptions import ValidationError

        with pytest.raises(ValidationError):
            await bd.abrir_tanda([], USUARIO, schema_name=SCHEMA)

    @pytest.mark.asyncio
    async def test_ignora_repetidos_y_conserva_el_orden(self):
        vistos = []

        async def _dispatch(doc, user, **kw):
            vistos.append(doc)
            return {"session_id": "SES" + doc[:6], "file_id": "DATA" + doc[:6]}

        with patch("services.documents.signing.dispatcher.dispatch_digital_signing", _dispatch), \
             patch.object(bd, "_publicar_manifiesto", AsyncMock(return_value="MANX")):
            r = await bd.abrir_tanda(
                [DOCS[0], DOCS[1], DOCS[0]], USUARIO, schema_name=SCHEMA
            )

        assert vistos == [DOCS[0], DOCS[1]]
        assert len(r["documents"]) == 2

    @pytest.mark.asyncio
    async def test_si_uno_falla_al_prepararse_no_se_abre_nada(self):
        async def _dispatch(doc, user, **kw):
            if doc == DOCS[1]:
                raise RuntimeError("el segundo no se pudo preparar")
            return {"session_id": "SES" + doc[:6], "file_id": "DATA" + doc[:6]}

        revertir = AsyncMock()
        with patch("services.documents.signing.dispatcher.dispatch_digital_signing", _dispatch), \
             patch.object(bd, "_revertir_apertura", revertir):
            with pytest.raises(RuntimeError):
                await bd.abrir_tanda(DOCS, USUARIO, schema_name=SCHEMA)

        revertir.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_la_uri_es_de_lote_y_declara_version_nueva(self):
        uri = bd._uri_de_tanda("MANABC", TANDA)
        assert uri.startswith("gdifirma://batch?")
        assert "ver=1_1" in uri
        assert "manifest=MANABC" in uri
        assert "keystore=PKCS11" in uri
        assert "&id=BAT" in uri
        assert "-" not in uri.split("&id=")[1].split("&")[0]


class TestNadaSeCierraHastaQueEstenTodas:

    @pytest.mark.asyncio
    async def test_con_hermanas_pendientes_no_encola_ningun_cierre(self):
        sesiones = [
            _sesion(DOCS[0], "waiting_batch"),
            _sesion(DOCS[1], "pending"),
            _sesion(DOCS[2], "pending"),
        ]
        encolar = AsyncMock()
        with patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch("services.documents.signing.digital_completion.encolar_cierre_digital", encolar):
            r = await bd.registrar_firma_de_una(_sesion(DOCS[0]))

        assert r["estado"] == "waiting_batch"
        assert r["faltan"] == 2
        encolar.assert_not_awaited(), (
            "cerró un documento con la tanda a medias: si después falla otra, "
            "ese documento ya no se puede borrar"
        )

    @pytest.mark.asyncio
    async def test_cuando_firma_la_ultima_se_encolan_todos_los_cierres(self):
        sesiones = [_sesion(d, "waiting_batch") for d in DOCS]
        encolar = AsyncMock()
        with patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch("services.documents.signing.digital_completion.encolar_cierre_digital", encolar):
            r = await bd.registrar_firma_de_una(_sesion(DOCS[2]))

        assert r["estado"] == "completing"
        assert encolar.await_count == 3

    @pytest.mark.asyncio
    async def test_el_cierre_es_el_mismo_de_la_firma_de_a_una(self):
        import inspect

        fuente = inspect.getsource(bd._cerrar_tanda_completa)
        assert "encolar_cierre_digital" in fuente


class TestCuandoLaTandaCae:

    @pytest.mark.asyncio
    async def test_borra_los_pdf_cancela_los_numeros_y_suelta_los_locks(self):
        sesiones = [_sesion(d, "waiting_batch") for d in DOCS]
        borrar, cancelar, soltar = AsyncMock(), AsyncMock(), AsyncMock()

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado", borrar), \
             patch("shared.numbering.cancel_number", cancelar), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", soltar):
            r = await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="uno_fallo")

        assert r["cancelled"] == 3
        assert borrar.await_count == 3, "quedaron PDF firmados de una tanda que no ocurrió"
        assert cancelar.await_count == 3, "los números no volvieron al circuito"
        assert soltar.await_count == 3, "quedaron documentos bloqueados"

    @pytest.mark.asyncio
    async def test_no_toca_las_que_ya_estan_terminadas(self):
        sesiones = [_sesion(DOCS[0], "signed"), _sesion(DOCS[1], "waiting_batch")]
        cancelar = AsyncMock()

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado", AsyncMock()), \
             patch("shared.numbering.cancel_number", cancelar), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", AsyncMock()):
            r = await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="x")

        assert r["cancelled"] == 1
        assert cancelar.await_count == 1

    @pytest.mark.asyncio
    async def test_un_fallo_al_borrar_no_frena_la_cancelacion(self):
        sesiones = [_sesion(DOCS[0], "waiting_batch")]
        soltar = AsyncMock()

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado",
                   AsyncMock(side_effect=RuntimeError("R2 caído"))), \
             patch("shared.numbering.cancel_number", AsyncMock()), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", soltar):
            r = await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="x")

        assert r["cancelled"] == 1
        soltar.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_se_puede_cancelar_una_tanda_ajena(self):
        from shared.exceptions import ValidationError

        with patch.object(bd, "_sesiones_de_la_tanda",
                          AsyncMock(return_value=[_sesion(DOCS[0], "waiting_batch")])):
            with pytest.raises(ValidationError):
                await bd.cancelar_tanda(
                    TANDA, schema_name=SCHEMA, motivo="x", user_id=str(uuid.uuid4())
                )


class TestElEstadoDelConjunto:

    @pytest.mark.asyncio
    async def test_una_sola_fallada_tumba_la_tanda_entera(self):
        sesiones = [
            _sesion(DOCS[0], "signed"),
            _sesion(DOCS[1], "failed"),
            _sesion(DOCS[2], "waiting_batch"),
        ]
        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)):
            e = await bd.estado_de_tanda(TANDA, schema_name=SCHEMA, user_id=USUARIO)
        assert e["status"] == "failed"

    @pytest.mark.asyncio
    async def test_mientras_falte_poner_el_pin_sigue_pendiente(self):
        sesiones = [_sesion(DOCS[0], "waiting_batch"), _sesion(DOCS[1], "pending")]
        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)):
            e = await bd.estado_de_tanda(TANDA, schema_name=SCHEMA, user_id=USUARIO)
        assert e["status"] == "pending"

    @pytest.mark.asyncio
    async def test_todas_guardadas_es_signed(self):
        sesiones = [_sesion(d, "signed") for d in DOCS]
        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)):
            e = await bd.estado_de_tanda(TANDA, schema_name=SCHEMA, user_id=USUARIO)
        assert e["status"] == "signed"
        assert e["signed"] == 3

    @pytest.mark.asyncio
    async def test_una_tanda_ajena_no_existe(self):
        with patch.object(bd, "_sesiones_de_la_tanda",
                          AsyncMock(return_value=[_sesion(DOCS[0])])):
            e = await bd.estado_de_tanda(
                TANDA, schema_name=SCHEMA, user_id=str(uuid.uuid4())
            )
        assert e is None


class TestLaRegla:

    def test_la_tanda_vence_antes_que_el_sweeper(self):
        import os

        sweeper_ttl = os.getenv("SWEEPER_RESERVED_EXPIRY_TTL", "15 minutes")
        minutos_sweeper = int(sweeper_ttl.split()[0])
        assert bd.TANDA_TTL_MINUTOS < minutos_sweeper

    def test_el_tope_es_cinco(self):
        assert bd.MAX_DOCUMENTOS_POR_TANDA == 5


class TestUnaHermanaCaidaArrastraALaTanda:

    @pytest.mark.asyncio
    async def test_una_fallada_cancela_la_tanda_en_vez_de_cerrarla(self):
        hermanas = [
            _sesion(DOCS[0], estado="waiting_batch"),
            _sesion(DOCS[1], estado="failed"),
            _sesion(DOCS[2], estado="waiting_batch"),
        ]
        cerrar = AsyncMock()
        cancelar = AsyncMock(return_value={"cancelled": 3})

        with patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=hermanas)), \
             patch.object(bd, "_cerrar_tanda_completa", cerrar), \
             patch.object(bd, "cancelar_tanda", cancelar):
            r = await bd.registrar_firma_de_una(_sesion(DOCS[2]))

        assert r["estado"] == "failed"
        cerrar.assert_not_awaited()
        cancelar.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_una_cancelada_tambien_arrastra(self):
        hermanas = [
            _sesion(DOCS[0], estado="waiting_batch"),
            _sesion(DOCS[1], estado="cancelled"),
        ]
        cancelar = AsyncMock(return_value={"cancelled": 2})

        with patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=hermanas)), \
             patch.object(bd, "_cerrar_tanda_completa", AsyncMock()), \
             patch.object(bd, "cancelar_tanda", cancelar):
            r = await bd.registrar_firma_de_una(_sesion(DOCS[0]))

        assert r["estado"] == "failed"
        cancelar.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_signed_NO_es_una_caida(self):
        hermanas = [
            _sesion(DOCS[0], estado="signed"),
            _sesion(DOCS[1], estado="waiting_batch"),
        ]
        cerrar = AsyncMock()
        cancelar = AsyncMock()

        with patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=hermanas)), \
             patch.object(bd, "_cerrar_tanda_completa", cerrar), \
             patch.object(bd, "cancelar_tanda", cancelar):
            r = await bd.registrar_firma_de_una(_sesion(DOCS[1]))

        cancelar.assert_not_awaited()
        assert r["estado"] == "completing"
        cerrar.assert_awaited_once()


class TestElEncoladoNoDejaTandasAMedias:

    @pytest.mark.asyncio
    async def test_si_un_cierre_no_se_puede_encolar_cae_la_tanda(self):
        sesiones = [_sesion(d, estado="waiting_batch") for d in DOCS[:3]]
        encolar = AsyncMock(side_effect=[("COLA1"), RuntimeError("BD saturada"), "COLA3"])
        cancelar = AsyncMock(return_value={"cancelled": 3})

        with patch("services.documents.signing.digital_completion.encolar_cierre_digital",
                   encolar), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "cancelar_tanda", cancelar):
            await bd._cerrar_tanda_completa(TANDA, sesiones, schema_name=SCHEMA)

        cancelar.assert_awaited_once()
        assert encolar.await_count == 2


class TestLaCancelacionMarcaAntesDeBorrar:

    @pytest.mark.asyncio
    async def test_el_estado_se_marca_antes_de_tocar_el_pdf(self):
        orden = []

        async def _marcar(*a, **k):
            orden.append("marcar")
            return "UPDATE 1"

        async def _borrar(*a, **k):
            orden.append("borrar")

        with patch.object(bd, "_sesiones_de_la_tanda",
                          AsyncMock(return_value=[_sesion(DOCS[0], estado="waiting_batch")])), \
             patch.object(bd, "execute", _marcar), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado",
                   _borrar), \
             patch("shared.numbering.cancel_number", AsyncMock()), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail",
                   AsyncMock()), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()):
            await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="prueba")

        assert orden[0] == "marcar", f"se tocó el PDF antes de marcar: {orden}"

    @pytest.mark.asyncio
    async def test_no_se_le_tocan_los_recursos_a_una_sesion_que_ya_movio_otro(self):
        borrar = AsyncMock()

        with patch.object(bd, "_sesiones_de_la_tanda",
                          AsyncMock(return_value=[_sesion(DOCS[0], estado="waiting_batch")])), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 0")), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado",
                   borrar), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()):
            r = await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="prueba")

        borrar.assert_not_awaited()
        assert r["cancelled"] == 0


class TestElSweeperLevantaLasTandasHuerfanas:

    @pytest.mark.asyncio
    async def test_una_tanda_vencida_se_cancela(self):
        from workers import sweeper_escri as sw

        cancelar = AsyncMock(return_value={"cancelled": 3})
        filas = [{"batch_id": TANDA, "total": 3, "firmadas": 1}]

        with patch.object(sw, "fetch_all", AsyncMock(return_value=filas)), \
             patch("services.documents.signing.batch_digital.cancelar_tanda", cancelar):
            await sw._handle_tandas_huerfanas(SCHEMA)

        cancelar.assert_awaited_once()
        assert cancelar.await_args.args[0] == TANDA

    @pytest.mark.asyncio
    async def test_una_tanda_que_falla_no_frena_a_las_otras(self):
        from workers import sweeper_escri as sw

        otra = str(uuid.uuid4())
        cancelar = AsyncMock(side_effect=[RuntimeError("boom"), {"cancelled": 2}])
        filas = [
            {"batch_id": TANDA, "total": 3, "firmadas": 1},
            {"batch_id": otra, "total": 2, "firmadas": 2},
        ]

        with patch.object(sw, "fetch_all", AsyncMock(return_value=filas)), \
             patch("services.documents.signing.batch_digital.cancelar_tanda", cancelar):
            await sw._handle_tandas_huerfanas(SCHEMA)

        assert cancelar.await_count == 2

    def test_completing_no_entra_en_el_barrido(self):
        import inspect

        from workers import sweeper_escri as sw

        fuente = inspect.getsource(sw._handle_tandas_huerfanas)
        assert "'pending', 'waiting_batch'" in fuente
        assert "completing" not in fuente.split('"""')[2]

    def test_va_primero_en_el_orden_del_sweeper(self):
        import inspect

        from workers import sweeper_escri as sw

        cuerpo = inspect.getsource(sw._sweep_schema)
        llamadas = [l.strip() for l in cuerpo.splitlines() if l.strip().startswith("await ")]
        assert llamadas[0].startswith("await _handle_tandas_huerfanas")


class TestElCierreDeLaTandaLlevaSuContexto:

    @pytest.mark.asyncio
    async def test_el_cierre_no_repite_el_cas_que_el_poll_ya_hizo(self):
        from services.documents.signing import batch_digital as bd

        encolados = []

        async def _encolar(**kw):
            encolados.append(kw)
            return "cola-1"

        sesiones = [
            {
                "session_id": "SES-A", "document_id": "doc-a", "user_id": "u1",
                "is_numerator": True, "number": "TOKEN-2026-1", "status": "waiting_batch",
                "reservation_id": "res-a", "file_id": "DATA-A",
                "cas_pre_done": True, "cert_payload": {"cert_serial": "AA11"},
            },
        ]

        with patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch("services.documents.signing.digital_completion.encolar_cierre_digital", _encolar):
            await bd._cerrar_tanda_completa("batch-1", sesiones, schema_name="100_test")

        assert len(encolados) == 1
        assert encolados[0]["cas_pre_done"] is True, (
            "el cierre iba a repetir el CAS que el poll ya hizo → StaleReservationError "
            "→ con el todo-o-nada, la tanda entera al piso"
        )

    @pytest.mark.asyncio
    async def test_el_certificado_llega_al_cierre_para_la_auditoria(self):
        from services.documents.signing import batch_digital as bd

        encolados = []

        async def _encolar(**kw):
            encolados.append(kw)
            return "cola-1"

        cert = {
            "cert_serial": "5F3A", "cert_subject_cuit": "20123456789",
            "tsa_time": "2026-08-24T20:47:26+00:00",
        }
        sesiones = [{
            "session_id": "SES-A", "document_id": "doc-a", "user_id": "u1",
            "is_numerator": True, "number": "TOKEN-2026-1", "status": "waiting_batch",
            "reservation_id": "res-a", "file_id": "DATA-A",
            "cas_pre_done": True, "cert_payload": cert,
        }]

        with patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch("services.documents.signing.digital_completion.encolar_cierre_digital", _encolar):
            await bd._cerrar_tanda_completa("batch-1", sesiones, schema_name="100_test")

        assert encolados[0]["cert"] == cert, "la auditoría del certificado se perdía"

    @pytest.mark.asyncio
    async def test_el_cert_tambien_se_lee_cuando_viene_como_texto(self):
        from services.documents.signing import batch_digital as bd
        import json as _json

        encolados = []

        async def _encolar(**kw):
            encolados.append(kw)
            return "cola-1"

        cert = {"cert_serial": "5F3A"}
        sesiones = [{
            "session_id": "SES-A", "document_id": "doc-a", "user_id": "u1",
            "is_numerator": True, "number": "N-1", "status": "waiting_batch",
            "reservation_id": "res-a", "file_id": "DATA-A",
            "cas_pre_done": True, "cert_payload": _json.dumps(cert),
        }]

        with patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch("services.documents.signing.digital_completion.encolar_cierre_digital", _encolar):
            await bd._cerrar_tanda_completa("batch-1", sesiones, schema_name="100_test")

        assert encolados[0]["cert"] == cert

    @pytest.mark.asyncio
    async def test_un_cert_ilegible_no_frena_la_firma(self):
        from services.documents.signing import batch_digital as bd

        encolados = []

        async def _encolar(**kw):
            encolados.append(kw)
            return "cola-1"

        sesiones = [{
            "session_id": "SES-A", "document_id": "doc-a", "user_id": "u1",
            "is_numerator": True, "number": "N-1", "status": "waiting_batch",
            "reservation_id": "res-a", "file_id": "DATA-A",
            "cas_pre_done": True, "cert_payload": "{roto",
        }]

        with patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch("services.documents.signing.digital_completion.encolar_cierre_digital", _encolar):
            await bd._cerrar_tanda_completa("batch-1", sesiones, schema_name="100_test")

        assert len(encolados) == 1
        assert encolados[0]["cert"] == {}

    @pytest.mark.asyncio
    async def test_registrar_firma_guarda_lo_que_el_cierre_va_a_necesitar(self):
        from services.documents.signing import batch_digital as bd

        ejecutados = []

        async def _execute(sql, *args, **kw):
            ejecutados.append((sql, args))
            return "UPDATE 1"

        sesion = {
            "session_id": "SES-A", "batch_id": "batch-1", "schema_name": "100_test",
            "document_id": "doc-a", "user_id": "u1", "is_numerator": True,
            "number": "N-1", "reservation_id": "res-a", "file_id": "DATA-A",
            "cert": {"cert_serial": "AA11"}, "cas_pre_done": True,
        }

        with patch.object(bd, "execute", _execute), \
             patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=[
                 {**sesion, "status": "waiting_batch"},
                 {"session_id": "SES-B", "status": "pending", "document_id": "doc-b"},
             ])):
            r = await bd.registrar_firma_de_una(sesion)

        assert r["estado"] == "waiting_batch"
        sql, args = ejecutados[0]
        assert "cas_pre_done" in sql and "cert_payload" in sql, (
            "sin persistir esto, el cierre del último documento no tiene forma de "
            "saber lo que pasó en los polls de sus hermanas"
        )
        assert args[1] is True
        assert "AA11" in args[2]


class TestUnaTandaCaidaNoSeQuedaConLosRecursos:

    @pytest.mark.asyncio
    async def test_le_suelta_los_recursos_a_una_sesion_que_ya_estaba_caida(self):
        from services.documents.signing import batch_digital as bd

        liberados = []

        async def _release(*, schema_name, doc_id):
            liberados.append(doc_id)

        sesiones = [{
            "session_id": "SES-A", "document_id": "doc-a", "user_id": "u1",
            "is_numerator": False, "number": None, "reservation_id": None,
            "status": "failed", "failure_reason": "stale_reservation",
            "cancelled_at": None,
        }]

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado", AsyncMock()), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", _release):
            r = await bd.cancelar_tanda("batch-1", schema_name="100_test", motivo="x")

        assert liberados == ["doc-a"], (
            "sin esto el PDF se queda en inprocess/ y el documento no se puede "
            "volver a firmar nunca"
        )
        assert r["cancelled"] == 1

    @pytest.mark.asyncio
    async def test_no_la_limpia_dos_veces(self):
        from services.documents.signing import batch_digital as bd

        liberados = []

        async def _release(*, schema_name, doc_id):
            liberados.append(doc_id)

        sesiones = [{
            "session_id": "SES-A", "document_id": "doc-a", "user_id": "u1",
            "is_numerator": False, "number": None, "reservation_id": None,
            "status": "failed", "cancelled_at": "2026-08-24T20:47:29+00:00",
        }]

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", _release):
            r = await bd.cancelar_tanda("batch-1", schema_name="100_test", motivo="x")

        assert liberados == []
        assert r["cancelled"] == 0

    @pytest.mark.asyncio
    async def test_a_una_ya_firmada_no_se_le_toca_nada(self):
        from services.documents.signing import batch_digital as bd

        liberados = []

        async def _release(*, schema_name, doc_id):
            liberados.append(doc_id)

        sesiones = [{
            "session_id": "SES-A", "document_id": "doc-a", "user_id": "u1",
            "is_numerator": True, "number": "TOKEN-2026-1", "reservation_id": "r1",
            "status": "signed", "cancelled_at": None,
        }]

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", _release):
            r = await bd.cancelar_tanda("batch-1", schema_name="100_test", motivo="x")

        assert liberados == []
        assert r["cancelled"] == 0

    @pytest.mark.asyncio
    async def test_a_la_caida_no_se_le_pisa_su_motivo(self):
        from services.documents.signing import batch_digital as bd

        sqls = []

        async def _execute(sql, *args, **kw):
            sqls.append(sql)
            return "UPDATE 1"

        sesiones = [{
            "session_id": "SES-A", "document_id": "doc-a", "user_id": "u1",
            "is_numerator": False, "number": None, "reservation_id": None,
            "status": "failed", "failure_reason": "stale_reservation",
            "cancelled_at": None,
        }]

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", _execute), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado", AsyncMock()), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", AsyncMock()):
            await bd.cancelar_tanda("batch-1", schema_name="100_test", motivo="generico")

        assert any("cancelled_at = NOW()" in q and "failure_reason" not in q for q in sqls), (
            "a una sesión ya caída solo se le reclama la limpieza, no se le "
            "reescribe por qué murió"
        )


class TestNoTodoFalloHabilitaTirarLaTanda:

    def _resultado(self, motivo, puede_caer):
        return {"ok": False, "failure_reason": motivo,
                "tanda_puede_caer": puede_caer, "auto_link_results": []}

    @pytest.mark.asyncio
    async def test_el_fallo_de_upload_NO_tira_la_tanda(self):
        from services.documents.signing import digital_completion as dc
        from shared.exceptions import NumeratorUploadError

        with patch.object(dc, "_alertar", AsyncMock()):
            r = self._resultado("numerator_partial_failure", False)
        assert r["tanda_puede_caer"] is False

        import inspect
        fuente = inspect.getsource(dc)
        i = fuente.index("except NumeratorUploadError:")
        j = fuente.index("except DocumentRejectedWhileInQueueError:", i)
        assert '"tanda_puede_caer": False' in fuente[i:j], (
            "el fallo de upload deja el número CONFIRMING y el sweeper reencola "
            "ese mismo cierre: tirar la tanda le cancela el número a un reintento"
        )

    @pytest.mark.asyncio
    async def test_el_fallo_pre_cas_SI_la_tira(self):
        import inspect
        from services.documents.signing import digital_completion as dc

        fuente = inspect.getsource(dc)
        i = fuente.index("except NumeratorPreCasError:")
        j = fuente.index("except NumeratorUploadError:", i)
        assert '"tanda_puede_caer": True' in fuente[i:j], (
            "pre-CAS ya canceló el número y no subió nada: no hay qué proteger"
        )

    @pytest.mark.asyncio
    async def test_el_stale_si_la_tira(self):
        import inspect
        from services.documents.signing import digital_completion as dc

        fuente = inspect.getsource(dc)
        i = fuente.index("except StaleReservationError as exc:")
        assert '"tanda_puede_caer": True' in fuente[i:i + 2000]

    def test_el_worker_pregunta_antes_de_tirar(self):
        import inspect
        from workers import escri

        fuente = inspect.getsource(escri.EscriWorker._procesar_cierre_digital) \
            if hasattr(escri, "EscriWorker") and hasattr(escri.EscriWorker, "_procesar_cierre_digital") \
            else inspect.getsource(escri)
        assert 'resultado.get("tanda_puede_caer")' in fuente, (
            "sin el guard, un fallo de upload cancelaba la tanda entera por debajo "
            "de un reintento que el sweeper estaba por correr"
        )

    def test_el_camino_reintentable_no_toca_la_tanda(self):
        import inspect
        from workers import escri

        fuente = inspect.getsource(escri)
        i = fuente.index("escri.digital.failed session=%s doc=%s: %s")
        j = fuente.index("if not resultado.get(\"ok\"):", i)
        assert "_tirar_la_tanda_si_es_de_una" not in fuente[i:j], (
            "este camino se reintenta: cancelar sería tirarle el número y el "
            "lock a un cierre que está por retomar"
        )


class TestElNumeroVuelveAlPozoAunqueElTokenYaHayaFirmado:

    @pytest.mark.asyncio
    async def test_un_numero_en_confirming_se_cancela(self):
        sesiones = [_sesion(d, "waiting_batch") for d in DOCS]
        cancelar = AsyncMock(return_value=1)

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado", AsyncMock()), \
             patch("shared.numbering.cancel_number", cancelar), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", AsyncMock()):
            r = await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="uno_fallo")

        for llamada in cancelar.await_args_list:
            assert llamada.kwargs["from_states"] == ("RESERVED", "CONFIRMING"), (
                "sin CONFIRMING el número de un documento ya firmado no vuelve al pozo"
            )
        assert r["numeros_liberados"] == 3

    @pytest.mark.asyncio
    async def test_a_un_cierre_en_vuelo_no_se_le_cancela_el_numero(self):
        sesiones = [_sesion(DOCS[0], "completing")]
        cancelar, marcar = AsyncMock(return_value=1), AsyncMock(return_value="UPDATE 1")

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", marcar), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado", AsyncMock()), \
             patch("shared.numbering.cancel_number", cancelar), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", AsyncMock()):
            r = await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="cancelled_by_user")

        cancelar.assert_not_awaited()
        assert marcar.await_count == 1, "la marca de la sesión es el cerrojo: no se saltea"
        assert r["numeros_liberados"] == 0

    @pytest.mark.asyncio
    async def test_cero_filas_con_la_reserva_todavia_viva_grita(self):
        sesiones = [_sesion(DOCS[0], "waiting_batch")]
        registro = MagicMock()

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch.object(bd, "log", registro), \
             patch.object(bd, "fetch_one",
                          AsyncMock(return_value={"reservation_status": "RESERVED"})), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado", AsyncMock()), \
             patch("shared.numbering.cancel_number", AsyncMock(return_value=0)), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", AsyncMock()):
            r = await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="x")

        assert r["numeros_liberados"] == 0
        assert r["cancelled"] == 1, "la sesión sí se dio de baja: son dos cuentas distintas"
        assert registro.error.called, "un número que no se libera tiene que gritar"
        assert "tanda.numero_no_liberado" in registro.error.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cero_filas_porque_otro_ya_lo_libero_NO_grita(self):
        for estado in ("CANCELLED", "CONFIRMED"):
            sesiones = [_sesion(DOCS[0], "waiting_batch")]
            registro = MagicMock()

            with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
                 patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
                 patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
                 patch.object(bd, "log", registro), \
                 patch.object(bd, "fetch_one",
                              AsyncMock(return_value={"reservation_status": estado})), \
                 patch("services.documents.signing.digital_completion.borrar_pdf_firmado", AsyncMock()), \
                 patch("shared.numbering.cancel_number", AsyncMock(return_value=0)), \
                 patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", AsyncMock()):
                r = await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="x")

            assert r["numeros_liberados"] == 0
            assert not registro.error.called, (
                f"reserva en {estado}: el número YA está resuelto, gritar es ruido"
            )
            assert registro.info.called

    @pytest.mark.asyncio
    async def test_si_no_se_puede_leer_el_estado_se_asume_lo_peor(self):
        sesiones = [_sesion(DOCS[0], "waiting_batch")]
        registro = MagicMock()

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch.object(bd, "log", registro), \
             patch.object(bd, "fetch_one", AsyncMock(side_effect=RuntimeError("base caída"))), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado", AsyncMock()), \
             patch("shared.numbering.cancel_number", AsyncMock(return_value=0)), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", AsyncMock()):
            await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="x")

        assert registro.error.called, "sin poder leer el estado, se grita"

    @pytest.mark.asyncio
    async def test_una_excepcion_al_cancelar_no_suma_numeros_ni_frena(self):
        sesiones = [_sesion(DOCS[0], "waiting_batch"), _sesion(DOCS[1], "waiting_batch")]
        soltar = AsyncMock()

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado", AsyncMock()), \
             patch("shared.numbering.cancel_number",
                   AsyncMock(side_effect=RuntimeError("base caída"))), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", soltar):
            r = await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="x")

        assert r["numeros_liberados"] == 0
        assert soltar.await_count == 2, "un fallo al cancelar no puede dejar locks tomados"

    @pytest.mark.asyncio
    async def test_signed_sigue_intacta(self):
        sesiones = [_sesion(DOCS[0], "signed"), _sesion(DOCS[1], "waiting_batch")]
        cancelar = AsyncMock(return_value=1)

        with patch.object(bd, "_sesiones_de_la_tanda", AsyncMock(return_value=sesiones)), \
             patch.object(bd, "execute", AsyncMock(return_value="UPDATE 1")), \
             patch.object(bd, "_limpiar_manifiesto", AsyncMock()), \
             patch("services.documents.signing.digital_completion.borrar_pdf_firmado", AsyncMock()), \
             patch("shared.numbering.cancel_number", cancelar), \
             patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail", AsyncMock()):
            r = await bd.cancelar_tanda(TANDA, schema_name=SCHEMA, motivo="x")

        assert cancelar.await_count == 1
        assert cancelar.await_args.args[0] == DOCS[1]
        assert r["numeros_liberados"] == 1

    def test_la_llamada_lleva_los_dos_estados(self):
        import inspect

        fuente = inspect.getsource(bd.cancelar_tanda)
        assert '"RESERVED", "CONFIRMING"' in fuente, (
            "sin from_states explícito, cancelar_tanda vuelve al default y los "
            "números de todo lo que el token ya firmó quedan colgados 30 minutos"
        )
        assert 'if s["status"] == "completing":' in fuente, (
            "la guarda del cierre en vuelo es lo único que impide cancelarle el "
            "número a un documento que se está subiendo al bucket inmutable"
        )
