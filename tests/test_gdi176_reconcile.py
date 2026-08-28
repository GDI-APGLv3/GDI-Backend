
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from jobs.reconcile_r2_db import _conciliar_tenant, _es_reciente

SCHEMA = "100_test"
AHORA = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
CORTE = AHORA - timedelta(hours=6)
VIEJO = AHORA - timedelta(days=3)
RECIENTE = AHORA - timedelta(hours=1)


def _obj(numero, last_modified=VIEJO, size=1024):
    return {"key": f"{numero}.pdf", "size": size, "last_modified": last_modified}


def _fila(numero, doc_id, estado="CONFIRMED", signed_at=VIEJO, updated_at=VIEJO):
    return {
        "official_number": numero,
        "doc_id": doc_id,
        "reservation_status": estado,
        "signed_at": signed_at,
        "updated_at": updated_at,
        "created_at": VIEJO,
    }


async def _correr(objetos_r2, filas_bd, truncado=False):
    async def _r2_list_por_bucket(schema_name, bucket, max_keys):
        if bucket == "oficial":
            return objetos_r2, truncado
        return [], False

    with patch("services.r2_client.r2_list",
               AsyncMock(side_effect=_r2_list_por_bucket)), \
         patch("database.fetch_all", AsyncMock(return_value=filas_bd)):
        return await _conciliar_tenant(SCHEMA, CORTE)


class TestVentanaDeGracia:
    def test_lo_reciente_esta_dentro(self):
        assert _es_reciente(RECIENTE, CORTE) is True

    def test_lo_viejo_esta_fuera(self):
        assert _es_reciente(VIEJO, CORTE) is False

    def test_sin_fecha_se_trata_como_viejo(self):
        assert _es_reciente(None, CORTE) is False

    def test_fecha_sin_zona_horaria_no_rompe(self):
        ingenua = (AHORA - timedelta(hours=1)).replace(tzinfo=None)
        assert _es_reciente(ingenua, CORTE) is True


@pytest.mark.asyncio
class TestPdfSinDocumento:

    async def test_pdf_sin_ninguna_fila_es_hallazgo(self):
        res = await _correr([_obj("DECRE-2026-00099-ARG")], [])
        assert len(res["hallazgos"]) == 1
        h = res["hallazgos"][0]
        assert h["kind"] == "pdf_sin_documento"
        assert h["official_number"] == "DECRE-2026-00099-ARG"
        assert h["document_id"] is None
        assert h["detail"]["estado_en_bd"] == "SIN FILA EN LA BASE"

    async def test_pdf_con_fila_en_transito_informa_el_estado(self):
        res = await _correr(
            [_obj("DECRE-2026-00100-ARG")],
            [_fila("DECRE-2026-00100-ARG", "d1", estado="CONFIRMING", signed_at=None)],
        )
        assert len(res["hallazgos"]) == 1
        h = res["hallazgos"][0]
        assert h["kind"] == "pdf_sin_documento"
        assert h["document_id"] == "d1"
        assert h["detail"]["estado_en_bd"] == "CONFIRMING"

    async def test_pdf_con_documento_oficial_no_es_hallazgo(self):
        res = await _correr(
            [_obj("DECRE-2026-00101-ARG")],
            [_fila("DECRE-2026-00101-ARG", "d1")],
        )
        assert res["hallazgos"] == []

    async def test_pdf_recien_subido_se_ignora(self):
        res = await _correr([_obj("DECRE-2026-00102-ARG", last_modified=RECIENTE)], [])
        assert res["hallazgos"] == []

    async def test_numero_reciclado_no_es_hallazgo(self):
        res = await _correr(
            [_obj("DECRE-2026-00103-ARG")],
            [
                _fila("DECRE-2026-00103-ARG", "viejo", estado="CANCELLED", signed_at=None),
                _fila("DECRE-2026-00103-ARG", "nuevo", estado="CONFIRMED"),
            ],
        )
        assert res["hallazgos"] == []

    async def test_documento_viejo_sin_reservation_status_cuenta_como_oficial(self):
        res = await _correr(
            [_obj("DECRE-2024-00001-ARG")],
            [_fila("DECRE-2024-00001-ARG", "d1", estado=None, signed_at=VIEJO)],
        )
        assert res["hallazgos"] == []

    async def test_objetos_que_no_son_pdf_se_ignoran(self):
        res = await _correr(
            [{"key": "backup.zip", "size": 10, "last_modified": VIEJO}], []
        )
        assert res["hallazgos"] == []


