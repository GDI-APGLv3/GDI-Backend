
import json
from shared.logging import get_logger
import os
import time
import uuid
from datetime import datetime, timezone

from database import fetch_all, fetch_one, execute, transaction as db_transaction
from starlette.concurrency import run_in_threadpool
from shared.alerts import send_alert_mail
from shared.numbering import cancel_number, finalize_number
from shared.utils import payload_as_dict
from config.constants import SWEEPER_SCHEMAS_CACHE_TTL_SEC, CONFIRMING_ORPHAN_GRACE_MINUTES

log = get_logger(__name__)

SWEEPER_INTERVAL_SECONDS    = int(os.getenv("SWEEPER_ESCRI_INTERVAL_SECONDS", "120"))
RESERVED_EXPIRY_TTL         = os.getenv("SWEEPER_RESERVED_EXPIRY_TTL", "15 minutes")
CONFIRMING_EXPIRY_TTL       = os.getenv("SWEEPER_CONFIRMING_EXPIRY_TTL", "20 minutes")
PROCESSING_EXPIRY_TTL       = os.getenv("SWEEPER_PROCESSING_EXPIRY_TTL", "10 minutes")
REQUEUE_PENDING_TTL         = os.getenv("ESCRI_PENDING_TTL_MINUTES", "30")
DTS_REQUEUE_PENDING_TTL     = os.getenv("DTS_PENDING_TTL_MINUTES", "60")

SWEEPER_ADVISORY_LOCK_ID    = int(os.getenv("SWEEPER_ESCRI_ADVISORY_LOCK_ID", "888890"))

_schemas_cache: dict = {"schemas": None, "cached_at": 0.0}


async def _get_active_schemas_cached() -> list[str]:
    now = time.time()
    if _schemas_cache["schemas"] is not None and (now - _schemas_cache["cached_at"]) < SWEEPER_SCHEMAS_CACHE_TTL_SEC:
        return _schemas_cache["schemas"]

    try:
        rows = await fetch_all(
            """
            SELECT schema_name
            FROM public.municipalities
            WHERE is_active = true
            """,
            schema_name="public",
        )
        schemas = [r["schema_name"] for r in rows]
        if schemas:
            _schemas_cache["schemas"] = schemas
            _schemas_cache["cached_at"] = now
        return schemas
    except Exception as exc:
        if _schemas_cache["schemas"] is not None:
            log.warning(
                "sweeper_escri.schemas_cache_refresh_failed — usando última lista conocida (%d schemas): %s",
                len(_schemas_cache["schemas"]), exc,
            )
            return _schemas_cache["schemas"]
        log.error("sweeper_escri.schemas_cache_refresh_failed_no_fallback: %s", exc)
        raise


async def _run_sweeper() -> None:
    from shared.advisory_lock import global_job_lock

    async with global_job_lock(
        SWEEPER_ADVISORY_LOCK_ID, "sweeper_escri"
    ) as got_lock:
        if not got_lock:
            return
        await _run_sweeper_body()


async def _run_sweeper_body() -> None:

    log.info("sweeper_escri.run")

    try:
        schemas = await _get_active_schemas_cached()
    except Exception:
        log.exception("sweeper_escri.no_schemas_available — se salta esta corrida")
        return

    if not schemas:
        log.debug("sweeper_escri.no_schemas")
        return

    log.info("sweeper_escri.schemas_to_sweep count=%d", len(schemas))

    for schema in schemas:
        try:
            await _sweep_schema(schema)
        except Exception:
            log.exception("sweeper_escri.schema_error schema=%s", schema)

    for schema in schemas:
        try:
            await _alertar_cola_trabada(schema)
        except Exception:
            log.exception("sweeper_escri.alerta_cola_error schema=%s", schema)


