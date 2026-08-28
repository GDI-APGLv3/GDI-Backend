import pytest
from unittest.mock import AsyncMock, patch

from services.documents.signing import unified_signing
from services.documents.signing.queue_signals import SenalesCola
from shared.exceptions import EscriQueueFullError


def _senales(*, activos=0, tenant=None, edad_s=0.0, drenadas_5min=50, p90=None,
             municipios=1, edad_tenant_s=None):
    tenant = activos if tenant is None else tenant
    if edad_tenant_s is None:
        edad_tenant_s = edad_s if tenant > 0 else 0.0
    return SenalesCola(
        activos_global=activos,
        activos_tenant=tenant,
        municipios_con_cola=municipios,
        edad_mas_vieja_s=edad_s,
        edad_mas_vieja_tenant_s=edad_tenant_s,
        drenadas_5min=drenadas_5min,
        p90_espera_s=p90,
        medido_en=0.0,
    )


async def _chequear(senales, *, max_tenant=30, max_global=150, degraded=100):
    with (
        patch("services.documents.signing.queue_signals.medir_cola",
              AsyncMock(return_value=senales)),
        patch.object(unified_signing, "ESCRI_QUEUE_MAX_GLOBAL", max_global),
        patch.object(unified_signing, "ESCRI_QUEUE_DEGRADED_THRESHOLD", degraded),
        patch.object(unified_signing, "ESCRI_QUEUE_MAX_PER_TENANT", max_tenant),
        patch.object(unified_signing, "_maybe_alert_degraded", AsyncMock()),
    ):
        try:
            await unified_signing._check_escri_queue_capacity(schema_name="100_test")
            return None
        except EscriQueueFullError as e:
            return e


class TestProyeccionDeEspera:

    def test_la_espera_suma_lo_que_ya_espera_mas_lo_que_falta_drenar(self):
        s = _senales(activos=60, edad_s=60.0, drenadas_5min=60)
        assert s.ritmo_por_min == 12.0
        assert s.espera_proyectada_s() == pytest.approx(60 + 300)

    def test_la_misma_cola_con_el_doble_de_ritmo_espera_la_mitad(self):
        lenta = _senales(activos=30, drenadas_5min=30)
        rapida = _senales(activos=30, drenadas_5min=60)
        assert lenta.espera_proyectada_s() == 2 * rapida.espera_proyectada_s()

    def test_sin_ritmo_medido_usa_un_fallback_conservador(self):
        s = _senales(activos=1, edad_s=0.0, drenadas_5min=0)
        assert not s.worker_muerto
        assert s.espera_proyectada_s() > 0

    def test_el_resumen_dice_municipio_cantidad_edad_y_ritmo(self):
        s = _senales(activos=22, edad_s=240.0, drenadas_5min=60)
        texto = s.resumen("144_yyxy")
        assert "144_yyxy" in texto
        assert "22 firmas esperando" in texto
        assert "4 min" in texto
        assert "12/min" in texto


class TestWorkerMuerto:

    def test_ritmo_cero_con_cola_vieja_es_worker_muerto(self):
        assert _senales(activos=3, edad_s=300.0, drenadas_5min=0).worker_muerto

    def test_cola_recien_encolada_no_es_worker_muerto(self):
        assert not _senales(activos=3, edad_s=5.0, drenadas_5min=0).worker_muerto

    def test_cola_vacia_no_es_worker_muerto(self):
        assert not _senales(activos=0, edad_s=0.0, drenadas_5min=0).worker_muerto


class TestCriteriosDeAceptacion:

    @pytest.mark.asyncio
    async def test_ca1_acepta_con_cola_larga_si_drena_rapido(self):
        err = await _chequear(
            _senales(activos=40, edad_s=2.0, drenadas_5min=600),
            max_tenant=500,
        )
        assert err is None

    @pytest.mark.asyncio
    async def test_ca2_rechaza_cuando_la_espera_proyectada_supera_el_sla(self):
        err = await _chequear(
            _senales(activos=20, edad_s=120.0, drenadas_5min=10),
            max_tenant=500,
        )
        assert err is not None
        assert err.reason == "wait_over_sla"
        assert err.retry_after >= 10

    @pytest.mark.asyncio
    async def test_ca3_worker_muerto_con_tres_firmas_rechaza(self):
        err = await _chequear(_senales(activos=3, edad_s=600.0, drenadas_5min=0))
        assert err is not None
        assert err.reason == "dead_worker"

    @pytest.mark.asyncio
    async def test_el_tope_de_municipio_sigue_como_guarda(self):
        err = await _chequear(_senales(activos=40, tenant=30, edad_s=1.0, drenadas_5min=600))
        assert err is not None
        assert err.reason == "tenant_cap"

    @pytest.mark.asyncio
    async def test_el_retry_after_no_manda_a_chocar_contra_el_mismo_429(self):
        err = await _chequear(
            _senales(activos=100, edad_s=300.0, drenadas_5min=10),
            max_tenant=500, max_global=5000, degraded=4000,
        )
        assert err.reason == "wait_over_sla"
        assert err.retry_after > 60


