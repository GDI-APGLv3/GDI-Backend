
from datetime import datetime, timezone

from shared.logging import get_logger
from shared.utils import payload_as_dict

from config.constants import (
    RECONCILE_ENABLED,
    MAX_RECONCILE_PER_RUN,
    RECONCILE_TS_SWEEP_HOUR_1,
    RECONCILE_TS_SWEEP_MINUTE_1,
    RECONCILE_TS_SWEEP_HOUR_2,
    RECONCILE_TS_SWEEP_MINUTE_2,
)

log = get_logger(__name__)


async def retry_failed_publications(schema_name: str) -> int:
    from database import fetch_all, execute
    from services.storage.cloudflare import get_tenant_r2_client
    from services.storage.publish_public import maybe_publish_official_pdf
    from fastapi.concurrency import run_in_threadpool

    rows = await fetch_all(
        """
        SELECT session_id::text, document_id::text, payload
          FROM public.signing_sessions
         WHERE schema_name = $1
           -- GDI-253 Fase 2: la publicación pública se movió al job 'sign'
           -- (sello inline, sin carril dts) — job_type='dts' sigue
           -- cubriendo lo legacy que todavía esté drenando.
           AND job_type    IN ('dts', 'sign')
           AND status      = 'signed'
           AND payload->>'publish_failed' = 'true'
         ORDER BY updated_at ASC
         LIMIT $2
        """,
        schema_name,
        MAX_RECONCILE_PER_RUN,
        schema_name="public",
    )
    if not rows:
        return 0

    r2 = await get_tenant_r2_client(schema_name=schema_name)
    republicados = 0
    for row in rows:
        session_id = row["session_id"]
        doc_id = row["document_id"]
        payload = payload_as_dict(row["payload"])
        official_number = payload.get("official_number")
        if not official_number:
            log.warning(
                "retry_publish.sin_official_number schema=%s session=%s",
                schema_name, session_id[:8],
            )
            continue
        try:
            pdf_bytes = await run_in_threadpool(r2.get_oficial_bytes, official_number)
            if not pdf_bytes:
                log.warning(
                    "retry_publish.pdf_not_found schema=%s doc=%s num=%s",
                    schema_name, doc_id[:8], official_number,
                )
                continue

            ok = await maybe_publish_official_pdf(
                schema_name=schema_name,
                official_number=official_number,
                document_id=doc_id,
                document_type_id=payload.get("document_type_id"),
                signed_pdf_bytes=pdf_bytes,
            )
            if not ok:
                continue

            new_payload = {k: v for k, v in payload.items() if k != "publish_failed"}
            await execute(
                """
                UPDATE public.signing_sessions
                SET payload = $1::jsonb, updated_at = NOW()
                WHERE session_id = $2::uuid
                """,
                new_payload,
                session_id,
                schema_name="public",
            )
            republicados += 1
            log.info(
                "retry_publish.republished schema=%s doc=%s num=%s",
                schema_name, doc_id[:8], official_number,
            )
        except Exception as exc:
            log.error(
                "retry_publish.failed schema=%s doc=%s num=%s error=%s",
                schema_name, doc_id[:8], official_number, exc,
            )

    return republicados


async def _run_retry_publications() -> None:
    from shared.alerts import send_alert_mail
    from shared.tenant_validation import get_valid_schemas

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log.info("retry_publish.run_start ts=%s", run_ts)

    all_schemas = await get_valid_schemas()
    tenant_schemas = [s for s in all_schemas if s not in ("public", "100_test")]
    if not tenant_schemas and "100_test" in all_schemas:
        tenant_schemas = ["100_test"]
        log.info("retry_publish.usando_100_test — es el único tenant de este ambiente")

    total_republicados = 0
    tenant_lines: list[str] = []
    hubo_error = False

    for schema_name in tenant_schemas:
        try:
            republicados = await retry_failed_publications(schema_name)
        except Exception as tenant_err:
            hubo_error = True
            log.error(
                "retry_publish.tenant_error schema=%s: %s",
                schema_name, tenant_err, exc_info=True,
            )
            tenant_lines.append(f"  {schema_name}: ERROR — {tenant_err}")
            continue

        total_republicados += republicados
        if republicados:
            tenant_lines.append(f"  {schema_name}: {republicados} republicado(s)")

    log.info("retry_publish.run_end republicados=%d", total_republicados)

    if not total_republicados and not hubo_error:
        return

    subject = (
        "GDI — publicaciones públicas: REQUIERE REVISIÓN"
        if hubo_error
        else "GDI — publicaciones públicas reintentadas"
    )
    body_lines = [
        f"Corrida: {run_ts}",
        f"Documentos republicados al bucket público: {total_republicados}",
        "",
        "Estos documentos estaban firmados y oficiales, pero su copia pública",
        "habia fallado (publish_failed=true) y no eran visibles en el bucket",
        "público hasta este reintento.",
    ]
    if tenant_lines:
        body_lines += ["", "Detalle por tenant:"] + tenant_lines

    await send_alert_mail(
        subject=subject,
        body="\n".join(body_lines),
        schema_name=None,
    )


async def run_retry_failed_publications() -> None:
    if not RECONCILE_ENABLED:
        log.info("retry_publish.disabled — RECONCILE_ENABLED=false, no corre")
        return

    from shared.advisory_lock import global_job_lock, LOCK_ID_RETRY_FAILED_PUBLICATIONS

    try:
        async with global_job_lock(
            LOCK_ID_RETRY_FAILED_PUBLICATIONS, "retry_failed_publications"
        ) as got_lock:
            if not got_lock:
                return
            await _run_retry_publications()
    except Exception:
        log.exception("retry_publish.fatal_error")


def schedule_retry_failed_publications(scheduler) -> None:
    scheduler.add_job(
        run_retry_failed_publications,
        "cron",
        hour=RECONCILE_TS_SWEEP_HOUR_1,
        minute=RECONCILE_TS_SWEEP_MINUTE_1,
        timezone="UTC",
        id="retry_failed_publications_run1",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_retry_failed_publications,
        "cron",
        hour=RECONCILE_TS_SWEEP_HOUR_2,
        minute=RECONCILE_TS_SWEEP_MINUTE_2,
        timezone="UTC",
        id="retry_failed_publications_run2",
        max_instances=1,
        coalesce=True,
    )
    log.info(
        "retry_failed_publications.scheduled enabled=%s cron1=%d:%02d UTC cron2=%d:%02d UTC",
        RECONCILE_ENABLED,
        RECONCILE_TS_SWEEP_HOUR_1, RECONCILE_TS_SWEEP_MINUTE_1,
        RECONCILE_TS_SWEEP_HOUR_2, RECONCILE_TS_SWEEP_MINUTE_2,
    )
