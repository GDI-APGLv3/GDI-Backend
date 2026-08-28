
import pytest
from unittest.mock import AsyncMock, patch

from services.documents.signing import unified_signing
from shared.exceptions import EscriQueueFullError


def _senales(global_n: int, tenant_n: int):
    from services.documents.signing.queue_signals import SenalesCola
    return SenalesCola(
        activos_global=global_n,
        activos_tenant=tenant_n,
        municipios_con_cola=1,
        edad_mas_vieja_s=1.0,
        edad_mas_vieja_tenant_s=1.0,
        drenadas_5min=5000,
        p90_espera_s=2.0,
        medido_en=0.0,
    )


async def _run(global_n, tenant_n, *, max_global, degraded, max_tenant):
    with (
        patch("services.documents.signing.queue_signals.medir_cola",
              AsyncMock(return_value=_senales(global_n, tenant_n))),
        patch.object(unified_signing, "ESCRI_QUEUE_MAX_GLOBAL", max_global),
        patch.object(unified_signing, "ESCRI_QUEUE_DEGRADED_THRESHOLD", degraded),
        patch.object(unified_signing, "ESCRI_QUEUE_MAX_PER_TENANT", max_tenant),
        patch.object(unified_signing, "_maybe_alert_degraded", AsyncMock()) as alert,
    ):
        try:
            await unified_signing._check_escri_queue_capacity(schema_name="100_test")
            return None, alert
        except EscriQueueFullError as e:
            return e, alert


class TestTopesSeEvaluanPorSeparado:

    @pytest.mark.asyncio
    async def test_tope_global_se_alcanza_con_defaults(self):
        err, _ = await _run(150, 5, max_global=150, degraded=100, max_tenant=30)
        assert err is not None
        assert err.reason == "global_cap"

    @pytest.mark.asyncio
    async def test_entre_ambos_umbrales_corta_por_degradado(self):
        err, _ = await _run(120, 5, max_global=150, degraded=100, max_tenant=30)
        assert err is not None
        assert err.reason == "degraded_threshold"

    @pytest.mark.asyncio
    async def test_tope_global_menor_al_degradado_tambien_manda(self):
        err, _ = await _run(60, 5, max_global=50, degraded=100, max_tenant=30)
        assert err is not None
        assert err.reason == "global_cap"

    @pytest.mark.asyncio
    async def test_tope_por_tenant_sigue_vivo(self):
        err, _ = await _run(10, 30, max_global=150, degraded=100, max_tenant=30)
        assert err is not None
        assert err.reason == "tenant_cap"

    @pytest.mark.asyncio
    async def test_cola_holgada_no_corta(self):
        err, _ = await _run(10, 3, max_global=150, degraded=100, max_tenant=30)
        assert err is None


class TestAlertaSeparadaDelCorte:

    @pytest.mark.asyncio
    async def test_alerta_aunque_corte_el_tope_global(self):
        _, alert = await _run(150, 5, max_global=150, degraded=100, max_tenant=30)
        alert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alerta_en_el_umbral_degradado(self):
        _, alert = await _run(120, 5, max_global=150, degraded=100, max_tenant=30)
        alert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_alerta_por_debajo_del_umbral(self):
        _, alert = await _run(99, 5, max_global=150, degraded=100, max_tenant=30)
        alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_alerta_por_saturacion_de_un_solo_tenant(self):
        _, alert = await _run(10, 30, max_global=150, degraded=100, max_tenant=30)
        alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rearma_el_antispam_al_bajar_la_cola(self):
        with patch.object(unified_signing, "_reset_degraded_alert_if_recovered") as reset:
            await _run(50, 5, max_global=150, degraded=100, max_tenant=30)
            reset.assert_called_once_with(50)

    @pytest.mark.asyncio
    async def test_no_rearma_mientras_sigue_saturada(self):
        with patch.object(unified_signing, "_reset_degraded_alert_if_recovered") as reset:
            await _run(120, 5, max_global=150, degraded=100, max_tenant=30)
            reset.assert_not_called()