@pytest.mark.asyncio
class TestDocumentoSinPdf:

    async def test_documento_firmado_sin_pdf_es_hallazgo(self):
        res = await _correr([], [_fila("DECRE-2026-00200-ARG", "d9")])
        assert len(res["hallazgos"]) == 1
        h = res["hallazgos"][0]
        assert h["kind"] == "documento_sin_pdf"
        assert h["document_id"] == "d9"

    async def test_documento_recien_firmado_se_ignora(self):
        res = await _correr(
            [], [_fila("DECRE-2026-00201-ARG", "d9", signed_at=RECIENTE)]
        )
        assert res["hallazgos"] == []

    async def test_reserva_sin_firmar_no_deberia_tener_pdf(self):
        res = await _correr(
            [], [_fila("DECRE-2026-00202-ARG", "d9", estado="RESERVED", signed_at=None)]
        )
        assert res["hallazgos"] == []

    async def test_con_listado_truncado_no_se_reporta_falta_de_pdf(self):
        res = await _correr(
            [], [_fila("DECRE-2026-00203-ARG", "d9")], truncado=True
        )
        assert res["hallazgos"] == []
        assert res["truncado"] is True

    async def test_con_listado_truncado_si_se_reportan_los_pdf_huerfanos(self):
        res = await _correr([_obj("DECRE-2026-00204-ARG")], [], truncado=True)
        assert len(res["hallazgos"]) == 1
        assert res["hallazgos"][0]["kind"] == "pdf_sin_documento"

    async def test_import_masivo_con_firma_historica_no_alerta(self):
        fila = {
            "official_number": "DECRE-2015-00042-BBL",
            "doc_id": "importado",
            "reservation_status": "CONFIRMED",
            "signed_at": datetime(2015, 3, 14, tzinfo=timezone.utc),
            "updated_at": RECIENTE,
            "created_at": RECIENTE,
        }
        res = await _correr([], [fila])
        assert res["hallazgos"] == []

    async def test_import_viejo_sin_pdf_si_alerta(self):
        fila = {
            "official_number": "DECRE-2015-00043-BBL",
            "doc_id": "importado-viejo",
            "reservation_status": "CONFIRMED",
            "signed_at": datetime(2015, 3, 14, tzinfo=timezone.utc),
            "updated_at": VIEJO,
            "created_at": VIEJO,
        }
        res = await _correr([], [fila])
        assert len(res["hallazgos"]) == 1
        assert res["hallazgos"][0]["kind"] == "documento_sin_pdf"

    async def test_updated_at_sin_zona_horaria_tampoco_rompe(self):
        fila = {
            "official_number": "DECRE-2015-00044-BBL",
            "doc_id": "mixto",
            "reservation_status": "CONFIRMED",
            "signed_at": datetime(2015, 3, 14),
            "updated_at": RECIENTE.replace(tzinfo=None),
            "created_at": None,
        }
        res = await _correr([], [fila])
        assert res["hallazgos"] == []


@pytest.mark.asyncio
class TestCruceCompleto:

    async def test_detecta_las_dos_direcciones_en_una_corrida(self):
        res = await _correr(
            [_obj("A-1"), _obj("A-2")],
            [_fila("A-2", "ok"), _fila("B-1", "sin-pdf")],
        )
        tipos = sorted(h["kind"] for h in res["hallazgos"])
        assert tipos == ["documento_sin_pdf", "pdf_sin_documento"]
        assert res["objetos_r2"] == 2
        assert res["oficiales_bd"] == 2

    async def test_tenant_sano_no_genera_nada(self):
        res = await _correr(
            [_obj("A-1"), _obj("A-2")],
            [_fila("A-1", "d1"), _fila("A-2", "d2")],
        )
        assert res["hallazgos"] == []