class TestAlertaPorMunicipio:

    @pytest.mark.asyncio
    async def test_ca4_el_mail_nombra_al_municipio_trabado(self):
        from workers import sweeper_escri

        mail = AsyncMock()
        with (
            patch("services.documents.signing.queue_signals.medir_cola",
                  AsyncMock(return_value=_senales(activos=22, edad_s=600.0, drenadas_5min=0))),
            patch.object(sweeper_escri, "send_alert_mail", mail),
            patch.object(sweeper_escri, "_tomar_turno_de_alerta", AsyncMock(return_value=True)),
        ):
            await sweeper_escri._alertar_cola_trabada("144_yyxy")

        mail.assert_awaited_once()
        kwargs = mail.call_args.kwargs
        assert "144_yyxy" in kwargs["subject"]
        assert "144_yyxy" in kwargs["body"]
        assert "22 firmas esperando" in kwargs["body"]

    @pytest.mark.asyncio
    async def test_no_alerta_si_el_municipio_esta_sano(self):
        from workers import sweeper_escri

        mail = AsyncMock()
        with (
            patch("services.documents.signing.queue_signals.medir_cola",
                  AsyncMock(return_value=_senales(activos=5, edad_s=2.0, drenadas_5min=600))),
            patch.object(sweeper_escri, "send_alert_mail", mail),
            patch.object(sweeper_escri, "_limpiar_incidente_cola", AsyncMock()),
        ):
            await sweeper_escri._alertar_cola_trabada("100_test")

        mail.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ca7_al_destrabarse_se_limpia_el_incidente(self):
        from workers import sweeper_escri

        limpiar = AsyncMock()
        with (
            patch("services.documents.signing.queue_signals.medir_cola",
                  AsyncMock(return_value=_senales(activos=0, edad_s=0.0, drenadas_5min=600))),
            patch.object(sweeper_escri, "_limpiar_incidente_cola", limpiar),
        ):
            await sweeper_escri._alertar_cola_trabada("100_test")

        limpiar.assert_awaited_once_with("100_test")

    @pytest.mark.asyncio
    async def test_ca5_el_cooldown_corta_el_segundo_mail(self):
        from workers import sweeper_escri

        mail = AsyncMock()
        with (
            patch("services.documents.signing.queue_signals.medir_cola",
                  AsyncMock(return_value=_senales(activos=22, edad_s=600.0, drenadas_5min=0))),
            patch.object(sweeper_escri, "send_alert_mail", mail),
            patch.object(sweeper_escri, "_tomar_turno_de_alerta", AsyncMock(return_value=False)),
        ):
            await sweeper_escri._alertar_cola_trabada("144_yyxy")

        mail.assert_not_awaited()

    def test_ca6_el_incidente_es_por_municipio_y_ambiente(self):
        from workers import sweeper_escri

        a = sweeper_escri._incident_key("144_yyxy")
        b = sweeper_escri._incident_key("100_test")
        assert a != b
        assert a.startswith("signing-queue:")
        assert a.endswith(":144_yyxy")


class TestNoSeLeCobraLaColaAjena:

    @pytest.mark.asyncio
    async def test_un_municipio_sin_cola_no_paga_la_saturacion_de_otro(self):
        err = await _chequear(
            _senales(activos=200, tenant=0, edad_s=600.0, drenadas_5min=20, municipios=2),
            max_tenant=500, max_global=5000, degraded=4000,
        )
        assert err is None, f"lo frenó por la cola de otro: {err and err.reason}"

    def test_la_proyeccion_del_municipio_reparte_el_ritmo_entre_los_que_esperan(self):
        s = _senales(activos=40, tenant=10, edad_s=0.0, drenadas_5min=60, municipios=4)
        assert s.espera_proyectada_s(del_tenant=True) == pytest.approx(200.0)

    def test_con_un_solo_municipio_en_cola_no_se_reparte_nada(self):
        s = _senales(activos=10, tenant=10, edad_s=0.0, drenadas_5min=60, municipios=1)
        assert s.espera_proyectada_s(del_tenant=True) == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_su_propia_cola_larga_si_lo_frena(self):
        err = await _chequear(
            _senales(activos=30, tenant=30, edad_s=120.0, drenadas_5min=10, municipios=1),
            max_tenant=500,
        )
        assert err is not None
        assert err.reason == "wait_over_sla"
