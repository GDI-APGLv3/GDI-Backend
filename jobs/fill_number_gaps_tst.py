
import asyncio
from shared.logging import get_logger
from datetime import datetime, timezone

from config.constants import (
    MAX_TST_PER_RUN,
    TST_THROTTLE_SECONDS,
    TST_SWEEP_HOUR_1,
    TST_SWEEP_MINUTE_1,
    TST_SWEEP_HOUR_2,
    TST_SWEEP_MINUTE_2,
)

log = get_logger(__name__)


async def _find_gaps(schema_name: str, max_n: int) -> list[dict]:
    from database import fetch_all
    year = datetime.now(timezone.utc).year
    rows = await fetch_all(
        """
        SELECT od.id,
               od.global_sequence,
               od.official_number     AS original_official_number,
               od.department_id,
               od.year,
               od.reservation_id
          FROM official_documents od
         WHERE od.reservation_status = 'CANCELLED'
           AND od.numbering_regime   = 'GLOBAL'
           AND od.year               = $1
           AND NOT EXISTS (
                   SELECT 1
                     FROM official_documents od2
                    WHERE od2.global_sequence = od.global_sequence
                      AND od2.year            = od.year
                      AND od2.reservation_status IN ('CONFIRMED', 'RESERVED', 'CONFIRMING')
               )
         ORDER BY od.global_sequence ASC
         LIMIT $2
        """,
        year,
        max_n,
        schema_name=schema_name,
    )
    return [dict(r) for r in rows]


async def _sweep_tenant(schema_name: str) -> dict:
    from services.documents.creation.tst_creator import create_tst_document_signed_by_system
    from services.shared.notary_breaker import breaker_status

    result = {
        "schema_name":    schema_name,
        "gaps_found":     0,
        "tst_created":    0,
        "tst_errors":     0,
        "skipped_breaker": False,
    }

    gaps = await _find_gaps(schema_name=schema_name, max_n=MAX_TST_PER_RUN)
    result["gaps_found"] = len(gaps)

    if not gaps:
        return result

    cb_status = await breaker_status()
    if cb_status["state"] != "CLOSED":
        log.warning(
            "tst_sweep.notary_breaker_open skip tenant=%s gaps=%d",
            schema_name, len(gaps),
        )
        result["skipped_breaker"] = True
        return result

    for gap_row in gaps:
        try:
            official_number = await create_tst_document_signed_by_system(
                gap_row,
                schema_name=schema_name,
            )
            result["tst_created"] += 1
            log.info(
                "tst_sweep.gap_filled number=%s schema=%s",
                official_number, schema_name,
            )
        except RuntimeError as race_err:
            log.info("tst_sweep.race_skip schema=%s: %s", schema_name, race_err)
        except Exception as err:
            result["tst_errors"] += 1
            log.error(
                "tst_sweep.gap_error schema=%s seq=%s: %s",
                schema_name,
                gap_row.get("global_sequence"),
                err,
                exc_info=True,
            )

        if TST_THROTTLE_SECONDS > 0:
            await asyncio.sleep(TST_THROTTLE_SECONDS)

    return result


async def _run_sweep() -> None:
    from shared.alerts import send_alert_mail
    from shared.tenant_validation import get_valid_schemas

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log.info("tst_sweep.run_start ts=%s", run_ts)

    all_schemas = await get_valid_schemas()
    tenant_schemas = [s for s in all_schemas if s not in ("public", "100_test")]

    total_gaps    = 0
    total_created = 0
    total_errors  = 0
    breaker_skips = 0

    tenant_lines: list[str] = []

    for schema_name in tenant_schemas:
        try:
            res = await _sweep_tenant(schema_name)
            total_gaps    += res["gaps_found"]
            total_created += res["tst_created"]
            total_errors  += res["tst_errors"]
            if res["skipped_breaker"]:
                breaker_skips += 1

            if res["gaps_found"] > 0 or res["tst_created"] > 0 or res["tst_errors"] > 0:
                line = (
                    f"  {schema_name}: "
                    f"{res['gaps_found']} huecos "
                    f"| {res['tst_created']} creados "
                    f"| {res['tst_errors']} errores"
                )
                if res["skipped_breaker"]:
                    line += " | NOTARY CAÍDO (huecos pendientes)"
                tenant_lines.append(line)

        except Exception as tenant_err:
            log.error("tst_sweep.tenant_error schema=%s: %s", schema_name, tenant_err, exc_info=True)
            tenant_lines.append(f"  {schema_name}: ERROR — {tenant_err}")

    log.info(
        "tst_sweep.run_done gaps=%d created=%d errors=%d breaker_skips=%d",
        total_gaps, total_created, total_errors, breaker_skips,
    )

    status_tag = "OK"
    if total_errors > 0:
        status_tag = "ERRORES"
    elif breaker_skips > 0:
        status_tag = "NOTARY CAÍDO"
    elif total_gaps == 0:
        status_tag = "SIN HUECOS"

    subject = f"[GDI TST] Barrido de huecos {run_ts} — {status_tag}"

    body_lines = [
        f"Barrido TST de numeración — {run_ts}",
        f"",
        f"Resumen global:",
        f"  Huecos detectados : {total_gaps}",
        f"  TST creados       : {total_created}",
        f"  Errores           : {total_errors}",
        f"  Tenants saltados (breaker Notary abierto): {breaker_skips}",
    ]

    if tenant_lines:
        body_lines += ["", "Detalle por tenant:"] + tenant_lines

    if breaker_skips > 0:
        body_lines += [
            "",
            "⚠ Notary estaba en mantenimiento (circuit breaker OPEN).",
            "  Los huecos marcados como pendientes se reintentarán en la próxima corrida.",
        ]

    if total_errors > 0:
        body_lines += [
            "",
            f"⚠ {total_errors} TST(s) no pudieron crearse por error. "
            "Revisar logs del backend.",
        ]

    await send_alert_mail(
        subject=subject,
        body="\n".join(body_lines),
        schema_name=None,
    )


async def run_tst_sweep() -> None:
    from shared.advisory_lock import global_job_lock, LOCK_ID_TST_SWEEP

    try:
        async with global_job_lock(LOCK_ID_TST_SWEEP, "tst_sweep") as got_lock:
            if not got_lock:
                return
            await _run_sweep()
    except Exception:
        log.exception("tst_sweep.fatal_error")


def schedule_tst_sweep(scheduler) -> None:
    scheduler.add_job(
        run_tst_sweep,
        "cron",
        hour=TST_SWEEP_HOUR_1,
        minute=TST_SWEEP_MINUTE_1,
        timezone="UTC",
        id="sweep_tst_run1",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_tst_sweep,
        "cron",
        hour=TST_SWEEP_HOUR_2,
        minute=TST_SWEEP_MINUTE_2,
        timezone="UTC",
        id="sweep_tst_run2",
        max_instances=1,
        coalesce=True,
    )
    log.info(
        "tst_sweep.scheduled cron1=%d:%02d UTC cron2=%d:%02d UTC",
        TST_SWEEP_HOUR_1, TST_SWEEP_MINUTE_1,
        TST_SWEEP_HOUR_2, TST_SWEEP_MINUTE_2,
    )