@pytest.mark.asyncio
class TestPersistenciaYAlertas:

    async def test_solo_se_alerta_lo_que_nunca_se_alerto(self):
        from jobs.reconcile_r2_db import _persistir

        hallazgos = [
            {"kind": "pdf_sin_documento", "official_number": "NUEVO",
             "document_id": None, "detail": {}},
            {"kind": "pdf_sin_documento", "official_number": "YA-AVISADO",
             "document_id": None, "detail": {}},
        ]
        respuestas = [
            [{"id": "11111111-1111-1111-1111-111111111111", "alerted_at": None}],
            [{"id": "22222222-2222-2222-2222-222222222222", "alerted_at": AHORA}],
        ]
        with patch("database.fetch_all", AsyncMock(side_effect=respuestas)), \
             patch("database.execute", AsyncMock(return_value="UPDATE 1")) as ex:
            nuevos = await _persistir(SCHEMA, hallazgos)

        assert [n["official_number"] for n in nuevos] == ["NUEVO"]
        assert ex.await_args.args[1] == ["11111111-1111-1111-1111-111111111111"]

    async def test_sin_hallazgos_nuevos_no_toca_la_tabla_de_alertas(self):
        from jobs.reconcile_r2_db import _persistir

        with patch("database.fetch_all",
                   AsyncMock(return_value=[{"id": "33333333-3333-3333-3333-333333333333",
                                            "alerted_at": AHORA}])), \
             patch("database.execute", AsyncMock()) as ex:
            nuevos = await _persistir(SCHEMA, [
                {"kind": "documento_sin_pdf", "official_number": "X",
                 "document_id": None, "detail": {}},
            ])

        assert nuevos == []
        ex.assert_not_awaited()

    async def test_lo_que_dejo_de_aparecer_se_cierra(self):
        from jobs.reconcile_r2_db import _marcar_resueltos

        with patch("database.execute", AsyncMock(return_value="UPDATE 3")) as ex:
            cerrados = await _marcar_resueltos(SCHEMA, [
                {"kind": "pdf_sin_documento", "official_number": "SIGUE-ABIERTO"},
            ])

        assert cerrados == 3
        assert ex.await_args.args[2] == ["pdf_sin_documento|SIGUE-ABIERTO"]

    async def test_sin_hallazgos_se_cierran_todos_los_del_tenant(self):
        from jobs.reconcile_r2_db import _marcar_resueltos

        with patch("database.execute", AsyncMock(return_value="UPDATE 5")) as ex:
            cerrados = await _marcar_resueltos(SCHEMA, [])

        assert cerrados == 5
        assert ex.await_args.args[2] == []


class TestRegistroEnElScheduler:

    def test_se_registra_como_cron_con_max_instances(self):
        from unittest.mock import MagicMock
        from jobs.reconcile_r2_db import schedule_reconcile_r2_db, RECONCILE_HOUR

        sched = MagicMock()
        schedule_reconcile_r2_db(sched)
        kw = sched.add_job.call_args.kwargs
        assert kw["max_instances"] == 1
        assert kw["coalesce"] is True
        assert kw["timezone"] == "UTC"
        assert kw["hour"] == RECONCILE_HOUR

    def test_corre_despues_del_repaso_de_tst(self):
        from jobs.reconcile_r2_db import RECONCILE_HOUR, RECONCILE_MINUTE
        from config.constants import TST_SWEEP_HOUR_2, TST_SWEEP_MINUTE_2

        assert (RECONCILE_HOUR, RECONCILE_MINUTE) > (TST_SWEEP_HOUR_2, TST_SWEEP_MINUTE_2)


@pytest.mark.asyncio
class TestCandadoGlobal:

    async def test_solo_una_copia_concilia(self):
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock
        from jobs.reconcile_r2_db import run_reconcile_r2_db
        from shared.advisory_lock import LOCK_ID_RECONCILE_R2_DB

        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value=False)

        @asynccontextmanager
        async def _ctx(**kwargs):
            yield conn

        cuerpo = AsyncMock()
        with patch("shared.advisory_lock.get_conn", _ctx), \
             patch("jobs.reconcile_r2_db._run_reconcile", cuerpo):
            await run_reconcile_r2_db()

        cuerpo.assert_not_awaited()
        assert conn.fetchval.await_args.args[1] == LOCK_ID_RECONCILE_R2_DB