async def _alertar_cola_trabada(schema: str) -> None:
    from config.constants import (
        ESCRI_QUEUE_SLA_SECONDS,
        ESCRI_QUEUE_ALERT_COOLDOWN_SECONDS,
    )
    from services.documents.signing.queue_signals import medir_cola

    senales = await medir_cola(schema_name=schema, usar_cache=False)

    if senales.activos_tenant == 0:
        await _limpiar_incidente_cola(schema)
        return

    trabada = senales.worker_muerto or (
        senales.espera_proyectada_s(del_tenant=True) > ESCRI_QUEUE_SLA_SECONDS
    )
    if not trabada:
        await _limpiar_incidente_cola(schema)
        return

    if not await _tomar_turno_de_alerta(schema, ESCRI_QUEUE_ALERT_COOLDOWN_SECONDS):
        return

    causa = (
        "el worker no está drenando (ritmo cero con cola pendiente)"
        if senales.worker_muerto
        else f"la espera proyectada supera el SLA de {ESCRI_QUEUE_SLA_SECONDS // 60} min"
    )
    espera = senales.espera_proyectada_s(del_tenant=True)
    p90 = (
        f", espera observada p90 {senales.p90_espera_s / 60:.0f} min"
        if senales.p90_espera_s is not None else ""
    )

    log.error("sweeper_escri.cola_trabada schema=%s — %s", schema, senales.resumen(schema))
    try:
        await send_alert_mail(
            subject=f"[GDI ESCRI] Cola de firma trabada en {schema}",
            body=(
                f"{senales.resumen(schema)}\n\n"
                f"Espera proyectada para una firma nueva: {espera / 60:.0f} min.\n"
                f"Causa: {causa}.\n\n"
                f"Contexto del ambiente: {senales.activos_global} firmas activas en "
                f"total, drenando {senales.ritmo_por_min:.0f}/min{p90}.\n\n"
                f"Mientras dure, este municipio recibe HTTP 429 al intentar firmar. "
                f"Revisar el worker escri y /api/v1/system/health.\n\n"
                f"Próximo aviso sobre este municipio: recién en "
                f"{ESCRI_QUEUE_ALERT_COOLDOWN_SECONDS // 60} min si sigue trabado."
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("sweeper_escri.alerta_cola_mail_failed schema=%s: %s", schema, exc)


def _incident_key(schema: str) -> str:
    entorno = os.getenv("ENVIRONMENT", os.getenv("ENV", "dev"))
    return f"signing-queue:{entorno}:{schema}"


async def _tomar_turno_de_alerta(schema: str, cooldown: int) -> bool:
    from services.cache import get_redis

    client = get_redis()
    if client is None:
        return True
    try:
        return bool(await run_in_threadpool(
            client.set, _incident_key(schema), "1", ex=cooldown, nx=True
        ))
    except Exception as exc:  # noqa: BLE001
        log.debug("sweeper_escri.alerta_cola_redis_off (%s) — se alerta igual", exc)
        return True


async def _limpiar_incidente_cola(schema: str) -> None:
    from services.cache import get_redis

    client = get_redis()
    if client is None:
        return
    try:
        await run_in_threadpool(client.delete, _incident_key(schema))
    except Exception:  # noqa: BLE001
        pass


async def _sweep_schema(schema: str) -> None:
    await _handle_tandas_huerfanas(schema)
    await _handle_tandas_caidas_sin_limpiar(schema)
    await _handle_pending_expired(schema)
    await _handle_processing_expired(schema)
    await _handle_dts_processing_expired(schema)
    await _handle_common_pending_expired(schema)
    await _handle_common_processing_expired(schema)
    await _handle_reserved_orphans(schema)
    await _handle_confirming_expired(schema)
    await _handle_confirmed_not_signed(schema)
    await _handle_confirmed_rejected_conflict(schema)


async def _handle_tandas_huerfanas(schema: str) -> None:
    from services.documents.signing.batch_digital import cancelar_tanda

    filas = await fetch_all(
        """
        SELECT batch_id::text AS batch_id,
               count(*)                                     AS total,
               count(*) FILTER (WHERE status = 'waiting_batch') AS firmadas
        FROM public.digital_signature_sessions
        WHERE schema_name = $1
          AND batch_id IS NOT NULL
          AND status IN ('pending', 'waiting_batch')
        GROUP BY batch_id
        HAVING max(expires_at) < NOW()
        """,
        schema,
        schema_name="public",
    )

    for fila in filas:
        batch_id = fila["batch_id"]
        try:
            resultado = await cancelar_tanda(
                batch_id,
                schema_name=schema,
                motivo="la tanda venció sin completarse",
            )
            log.warning(
                "sweeper.tanda_huerfana schema=%s batch=%s total=%s firmadas=%s "
                "canceladas=%s — nadie avisó que había caído",
                schema, batch_id[:8], fila["total"], fila["firmadas"],
                resultado.get("cancelled"),
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "sweeper.tanda_huerfana_fallo schema=%s batch=%s: %s",
                schema, batch_id[:8], exc,
            )


async def _handle_tandas_caidas_sin_limpiar(schema: str) -> None:
    from services.documents.signing.batch_digital import cancelar_tanda

    filas = await fetch_all(
        """
        SELECT batch_id::text AS batch_id,
               count(*) AS sucias,
               min(failure_reason) AS motivo
        FROM public.digital_signature_sessions
        WHERE schema_name = $1
          AND batch_id IS NOT NULL
          AND status IN ('failed', 'cancelled', 'expired')
          AND cancelled_at IS NULL
          -- Ninguna hermana puede estar todavía en juego.
          --
          -- `numerator_partial_failure` puede ser el fallo de UPLOAD: ahí el
          -- número queda CONFIRMING y `_handle_confirming_expired` reencola ESE
          -- MISMO cierre. Limpiar la tanda le cancelaría el número por debajo a
          -- un reintento que está por correr.
          --
          -- Ante la duda no se toca: un lock que queda tomado se limpia en la
          -- pasada siguiente; un número cancelado en medio de un cierre, no.
          AND batch_id NOT IN (
              SELECT batch_id FROM public.digital_signature_sessions
              WHERE schema_name = $1
                AND batch_id IS NOT NULL
                AND (status IN ('pending', 'waiting_batch', 'completing')
                     OR failure_reason = 'numerator_partial_failure')
          )
        GROUP BY batch_id
        -- Tope por pasada: la PRIMERA corrida después del deploy encuentra
        -- todo el pasado junto (nadie escribía `cancelled_at` antes). Sin
        -- techo, esa pasada intentaría cancelar el backlog entero de una y
        -- cada cancelación toca R2 y numeración. Corre cada dos minutos: el
        -- resto se drena solo en las siguientes.
        ORDER BY max(created_at) DESC
        LIMIT 20
        """,
        schema,
        schema_name="public",
    )

    for fila in filas:
        batch_id = fila["batch_id"]
        try:
            resultado = await cancelar_tanda(
                batch_id,
                schema_name=schema,
                motivo=fila.get("motivo") or "la tanda cayó y quedó sin limpiar",
            )
            log.warning(
                "sweeper.tanda_sin_limpiar schema=%s batch=%s sucias=%s limpiadas=%s "
                "— cayó en el cierre y nadie soltó los recursos",
                schema, batch_id[:8], fila["sucias"], resultado.get("cancelled"),
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "sweeper.tanda_sin_limpiar_fallo schema=%s batch=%s: %s",
                schema, batch_id[:8], exc,
            )


async def _handle_pending_expired(schema: str) -> None:
    rows = await fetch_all(
        """
        UPDATE public.signing_sessions
        SET status        = 'expired',
            failure_reason = 'pending_expired_worker_offline',
            updated_at     = NOW()
        WHERE schema_name = $1
          AND job_type    = 'sign'
          AND status      = 'pending'
          AND expires_at  < NOW()
        RETURNING session_id::text, document_id::text, user_id::text, failure_reason
        """,
        schema,
        schema_name="public",
    )
    for row in rows:
        log.warning(
            "sweeper_escri.pending_expired schema=%s session=%s doc=%s "
            "— sesión expirada; la reserva se libera en esta pasada",
            schema, row["session_id"][:8], row["document_id"][:8],
        )
        await _avisar_expirada(schema, row)


async def _avisar_expirada(schema: str, row: dict) -> None:
    try:
        from services.documents.signing.failure_notice import avisar_firma_fallida

        await avisar_firma_fallida(
            session_id=row["session_id"],
            schema_name=schema,
            document_id=row["document_id"],
            user_id=row.get("user_id"),
            reason=row.get("failure_reason") or "pending_expired_worker_offline",
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "sweeper_escri.aviso_expirada_failed schema=%s session=%s",
            schema, str(row.get("session_id"))[:8],
        )


async def _handle_processing_expired(schema: str) -> None:
    rows = await fetch_all(
        """
        SELECT session_id::text,
               document_id::text,
               payload
        FROM public.signing_sessions
        WHERE status    = 'processing'
          AND job_type  = 'sign'
          AND expires_at < NOW()
          AND schema_name = $1
        """,
        schema,
        schema_name="public",
    )
    for row in rows:
        sid    = row["session_id"]
        doc_id = row["document_id"]
        current_payload: dict = payload_as_dict(row["payload"])

        od_row = None
        try:
            od_row = await fetch_one(
                """
                SELECT reservation_status, official_number
                FROM official_documents
                WHERE id = $1
                """,
                doc_id,
                schema_name=schema,
            )
        except Exception as _od_err:
            log.warning(
                "sweeper_escri.processing_expired_od_check_failed session=%s: %s",
                sid[:8], _od_err,
            )

        is_confirming = (
            od_row is not None
            and od_row["reservation_status"] == "CONFIRMING"
        )
        new_payload = dict(current_payload)
        if is_confirming:
            new_payload["is_confirming"] = True
            if od_row.get("official_number") and not new_payload.get("official_number"):
                new_payload["official_number"] = str(od_row["official_number"])

        log.warning(
            "sweeper_escri.requeue_processing schema=%s session=%s doc=%s is_confirming=%s",
            schema, sid[:8], doc_id[:8], is_confirming,
        )
        await execute(
            """
            UPDATE public.signing_sessions
            SET status      = 'pending',
                claimed_by  = NULL,
                claimed_at  = NULL,
                expires_at  = NOW() + INTERVAL '30 minutes',
                payload     = $2::jsonb,
                updated_at  = NOW()
            WHERE session_id = $1::uuid
              AND status = 'processing'
            """,
            sid,
            new_payload,
            schema_name="public",
        )
        await execute(
            "SELECT pg_notify('escri', $1)",
            schema,
            schema_name="public",
        )


async def _handle_dts_processing_expired(schema: str) -> None:
    rows = await fetch_all(
        """
        UPDATE public.signing_sessions
        SET status       = 'pending',
            claimed_by   = NULL,
            claimed_at   = NULL,
            expires_at   = NOW() + $1::text::interval,
            available_at = NULL,
            updated_at   = NOW()
        WHERE schema_name = $2
          AND job_type    = 'dts'
          AND status      = 'processing'
          AND expires_at  < NOW()
        RETURNING session_id::text, document_id::text
        """,
        f"{DTS_REQUEUE_PENDING_TTL} minutes",
        schema,
        schema_name="public",
    )
    for row in rows:
        log.warning(
            "sweeper_escri.dts_processing_expired schema=%s session=%s doc=%s "
            "— job dts huérfano recuperado (worker muerto/cancelado), "
            "vuelve a pending con attempts intactos",
            schema, row["session_id"][:8], row["document_id"][:8],
        )
        await execute(
            "SELECT pg_notify('escri', $1)",
            schema,
            schema_name="public",
        )


async def _handle_common_pending_expired(schema: str) -> None:
    rows = await fetch_all(
        """
        UPDATE public.signing_sessions
        SET status         = 'expired',
            failure_reason = 'pending_expired_worker_offline',
            updated_at     = NOW()
        WHERE schema_name = $1
          AND job_type    = 'sign_common'
          AND status      = 'pending'
          AND expires_at  < NOW()
        RETURNING session_id::text, document_id::text, user_id::text, failure_reason
        """,
        schema,
        schema_name="public",
    )
    if not rows:
        return

    from services.documents.signing.r2_lock import release_signing_lock_R2_fail

    for row in rows:
        log.warning(
            "sweeper_escri.common_pending_expired schema=%s session=%s doc=%s "
            "— liberando lock R2 (inprocess → tosign)",
            schema, row["session_id"][:8], row["document_id"][:8],
        )
        try:
            await release_signing_lock_R2_fail(schema_name=schema, doc_id=row["document_id"])
        except Exception:
            log.exception(
                "sweeper_escri.common_pending_expired_lock_release_failed schema=%s doc=%s",
                schema, row["document_id"][:8],
            )
        await _avisar_expirada(schema, row)


async def _handle_common_processing_expired(schema: str) -> None:
    rows = await fetch_all(
        """
        UPDATE public.signing_sessions
        SET status      = 'pending',
            claimed_by  = NULL,
            claimed_at  = NULL,
            expires_at  = NOW() + INTERVAL '30 minutes',
            updated_at  = NOW()
        WHERE status    = 'processing'
          AND job_type  = 'sign_common'
          AND expires_at < NOW()
          AND schema_name = $1
        RETURNING session_id::text, document_id::text
        """,
        schema,
        schema_name="public",
    )
    for row in rows:
        log.warning(
            "sweeper_escri.common_requeue_processing schema=%s session=%s doc=%s",
            schema, row["session_id"][:8], row["document_id"][:8],
        )
        await execute(
            "SELECT pg_notify('escri', $1)",
            schema,
            schema_name="public",
        )


async def _handle_reserved_orphans(schema: str) -> None:

    rows = await fetch_all(
        """
        SELECT od.id::text              AS doc_id,
               od.reservation_id::text  AS reservation_id
        FROM official_documents od
        WHERE od.reservation_status = 'RESERVED'
          AND od.created_at < NOW() - $1::text::interval
          -- Sin signing_session viva (pending o processing) del mismo tenant
          AND NOT EXISTS (
              SELECT 1
              FROM public.signing_sessions ss
              WHERE ss.reservation_id = od.reservation_id
                AND ss.schema_name = $2
                AND ss.status IN ('pending', 'processing')
          )
          -- Anti-false-positive (d): sin sesión digital activa del mismo tenant
          AND NOT EXISTS (
              SELECT 1
              FROM public.digital_signature_sessions dss
              WHERE dss.reservation_id = od.reservation_id
                AND dss.schema_name = $2
                AND (
                    (dss.consumed_at IS NULL AND dss.expires_at > NOW())
                    -- GDI-266: 'completing' = el token YA firmó y el worker
                    -- está cerrando. Esa sesión está consumida y su expires_at
                    -- ya pasó, así que las dos condiciones de arriba dan falso
                    -- y el sweeper la tomaría por abandonada: cancelaría el
                    -- número de una firma que existe y se está confirmando.
                    -- GDI-167: 'waiting_batch' es lo mismo pero dentro de una
                    -- tanda: ya firmó y espera a sus hermanos. Tampoco está
                    -- abandonada.
                    OR dss.status IN ('completing', 'waiting_batch')
                )
          )
        """,
        RESERVED_EXPIRY_TTL,
        schema,
        schema_name=schema,
    )

    cancelled = 0
    for row in rows:
        doc_id         = row["doc_id"]
        reservation_id = row["reservation_id"]
        log.warning(
            "sweeper_escri.cancel_reserved_orphan schema=%s doc=%s reservation=%s",
            schema, doc_id[:8], reservation_id[:8],
        )
        try:
            await cancel_number(
                doc_id,
                schema_name=schema,
                reason="sweeper_reserved_orphan",
                reservation_id=reservation_id,
                alert=False,
            )
            cancelled += 1
        except Exception:
            log.exception(
                "sweeper_escri.cancel_failed schema=%s doc=%s",
                schema, doc_id[:8],
            )

    if cancelled > 0:
        log.info(
            "sweeper_escri.orphans_cancelled schema=%s count=%d", schema, cancelled
        )
        try:
            await send_alert_mail(
                subject=(
                    f"[GDI SWEEPER] {cancelled} reserva(s) huérfana(s) cancelada(s) "
                    f"— schema {schema}"
                ),
                body=(
                    f"Schema: {schema}\n"
                    f"El sweeper canceló {cancelled} reserva(s) RESERVED huérfanas "
                    f"(sin sesión de firma viva, TTL expirado según SWEEPER_RESERVED_EXPIRY_TTL={RESERVED_EXPIRY_TTL}).\n"
                    f"Los números cancelados quedan disponibles para reciclaje."
                ),
            )
        except Exception as _ae:
            log.error(
                "sweeper_escri.orphan_alert_failed schema=%s: %s", schema, _ae
            )


async def _handle_confirming_expired(schema: str) -> None:

    rows = await fetch_all(
        """
        SELECT
            od.id::text              AS doc_id,
            od.reservation_id::text  AS reservation_id,
            od.official_number,
            od.updated_at
        FROM official_documents od
        WHERE od.reservation_status = 'CONFIRMING'
          AND od.updated_at < NOW() - $1::text::interval
          -- Sin signing_session viva del mismo tenant
          AND NOT EXISTS (
              SELECT 1
              FROM public.signing_sessions ss
              WHERE ss.reservation_id = od.reservation_id
                AND ss.schema_name = $2
                AND ss.status IN ('pending', 'processing')
          )
          -- Anti-false-positive (d): sin sesión digital activa del mismo tenant
          AND NOT EXISTS (
              SELECT 1
              FROM public.digital_signature_sessions dss
              WHERE dss.reservation_id = od.reservation_id
                AND dss.schema_name = $2
                AND (
                    (dss.consumed_at IS NULL AND dss.expires_at > NOW())
                    -- GDI-266: 'completing' = el token YA firmó y el worker
                    -- está cerrando. Esa sesión está consumida y su expires_at
                    -- ya pasó, así que las dos condiciones de arriba dan falso
                    -- y el sweeper la tomaría por abandonada: cancelaría el
                    -- número de una firma que existe y se está confirmando.
                    -- GDI-167: 'waiting_batch' es lo mismo pero dentro de una
                    -- tanda: ya firmó y espera a sus hermanos. Tampoco está
                    -- abandonada.
                    OR dss.status IN ('completing', 'waiting_batch')
                )
          )
        """,
        CONFIRMING_EXPIRY_TTL,
        schema,
        schema_name=schema,
    )

    for row in rows:
        doc_id         = row["doc_id"]
        reservation_id = row["reservation_id"]
        official_number = row["official_number"]

        dss_row = await fetch_one(
            """
            SELECT user_id::text
            FROM public.digital_signature_sessions
            WHERE reservation_id = $1::uuid
              AND schema_name = $2
            LIMIT 1
            """,
            reservation_id,
            schema,
            schema_name="public",
        )
        if dss_row is not None:
            updated_at = row.get("updated_at")
            age_minutes = None
            if updated_at is not None:
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                age_minutes = (datetime.now(timezone.utc) - updated_at).total_seconds() / 60

            if age_minutes is not None and age_minutes >= CONFIRMING_ORPHAN_GRACE_MINUTES:
                await _resolve_digital_confirming_orphan(
                    schema=schema,
                    doc_id=doc_id,
                    reservation_id=reservation_id,
                    official_number=official_number,
                    user_id=dss_row["user_id"],
                )
                continue

            orphan_marker = await fetch_one(
                """
                SELECT 1 AS found
                FROM public.signing_sessions
                WHERE reservation_id = $1::uuid
                  AND failure_reason = 'digital_confirming_orphan'
                LIMIT 1
                """,
                reservation_id,
                schema_name="public",
            )
            if orphan_marker is None:
                log.warning(
                    "sweeper_escri.digital_confirming_orphan schema=%s doc=%s "
                    "reservation=%s — CONFIRMING de origen digital sin sesión async "
                    "viva; en observación, se resolverá sola tras %.0f min.",
                    schema, doc_id[:8], reservation_id[:8], CONFIRMING_ORPHAN_GRACE_MINUTES,
                )
                try:
                    await send_alert_mail(
                        subject=(
                            f"[GDI ESCRI] CONFIRMING huérfano de origen digital — {official_number}"
                        ),
                        body=(
                            f"Schema: {schema}\n"
                            f"Documento: {doc_id}\n"
                            f"Número: {official_number}\n"
                            f"Reserva: {reservation_id}\n\n"
                            f"El documento está en CONFIRMING pero la sesión digital "
                            f"(digital_signature_sessions) que originó la firma está "
                            f"consumida o fallida y no hay ninguna sesión async viva. "
                            f"El sweeper NO re-encola como async (evitaría invertir el "
                            f"firmante). Queda en observación: si sigue así tras "
                            f"{CONFIRMING_ORPHAN_GRACE_MINUTES:.0f} min de antigüedad, el sweeper "
                            f"lo resolverá solo (completa si el PDF ya está en oficial/, "
                            f"cancela el número si no). No requiere acción todavía."
                        ),
                    )
                except Exception as _alert_err:
                    log.error(
                        "sweeper_escri.digital_confirming_orphan_alert_failed: %s",
                        _alert_err,
                    )
                try:
                    await execute(
                        """
                        INSERT INTO public.signing_sessions
                        (session_id, schema_name, document_id, reservation_id, user_id,
                         job_type, status, failure_reason, expires_at, payload)
                        VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5::uuid,
                                'sign', 'failed', 'digital_confirming_orphan',
                                NOW(),
                                jsonb_build_object('official_number', $6::text))
                        """,
                        str(uuid.uuid4()),
                        schema,
                        doc_id,
                        reservation_id,
                        dss_row["user_id"],
                        official_number,
                        schema_name="public",
                    )
                except Exception as _marker_err:
                    log.error(
                        "sweeper_escri.digital_confirming_orphan_marker_failed: %s",
                        _marker_err,
                    )
            else:
                log.debug(
                    "sweeper_escri.digital_confirming_orphan ya alertado schema=%s doc=%s — no re-alert",
                    schema, doc_id[:8],
                )
            continue

        log.warning(
            "sweeper_escri.requeue_confirming schema=%s doc=%s num=%s",
            schema, doc_id[:8], official_number,
        )

        last_session = await fetch_one(
            """
            SELECT user_id::text, document_id::text
            FROM public.signing_sessions
            WHERE reservation_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT 1
            """,
            reservation_id,
            schema_name="public",
        )
        if not last_session:
            log.error(
                "sweeper_escri.no_last_session schema=%s doc=%s — saltando re-encolar",
                schema, doc_id[:8],
            )
            continue

        new_session_id = str(uuid.uuid4())
        try:
            await execute(
                """
                INSERT INTO public.signing_sessions
                (session_id, schema_name, document_id, reservation_id, user_id,
                 job_type, status, expires_at, payload)
                VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5::uuid,
                        'sign', 'pending', NOW() + $6::text::interval,
                        jsonb_build_object(
                            'official_number', $7::text,
                            'is_confirming',   true
                        ))
                """,
                new_session_id,
                schema,
                last_session["document_id"],
                reservation_id,
                last_session["user_id"],
                f"{REQUEUE_PENDING_TTL} minutes",
                official_number,
                schema_name="public",
            )
            await execute(
                "SELECT pg_notify('escri', $1)",
                schema,
                schema_name="public",
            )
            log.info(
                "sweeper_escri.requeued_confirming new_session=%s",
                new_session_id[:8],
            )
        except Exception:
            log.exception(
                "sweeper_escri.requeue_insert_failed schema=%s doc=%s",
                schema, doc_id[:8],
            )


async def _resolve_digital_confirming_orphan(
    *, schema: str, doc_id: str, reservation_id: str,
    official_number: str, user_id: str,
) -> bool:
    from services.storage.cloudflare import get_tenant_r2_client
    from fastapi.concurrency import run_in_threadpool

    filename = f"{official_number}.pdf"
    try:
        r2 = await get_tenant_r2_client(schema_name=schema)
        exists = await run_in_threadpool(r2.exists_oficial, filename, "any")
    except Exception as exc:
        log.warning(
            "sweeper_escri.digital_confirming_orphan_r2_check_failed schema=%s "
            "doc=%s: %s — reintenta en el próximo ciclo (fail-safe)",
            schema, doc_id[:8], exc,
        )
        return False

    if exists:
        return await _complete_digital_confirming_orphan(
            schema=schema, doc_id=doc_id, reservation_id=reservation_id,
            official_number=official_number, user_id=user_id, r2=r2,
        )

    try:
        exists = await run_in_threadpool(r2.exists_oficial, filename, "any")
    except Exception as exc:
        log.warning(
            "sweeper_escri.digital_confirming_orphan_r2_recheck_failed schema=%s "
            "doc=%s: %s — reintenta en el próximo ciclo (fail-safe)",
            schema, doc_id[:8], exc,
        )
        return False
    if exists:
        return await _complete_digital_confirming_orphan(
            schema=schema, doc_id=doc_id, reservation_id=reservation_id,
            official_number=official_number, user_id=user_id, r2=r2,
        )

    return await _cancel_digital_confirming_orphan(
        schema=schema, doc_id=doc_id, reservation_id=reservation_id,
        official_number=official_number,
    )


async def _complete_digital_confirming_orphan(
    *, schema: str, doc_id: str, reservation_id: str,
    official_number: str, user_id: str, r2,
) -> bool:
    from fastapi.concurrency import run_in_threadpool
    from services.documents.signing.audit_logger import log_signature_event

    try:
        await finalize_number(doc_id, reservation_id, schema_name=schema)
    except Exception:
        log.exception(
            "sweeper_escri.digital_confirming_orphan_finalize_failed schema=%s doc=%s",
            schema, doc_id[:8],
        )
        return False

    draft_updated = False
    try:
        async with db_transaction(schema_name=schema) as conn:
            await conn.execute(
                "UPDATE official_documents SET signed_at = CURRENT_TIMESTAMP "
                "WHERE id = $1 AND signed_at IS NULL",
                doc_id,
            )
            _draft_row = await conn.fetchrow(
                """
                UPDATE document_draft
                SET status = 'signed', document_number = $1,
                    numbered_at = CURRENT_TIMESTAMP, numbered_by = $2,
                    last_modified_at = CURRENT_TIMESTAMP
                WHERE id = $3 AND status = 'sent_to_sign'
                RETURNING id
                """,
                official_number, user_id, doc_id,
            )
            draft_updated = _draft_row is not None
    except Exception:
        log.error(
            "sweeper_escri.digital_confirming_orphan_bd_update_failed schema=%s "
            "doc=%s num=%s — número CONFIRMED, document_draft puede haber "
            "quedado desactualizado, requiere revisión",
            schema, doc_id[:8], official_number, exc_info=True,
        )

    try:
        from services.shared.auto_link_trigger import collect_auto_link_results
        await collect_auto_link_results(doc_id, schema_name=schema)
    except Exception as _al_err:
        log.warning(
            "sweeper_escri.digital_confirming_orphan_autolink_soft_fail: %s", _al_err
        )

    signed_pdf: bytes | None = None
    try:
        signed_pdf = await run_in_threadpool(r2.get_oficial_bytes, official_number)
    except Exception as _dl_err:
        log.warning(
            "sweeper_escri.digital_confirming_orphan_download_soft_fail schema=%s doc=%s: %s",
            schema, doc_id[:8], _dl_err,
        )

    if signed_pdf:
        try:
            from services.storage.publish_public import maybe_publish_official_pdf
            await maybe_publish_official_pdf(
                schema_name=schema, official_number=official_number,
                document_id=doc_id, signed_pdf_bytes=signed_pdf,
            )
        except Exception as _pub_err:
            log.warning(
                "sweeper_escri.digital_confirming_orphan_publish_soft_fail: %s", _pub_err
            )


    if draft_updated:
        try:
            _sess_ok = await fetch_one(
                """
                SELECT session_id FROM public.digital_signature_sessions
                WHERE reservation_id = $1::uuid AND schema_name = $2
                ORDER BY created_at DESC LIMIT 1
                """,
                reservation_id, schema,
                schema_name="public",
            )
            await log_signature_event(
                schema_name=schema,
                document_id=doc_id,
                user_id=user_id,
                signature_method="digital_token",
                result="ok",
                official_number=official_number,
                session_id=(_sess_ok or {}).get("session_id"),
                r2_object_key=official_number,
            )
        except Exception as _audit_err:  # noqa: BLE001
            log.warning(
                "sweeper_escri.digital_confirming_orphan_audit_soft_fail doc=%s: %s",
                doc_id[:8], _audit_err,
            )

    log.warning(
        "sweeper_escri.digital_confirming_orphan_auto_completado schema=%s doc=%s num=%s draft_updated=%s",
        schema, doc_id[:8], official_number, draft_updated,
    )
    try:
        if draft_updated:
            _subject = f"[GDI ESCRI] CONFIRMING huérfano digital AUTO-COMPLETADO — {official_number}"
            _detalle = (
                f"El PDF firmado por el token del usuario YA estaba en R2 oficial/ — la "
                f"sesión murió después del upload pero antes de cerrar la BD. El sweeper "
                f"completó automáticamente lo que faltaba (finalize_number + estado de "
                f"BD, publicación pública y sello diferido si correspondían) tras "
                f"{CONFIRMING_ORPHAN_GRACE_MINUTES:.0f} min de antigüedad. No requiere acción."
            )
        else:
            _subject = (
                f"[GDI ESCRI] CONFIRMING huérfano digital AUTO-COMPLETADO "
                f"CON DRAFT FUERA DE CIRCUITO — {official_number}"
            )
            _detalle = (
                f"El PDF firmado estaba en R2 oficial/ y el número quedó CONFIRMED, "
                f"pero document_draft ya NO estaba en 'sent_to_sign' (posible rechazo "
                f"mientras el huérfano esperaba). REVISAR A MANO: el número está "
                f"emitido sobre un draft que salió del circuito de firma."
            )
        await send_alert_mail(
            subject=_subject,
            body=(
                f"Schema: {schema}\n"
                f"Documento: {doc_id}\n"
                f"Número: {official_number}\n"
                f"Reserva: {reservation_id}\n\n"
                f"{_detalle}"
            ),
        )
    except Exception as _alert_err:
        log.error("sweeper_escri.digital_confirming_orphan_alert_failed: %s", _alert_err)

    return True


async def _cancel_digital_confirming_orphan(
    *, schema: str, doc_id: str, reservation_id: str, official_number: str,
) -> bool:
    from services.documents.signing.audit_logger import log_signature_event

    try:
        rows_cancelled = await cancel_number(
            doc_id,
            schema_name=schema,
            reason="confirming_orphan_timeout",
            reservation_id=reservation_id,
            alert=False,
            from_states=('RESERVED', 'CONFIRMING'),
        )
    except Exception:
        log.exception(
            "sweeper_escri.digital_confirming_orphan_cancel_failed schema=%s doc=%s",
            schema, doc_id[:8],
        )
        return False

    if rows_cancelled == 0:
        log.error(
            "sweeper_escri.digital_confirming_orphan_cancel_no_rows schema=%s doc=%s "
            "num=%s reserva=%s — el número sigue sin liberarse",
            schema, doc_id[:8], official_number, reservation_id,
        )
        return False

    _sess = None
    try:
        _sess = await fetch_one(
            """
            UPDATE public.digital_signature_sessions
            SET status = 'failed', failure_reason = 'confirming_orphan_timeout',
                updated_at = NOW()
            WHERE reservation_id = $1::uuid AND schema_name = $2 AND status = 'pending'
            RETURNING session_id, user_id::text AS user_id
            """,
            reservation_id, schema,
            schema_name="public",
        )
    except Exception as _sess_err:
        log.warning(
            "sweeper_escri.digital_confirming_orphan_session_mark_failed schema=%s doc=%s: %s",
            schema, doc_id[:8], _sess_err,
        )

    if _sess:
        try:
            await log_signature_event(
                schema_name=schema,
                document_id=doc_id,
                user_id=_sess["user_id"],
                signature_method="digital_token",
                result="fail",
                failure_reason="confirming_orphan_timeout",
                session_id=_sess["session_id"],
                official_number=official_number,
            )
        except Exception as _audit_err:  # noqa: BLE001
            log.warning(
                "sweeper_escri.digital_confirming_orphan_audit_soft_fail doc=%s: %s",
                doc_id[:8], _audit_err,
            )

    log.warning(
        "sweeper_escri.digital_confirming_orphan_auto_cancelado schema=%s doc=%s num=%s",
        schema, doc_id[:8], official_number,
    )
    try:
        await send_alert_mail(
            subject=f"[GDI ESCRI] CONFIRMING huérfano digital AUTO-CANCELADO — {official_number}",
            body=(
                f"Schema: {schema}\n"
                f"Documento: {doc_id}\n"
                f"Número: {official_number}\n"
                f"Reserva: {reservation_id}\n\n"
                f"El PDF firmado por el token del usuario NUNCA llegó a R2 oficial/ — "
                f"la firma no se completó y el usuario ya no está (el token era suyo, "
                f"no se puede reintentar sin él). El sweeper canceló el número tras "
                f"{CONFIRMING_ORPHAN_GRACE_MINUTES:.0f} min de antigüedad; queda CANCELLED y "
                f"disponible para reciclaje. No requiere acción."
            ),
        )
    except Exception as _alert_err:
        log.error("sweeper_escri.digital_confirming_orphan_alert_failed: %s", _alert_err)

    return True


async def _handle_confirmed_not_signed(schema: str) -> None:

    rows = await fetch_all(
        """
        SELECT
            od.id::text              AS doc_id,
            od.reservation_id::text  AS reservation_id,
            od.official_number
        FROM official_documents od
        JOIN document_draft dd ON od.id = dd.id
        WHERE od.reservation_status = 'CONFIRMED'
          AND dd.status = 'sent_to_sign'
          -- Confirmar que hubo una sesión async (evita tocar flujos síncronos)
          AND EXISTS (
              SELECT 1
              FROM public.signing_sessions ss
              WHERE ss.reservation_id = od.reservation_id
                AND ss.schema_name = $1
          )
          -- No re-encolar si ya hay una sesión activa
          AND NOT EXISTS (
              SELECT 1
              FROM public.signing_sessions ss2
              WHERE ss2.reservation_id = od.reservation_id
                AND ss2.status IN ('pending', 'processing')
          )
        """,
        schema,
        schema_name=schema,
    )

    for row in rows:
        doc_id          = row["doc_id"]
        reservation_id  = row["reservation_id"]
        official_number = row["official_number"]

        log.warning(
            "sweeper_escri.auto_heal_confirmed schema=%s doc=%s num=%s",
            schema, doc_id[:8], official_number,
        )

        last_session = await fetch_one(
            """
            SELECT user_id::text, document_id::text
            FROM public.signing_sessions
            WHERE reservation_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT 1
            """,
            reservation_id,
            schema_name="public",
        )
        if not last_session:
            continue

        new_session_id = str(uuid.uuid4())
        try:
            await execute(
                """
                INSERT INTO public.signing_sessions
                (session_id, schema_name, document_id, reservation_id, user_id,
                 job_type, status, expires_at, payload)
                VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5::uuid,
                        'sign', 'pending', NOW() + $6::text::interval,
                        jsonb_build_object(
                            'official_number',   $7::text,
                            'is_confirming',     true,
                            'confirmed_autoheal', true
                        ))
                """,
                new_session_id,
                schema,
                last_session["document_id"],
                reservation_id,
                last_session["user_id"],
                f"{REQUEUE_PENDING_TTL} minutes",
                official_number,
                schema_name="public",
            )
            await execute(
                "SELECT pg_notify('escri', $1)",
                schema,
                schema_name="public",
            )
            log.info(
                "sweeper_escri.auto_heal_queued new_session=%s",
                new_session_id[:8],
            )
        except Exception:
            log.exception(
                "sweeper_escri.auto_heal_insert_failed schema=%s doc=%s",
                schema, doc_id[:8],
            )


async def _handle_confirmed_rejected_conflict(schema: str) -> None:

    conflict_rows = await fetch_all(
        """
        SELECT
            od.id::text              AS doc_id,
            od.reservation_id::text  AS reservation_id,
            od.official_number,
            od.numerator_id::text    AS numerator_id
        FROM official_documents od
        JOIN document_draft dd ON od.id = dd.id
        WHERE od.reservation_status = 'CONFIRMED'
          AND dd.status = 'rejected'
        """,
        schema_name=schema,
    )

    for row in conflict_rows:
        doc_id          = row["doc_id"]
        reservation_id  = row["reservation_id"]
        official_number = row["official_number"]
        numerator_id    = row["numerator_id"]

        marker = await fetch_one(
            """
            SELECT 1 AS found
            FROM public.signing_sessions
            WHERE reservation_id = $1::uuid
              AND failure_reason = 'confirmed_rejected_conflict'
            LIMIT 1
            """,
            reservation_id,
            schema_name="public",
        )
        if marker is not None:
            log.debug(
                "sweeper_escri.confirmed_rejected_conflict ya alertado "
                "schema=%s doc=%s — no re-alert",
                schema, doc_id[:8],
            )
            continue

        log.error(
            "sweeper_escri.confirmed_rejected_conflict schema=%s doc=%s num=%s "
            "— CONFIRMED (PDF en oficial/) pero draft='rejected'. "
            "Estado inconsistente — intervención manual requerida.",
            schema, doc_id[:8], official_number,
        )
        try:
            await send_alert_mail(
                subject=(
                    f"[GDI ESCRI] Conflicto CONFIRMED+rechazado — {official_number}"
                ),
                body=(
                    f"Schema: {schema}\n"
                    f"Documento: {doc_id}\n"
                    f"Número: {official_number}\n"
                    f"Reserva: {reservation_id}\n\n"
                    f"El documento está CONFIRMED (número emitido, PDF en oficial/) "
                    f"pero document_draft.status='rejected'. Estado inconsistente. "
                    f"El sweeper NO aplica cura automática ni re-encola. "
                    f"Requiere revisión y resolución manual."
                ),
            )
        except Exception as _ae:
            log.error(
                "sweeper_escri.confirmed_rejected_conflict_alert_failed: %s", _ae
            )

        last_session = await fetch_one(
            """
            SELECT user_id::text, document_id::text
            FROM public.signing_sessions
            WHERE reservation_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT 1
            """,
            reservation_id,
            schema_name="public",
        )

        if last_session:
            centinela_doc_id  = last_session["document_id"]
            centinela_user_id = last_session["user_id"]
        elif numerator_id:
            centinela_doc_id  = doc_id
            centinela_user_id = numerator_id
        else:
            log.error(
                "sweeper_escri.confirmed_rejected_conflict sin user_id disponible "
                "schema=%s doc=%s — centinela no insertado; se re-alertará",
                schema, doc_id[:8],
            )
            continue

        try:
            await execute(
                """
                INSERT INTO public.signing_sessions
                (session_id, schema_name, document_id, reservation_id, user_id,
                 job_type, status, failure_reason, expires_at, payload)
                VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5::uuid,
                        'sign', 'failed', 'confirmed_rejected_conflict',
                        NOW(),
                        jsonb_build_object('official_number', $6::text))
                """,
                str(uuid.uuid4()),
                schema,
                centinela_doc_id,
                reservation_id,
                centinela_user_id,
                official_number,
                schema_name="public",
            )
        except Exception as _marker_err:
            log.error(
                "sweeper_escri.confirmed_rejected_conflict_marker_failed: %s",
                _marker_err,
            )


async def sweep_escri_sessions() -> None:
    try:
        await _run_sweeper()
    except Exception:
        log.exception("sweeper_escri.unhandled_error")


def schedule_sweeper_escri(scheduler) -> None:
    scheduler.add_job(
        sweep_escri_sessions,
        "interval",
        seconds=SWEEPER_INTERVAL_SECONDS,
        id="sweeper_escri",
        max_instances=1,
        coalesce=True,
    )
    log.info(
        "sweeper_escri.scheduled interval=%ds", SWEEPER_INTERVAL_SECONDS
    )
