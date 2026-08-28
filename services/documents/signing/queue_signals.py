import json
import time
from dataclasses import dataclass, asdict

from starlette.concurrency import run_in_threadpool

from config.constants import (
    ESCRI_QUEUE_SLA_SECONDS,
    ESCRI_QUEUE_SIGNALS_CACHE_SECONDS,
    ESCRI_QUEUE_DEAD_WORKER_MIN_AGE_SECONDS,
)
from database import fetch_one
from shared.logging import get_logger

log = get_logger(__name__)

_CACHE_KEY = "escri:queue_signals"

_RITMO_FALLBACK_POR_MIN = 6.0


@dataclass(frozen=True)
class SenalesCola:

    activos_global: int
    activos_tenant: int
    municipios_con_cola: int
    edad_mas_vieja_s: float
    edad_mas_vieja_tenant_s: float
    drenadas_5min: int
    p90_espera_s: float | None
    medido_en: float


    @property
    def ritmo_por_min(self) -> float:
        if self.drenadas_5min > 0:
            return self.drenadas_5min / 5.0
        return 0.0

    @property
    def worker_muerto(self) -> bool:
        return (
            self.activos_global > 0
            and self.drenadas_5min == 0
            and self.edad_mas_vieja_s >= ESCRI_QUEUE_DEAD_WORKER_MIN_AGE_SECONDS
        )

    def espera_proyectada_s(self, *, del_tenant: bool = False) -> float:
        ritmo = self.ritmo_por_min or _RITMO_FALLBACK_POR_MIN
        if del_tenant:
            reparto = max(1, self.municipios_con_cola)
            return self.edad_mas_vieja_tenant_s + (
                self.activos_tenant / (ritmo / reparto)
            ) * 60.0
        return self.edad_mas_vieja_s + (self.activos_global / ritmo) * 60.0

    @property
    def supera_sla(self) -> bool:
        return self.espera_proyectada_s(del_tenant=True) > ESCRI_QUEUE_SLA_SECONDS

    def resumen(self, schema_name: str | None = None) -> str:
        quien = f"{schema_name}: " if schema_name else ""
        activos = self.activos_tenant if schema_name else self.activos_global
        edad = self.edad_mas_vieja_tenant_s if schema_name else self.edad_mas_vieja_s
        return (
            f"{quien}{activos} firmas esperando, "
            f"la más vieja hace {edad / 60:.0f} min, "
            f"drena {self.ritmo_por_min:.0f}/min"
        )


async def _medir(schema_name: str) -> SenalesCola:
    row = await fetch_one(
        """
        WITH activos AS (
            SELECT schema_name, status, created_at
            FROM public.signing_sessions
            WHERE job_type IN ('sign', 'sign_common')
              AND status IN ('pending', 'processing')
        ),
        esperas AS (
            SELECT EXTRACT(EPOCH FROM (claimed_at - created_at)) AS espera
            FROM public.signing_sessions
            WHERE job_type IN ('sign', 'sign_common')
              AND claimed_at IS NOT NULL
              AND claimed_at > NOW() - INTERVAL '15 minutes'
        )
        SELECT
            (SELECT count(*) FROM activos)                              AS activos_global,
            (SELECT count(DISTINCT schema_name) FROM activos)            AS municipios_con_cola,
            (SELECT count(*) FROM activos WHERE schema_name = $1)       AS activos_tenant,
            (SELECT COALESCE(EXTRACT(EPOCH FROM (NOW() - min(created_at))), 0)
               FROM activos WHERE status = 'pending')                   AS edad_global,
            (SELECT COALESCE(EXTRACT(EPOCH FROM (NOW() - min(created_at))), 0)
               FROM activos WHERE status = 'pending' AND schema_name = $1) AS edad_tenant,
            (SELECT count(*) FROM public.signing_sessions
              WHERE job_type IN ('sign', 'sign_common')
                AND status = 'signed'
                AND updated_at > NOW() - INTERVAL '5 minutes')          AS drenadas_5min,
            (SELECT percentile_disc(0.9) WITHIN GROUP (ORDER BY espera)
               FROM esperas)                                            AS p90
        """,
        schema_name,
        schema_name="public",
    )
    if row is None:
        return SenalesCola(0, 0, 0, 0.0, 0.0, 0, None, time.time())

    p90 = row["p90"]
    return SenalesCola(
        activos_global=int(row["activos_global"] or 0),
        activos_tenant=int(row["activos_tenant"] or 0),
        municipios_con_cola=int(row["municipios_con_cola"] or 0),
        edad_mas_vieja_s=float(row["edad_global"] or 0),
        edad_mas_vieja_tenant_s=float(row["edad_tenant"] or 0),
        drenadas_5min=int(row["drenadas_5min"] or 0),
        p90_espera_s=float(p90) if p90 is not None else None,
        medido_en=time.time(),
    )


async def medir_cola(*, schema_name: str, usar_cache: bool = True) -> SenalesCola:
    if not usar_cache:
        return await _medir(schema_name)

    from services.cache import get_redis

    key = f"{_CACHE_KEY}:{schema_name}"
    client = get_redis()

    if client is not None:
        try:
            crudo = await run_in_threadpool(client.get, key)
            if crudo:
                datos = json.loads(crudo)
                esperadas = SenalesCola.__dataclass_fields__.keys()
                if datos.keys() == esperadas:
                    return SenalesCola(**datos)
                log.debug("GDI-257: cache con formato viejo — se vuelve a medir")
        except Exception as exc:  # noqa: BLE001 — fail-open a propósito
            log.debug("GDI-257: cache de señales no disponible (%s), midiendo", exc)

    senales = await _medir(schema_name)

    if client is not None:
        try:
            await run_in_threadpool(
                client.setex, key, ESCRI_QUEUE_SIGNALS_CACHE_SECONDS,
                json.dumps(asdict(senales)),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("GDI-257: no se pudo cachear las señales (%s)", exc)

    return senales
