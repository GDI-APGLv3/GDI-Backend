
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from shared.logging import get_logger
from typing import Dict, Any
from database import fetch_one, get_conn
from shared.exceptions import (
    DocumentNotFoundError, ValidationError, DocumentStateError,
    AuthorizationError, EscriQueueFullError, SignerTurnPendingError,
)
from services.documents.signing.lookup_guard import confirm_document_missing
from shared.alerts import send_alert_mail
from config.constants import (
    ESCRI_QUEUE_MAX_PER_TENANT,
    ESCRI_QUEUE_MAX_GLOBAL,
    ESCRI_QUEUE_DEGRADED_THRESHOLD,
    ESCRI_QUEUE_SLA_SECONDS,
)
from ..core.queries import get_signer_role_and_document_status_query

logger = get_logger("unified_signing")

_ESCRI_PENDING_TTL_MINUTES = int(os.getenv("ESCRI_PENDING_TTL_MINUTES", "30"))

from .signing import sign_document
from .numerator import sign_document_as_numerator


async def super_sign_document(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:

    logger.info(f"Iniciando proceso de firma unificada para documento {document_id[:8]}... por usuario {user_id[:8]}...")


    result = await fetch_one(
        get_signer_role_and_document_status_query(),
        document_id,
        document_id,
        user_id,
        schema_name=schema_name,
    )

    if not result:
        logger.warning(f"Usuario {user_id[:8]}... no es firmante del documento {document_id[:8]}...")
        raise AuthorizationError(
            f"Usuario '{user_id}' no está registrado como firmante del documento '{document_id}'"
        )


    is_numerator = result['is_numerator']
    signer_status = result['signer_status']
    doc_status = result['doc_status']
    pending_common_signers = result['pending_common_signers']

    logger.info(f"is_numerator: {is_numerator}")
    logger.info(f"signer_status: {signer_status}")
    logger.info(f"doc_status: {doc_status}")
    logger.info(f"pending_common_signers: {pending_common_signers}")

    if doc_status != 'sent_to_sign':
        logger.error("Documento no está en estado sent_to_sign")
        raise DocumentStateError(
            f"Documento en estado '{doc_status}' no puede firmarse. "
            f"El documento debe estar enviado a firma primero. Use /start-signing-process.",
            current_state=doc_status,
            required_state="sent_to_sign"
        )

    if signer_status not in ['pending', None]:
        logger.error("Usuario ya firmó este documento")
        raise ValidationError(
            f"El usuario ya firmó este documento (status: {signer_status})"
        )


    if not is_numerator:
        logger.info("Ejecutando lógica de firmante común")

        async_result = await _try_enqueue_common_signer(
            document_id=document_id,
            user_id=user_id,
            schema_name=schema_name,
        )
        if async_result is not None:
            return async_result

        logger.info("Lock R2 no disponible — sigue el camino síncrono, que da el 409")
        result = await sign_document(document_id, user_id, schema_name=schema_name)

        logger.info(f"Firmante común - Resultado: {result.get('success')}")

        return {
            "success": result["success"],
            "message": result["message"],
            "document_id": document_id,
            "signature_id": result["signature_id"],
            "document_status": result["document_status"],
            "signed_at": datetime.now().isoformat(),
            "is_numerator": False,
            "official_number": None,
            "signed_pdf_url": None,
            "auto_link_results": [],
        }

    else:
        logger.info("Ejecutando lógica de numerador")

        if pending_common_signers > 0:
            logger.info(
                f"Firma fuera de turno: quedan {pending_common_signers} firmante(s) "
                "común(es) pendiente(s) — el numerador firma al final"
            )
            raise SignerTurnPendingError(pending_common_signers)

        logger.info("Todos los firmantes comunes han firmado, procediendo con numerador")

        async_result = await _try_reserve_and_enqueue(
            document_id=document_id,
            user_id=user_id,
            schema_name=schema_name,
        )
        if async_result is not None:
            return async_result

        logger.info("Régimen SPECIAL — numera por el camino síncrono (D1)")
        result = await sign_document_as_numerator(document_id, user_id, schema_name=schema_name)

        logger.info(f"Numerador - Resultado: {result.get('success')}")
        logger.info(f"Numerador - Official number: {result.get('official_number')}")

        signed_pdf_url = None
        if result.get("api_result"):
            signed_pdf_url = (
                result["api_result"].get("signed_pdf_url") or
                result["api_result"].get("url_pdf_firmado_1")
            )

        return {
            "success": result["success"],
            "message": result["message"],
            "document_id": result["document_id"],
            "signature_id": result["numerator_id"],
            "document_status": result.get("document_status", "signed"),
            "signed_at": datetime.now().isoformat(),
            "is_numerator": True,
            "official_number": result.get("official_number"),
            "signed_pdf_url": signed_pdf_url,
            "auto_link_results": result.get("auto_link_results", []),
        }


async def _try_reserve_and_enqueue(
    document_id: str,
    user_id: str,
    *,
    schema_name: str,
) -> Dict[str, Any] | None:
    existing_session_row = await fetch_one(
        """
        SELECT session_id::text, expires_at
        FROM public.signing_sessions
        WHERE schema_name = $1
          AND document_id = $2::uuid
          AND user_id     = $3::uuid
          AND job_type    = 'sign'
          AND status      IN ('pending', 'processing')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        schema_name,
        document_id,
        user_id,
        schema_name="public",
    )
    if existing_session_row:
        existing_sid = str(existing_session_row["session_id"])
        existing_expires = existing_session_row["expires_at"]
        existing_expires_iso = (
            existing_expires.strftime("%Y-%m-%dT%H:%M:%SZ")
            if existing_expires else None
        )
        logger.info(
            "H2 idempotencia: sesión existente %s para doc=%s user=%s — "
            "devolviendo misma sesión sin re-encolar",
            existing_sid[:8], document_id[:8], user_id[:8],
        )
        return {
            "success": True,
            "message": "Firma ya encolada — se procesará en instantes",
            "document_id": document_id,
            "signature_id": existing_sid,
            "document_status": "sent_to_sign",
            "signed_at": None,
            "is_numerator": True,
            "official_number": None,
            "signed_pdf_url": None,
            "auto_link_results": [],
            "flow": "electronic_async",
            "session_id": existing_sid,
            "poll_url": f"/signing/async-poll/{existing_sid}",
            "expires_at": existing_expires_iso,
        }

    await _check_escri_queue_capacity(schema_name=schema_name)

    doc_data_row = await fetch_one(
        """
        SELECT
            dd.reference,
            dd.content,
            dd.document_type_id AS document_type_id,
            dd.resume,
            dt.acronym                AS document_type_acronym,
            dt.special_numbering,
            (
                SELECT json_agg(json_build_object(
                    'user_id',       ds2.user_id,
                    'full_name',     u2.full_name,
                    'status',        ds2.status,
                    'is_numerator',  ds2.is_numerator,
                    'signing_order', ds2.signing_order,
                    'signed_at',     ds2.signed_at
                ))
                FROM document_signers ds2
                JOIN users u2 ON ds2.user_id = u2.id
                WHERE ds2.document_id = $1
            ) AS signers_json,
            (
                SELECT ARRAY_AGG(DISTINCT u3.sector_id)
                       FILTER (WHERE u3.sector_id IS NOT NULL)
                FROM document_signers ds3
                JOIN users u3 ON ds3.user_id = u3.id
                WHERE ds3.document_id = $1
            ) AS signer_sector_ids
        FROM document_draft dd
        JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE dd.id = $1
        """,
        document_id,
        schema_name=schema_name,
    )

    if not doc_data_row:
        await confirm_document_missing(
            document_id, schema_name=schema_name, context="unified_signing.prepare_async"
        )
        raise DocumentNotFoundError(f"Documento {document_id} no encontrado al preparar firma async")

    if bool(doc_data_row["special_numbering"]):
        return None

    from services.documents.signing.numbering_permissions import can_user_number_document_type
    has_rank, has_sector, reason = await can_user_number_document_type(
        user_id,
        doc_data_row["document_type_id"],
        schema_name=schema_name,
    )
    if not has_rank or not has_sector:
        raise AuthorizationError(reason)

    content = doc_data_row["content"] or {}
    field_defs_row = await fetch_one(
        "SELECT field_definitions FROM document_type_fields WHERE document_type_id = $1",
        doc_data_row["document_type_id"],
        schema_name=schema_name,
    )
    if field_defs_row is not None and field_defs_row["field_definitions"]:
        field_defs = field_defs_row["field_definitions"]
        from services.documents.ffcc_validator import validate_ffcc_content
        validate_ffcc_content(
            content if isinstance(content, dict) else {},
            field_defs,
            schema_name=schema_name,
            enforce_required=True,
        )
        content = {
            "schema": field_defs,
            "data": content if isinstance(content, dict) else {},
        }
        logger.info("Snapshot FFCC armado para firma async")

    from shared.numbering import reserve_number
    current_year = datetime.now().year
    official_number, _, _, reservation_id = await reserve_number(
        document_type_acronym=doc_data_row["document_type_acronym"] or "DOC",
        user_id=user_id,
        year=current_year,
        schema_name=schema_name,
        document_id=document_id,
        reference=doc_data_row["reference"],
        document_type_id=doc_data_row["document_type_id"],
        content=content,
        resume=doc_data_row.get("resume"),
        signers=doc_data_row["signers_json"] or [],
        signer_sector_ids=doc_data_row["signer_sector_ids"],
    )
    logger.info("Número reservado para firma async: %s ticket=%s", official_number, reservation_id[:8])

    if doc_data_row.get("resume"):
        from database import execute
        await execute(
            "UPDATE document_draft SET resume = NULL WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )

    expires_at_dt = datetime.now(tz=timezone.utc) + timedelta(minutes=_ESCRI_PENDING_TTL_MINUTES)
    expires_at_iso = expires_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    session_id = await _enqueue_sign_session(
        document_id=document_id,
        user_id=user_id,
        reservation_id=reservation_id,
        official_number=official_number,
        schema_name=schema_name,
    )
    logger.info("Sesión async encolada: session=%s expires_at=%s", session_id[:8], expires_at_iso)

    poll_url = f"/signing/async-poll/{session_id}"
    espera_estimada = await _estimated_wait_seconds(schema_name)

    return {
        "success": True,
        "message": "Firma encolada — se procesará en instantes",
        "document_id": document_id,
        "signature_id": session_id,
        "document_status": "sent_to_sign",
        "signed_at": None,
        "is_numerator": True,
        "official_number": None,
        "signed_pdf_url": None,
        "auto_link_results": [],
        "flow": "electronic_async",
        "session_id": session_id,
        "poll_url": poll_url,
        "expires_at": expires_at_iso,
        "estimated_wait_seconds": espera_estimada,
    }


async def _enqueue_sign_session(
    document_id: str,
    user_id: str,
    reservation_id: str,
    official_number: str,
    schema_name: str,
) -> str:
    session_id = str(uuid.uuid4())
    async with get_conn(schema_name="public") as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO public.signing_sessions
                (session_id, schema_name, document_id, reservation_id, user_id,
                 job_type, status, expires_at, payload)
                VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5::uuid,
                        'sign', 'pending', NOW() + $6::text::interval,
                        jsonb_build_object('official_number', $7::text))
                ON CONFLICT (schema_name, document_id, user_id)
                  WHERE job_type = 'sign' AND status IN ('pending', 'processing')
                DO NOTHING
                RETURNING session_id::text
                """,
                session_id,
                schema_name,
                document_id,
                reservation_id,
                user_id,
                f"{_ESCRI_PENDING_TTL_MINUTES} minutes",
                official_number,
            )

            if row is None:
                existente = await conn.fetchrow(
                    """
                    SELECT session_id::text AS sid
                    FROM public.signing_sessions
                    WHERE schema_name = $1
                      AND document_id = $2::uuid
                      AND user_id     = $3::uuid
                      AND job_type    = 'sign'
                      AND status IN ('pending', 'processing')
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    schema_name, document_id, user_id,
                )
                if existente:
                    logger.info(
                        "GDI-271: ya había una sesión de firma viva para doc=%s user=%s "
                        "— se devuelve la existente (%s) en lugar de crear una segunda",
                        document_id[:8], user_id[:8], existente["sid"][:8],
                    )
                    return existente["sid"]

                raise RuntimeError(
                    "GDI-271: no se pudo encolar la sesión de firma ni recuperar la existente"
                )

            await conn.execute("SELECT pg_notify('escri', $1)", schema_name)
    return session_id


async def _try_enqueue_common_signer(
    document_id: str,
    user_id: str,
    *,
    schema_name: str,
) -> Dict[str, Any] | None:
    """
    Intenta encolar la firma async del firmante común (job_type='sign_common').

    Retorna el dict de respuesta 202 si el encolado fue exitoso, o None si
    el caller debe caer al flujo síncrono (cola saturada con soft-fail —
    en la práctica solo levanta EscriQueueFullError, que el endpoint traduce
    a 429; None solo se usa para el caso "lock no disponible", donde SÍ
    queremos el 409 síncrono de siempre, no un 202 fantasma).

    Raises:
        EscriQueueFullError: cola saturada (GDI-217, mismo tope que 'sign').
    """
    existing_session_row = await fetch_one(
        """
        SELECT session_id::text, expires_at
        FROM public.signing_sessions
        WHERE schema_name = $1
          AND document_id = $2::uuid
          AND user_id     = $3::uuid
          AND job_type    = 'sign_common'
          AND status      IN ('pending', 'processing')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        schema_name,
        document_id,
        user_id,
        schema_name="public",
    )
    if existing_session_row:
        existing_sid = str(existing_session_row["session_id"])
        existing_expires = existing_session_row["expires_at"]
        existing_expires_iso = (
            existing_expires.strftime("%Y-%m-%dT%H:%M:%SZ")
            if existing_expires else None
        )
        logger.info(
            "Idempotencia sign_common: sesión existente %s para doc=%s user=%s — "
            "devolviendo misma sesión sin re-encolar",
            existing_sid[:8], document_id[:8], user_id[:8],
        )
        return {
            "success": True,
            "message": "Firma ya encolada — se procesará en instantes",
            "document_id": document_id,
            "signature_id": existing_sid,
            "document_status": "sent_to_sign",
            "signed_at": None,
            "is_numerator": False,
            "official_number": None,
            "signed_pdf_url": None,
            "auto_link_results": [],
            "flow": "electronic_async",
            "session_id": existing_sid,
            "poll_url": f"/signing/async-poll/{existing_sid}",
            "expires_at": existing_expires_iso,
        }

    await _check_escri_queue_capacity(schema_name=schema_name)

    from services.documents.signing.r2_lock import acquire_signing_lock_R2

    lock_acquired = await acquire_signing_lock_R2(
        schema_name=schema_name,
        doc_id=document_id,
    )
    if not lock_acquired:
        logger.warning(
            "sign_common: lock R2 no disponible para doc=%s — 409 "
            "document_already_signing (mismo criterio que el flujo síncrono)",
            document_id[:8],
        )
        raise ValidationError(
            f"El documento {document_id} ya está siendo firmado por otro proceso (lock R2 activo)"
        )

    expires_at_dt = datetime.now(tz=timezone.utc) + timedelta(minutes=_ESCRI_PENDING_TTL_MINUTES)
    expires_at_iso = expires_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    session_id = str(uuid.uuid4())
    try:
        async with get_conn(schema_name="public") as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO public.signing_sessions
                    (session_id, schema_name, document_id, reservation_id, user_id,
                     job_type, status, expires_at, payload)
                    VALUES ($1::uuid, $2, $3::uuid, NULL, $4::uuid,
                            'sign_common', 'pending', NOW() + $5::text::interval,
                            '{}'::jsonb)
                    ON CONFLICT DO NOTHING
                    RETURNING session_id::text
                    """,
                    session_id,
                    schema_name,
                    document_id,
                    user_id,
                    f"{_ESCRI_PENDING_TTL_MINUTES} minutes",
                )

                if row is None:
                    existente = await conn.fetchrow(
                        """
                        SELECT session_id::text AS sid, expires_at
                        FROM public.signing_sessions
                        WHERE schema_name = $1
                          AND document_id = $2::uuid
                          AND user_id     = $3::uuid
                          AND job_type    = 'sign_common'
                          AND status IN ('pending', 'processing')
                        ORDER BY created_at ASC
                        LIMIT 1
                        """,
                        schema_name, document_id, user_id,
                    )
                    if existente is None:
                        raise RuntimeError(
                            "GDI-271: no se pudo encolar la sesión sign_common "
                            "ni recuperar la existente"
                        )

                    logger.info(
                        "GDI-271: ya había una sesión sign_common viva para doc=%s "
                        "user=%s — se devuelve la existente (%s) en lugar de crear "
                        "una segunda",
                        document_id[:8], user_id[:8], existente["sid"][:8],
                    )
                    session_id = existente["sid"]
                    existing_expires = existente["expires_at"]
                    expires_at_iso = (
                        existing_expires.strftime("%Y-%m-%dT%H:%M:%SZ")
                        if existing_expires else None
                    )
                    return {
                        "success": True,
                        "message": "Firma ya encolada — se procesará en instantes",
                        "document_id": document_id,
                        "signature_id": session_id,
                        "document_status": "sent_to_sign",
                        "signed_at": None,
                        "is_numerator": False,
                        "official_number": None,
                        "signed_pdf_url": None,
                        "auto_link_results": [],
                        "flow": "electronic_async",
                        "session_id": session_id,
                        "poll_url": f"/signing/async-poll/{session_id}",
                        "expires_at": expires_at_iso,
                    }

                await conn.execute("SELECT pg_notify('escri', $1)", schema_name)
    except Exception:
        logger.exception(
            "sign_common: fallo al encolar sesión — revirtiendo lock R2 doc=%s",
            document_id[:8],
        )
        from services.documents.signing.r2_lock import release_signing_lock_R2_fail
        await release_signing_lock_R2_fail(schema_name=schema_name, doc_id=document_id)
        raise

    logger.info(
        "sign_common: sesión async encolada session=%s doc=%s expires_at=%s",
        session_id[:8], document_id[:8], expires_at_iso,
    )

    return {
        "success": True,
        "message": "Firma encolada — se procesará en instantes",
        "document_id": document_id,
        "signature_id": session_id,
        "document_status": "sent_to_sign",
        "signed_at": None,
        "is_numerator": False,
        "official_number": None,
        "signed_pdf_url": None,
        "auto_link_results": [],
        "flow": "electronic_async",
        "session_id": session_id,
        "poll_url": f"/signing/async-poll/{session_id}",
        "expires_at": expires_at_iso,
        "estimated_wait_seconds": await _estimated_wait_seconds(schema_name),
    }


_degraded_alert_lock = threading.Lock()
_degraded_alert_state = {"alerted": False, "last_alert_at": 0.0}


async def _check_escri_queue_capacity(*, schema_name: str) -> None:
    from services.documents.signing.queue_signals import medir_cola

    senales = await medir_cola(schema_name=schema_name)
    global_count = senales.activos_global
    tenant_count = senales.activos_tenant

    if global_count >= ESCRI_QUEUE_DEGRADED_THRESHOLD:
        await _maybe_alert_degraded(global_count)
    else:
        _reset_degraded_alert_if_recovered(global_count)

    if senales.worker_muerto:
        logger.error(
            "escri_queue.worker_muerto %s — encolado cortado",
            senales.resumen(),
        )
        raise EscriQueueFullError("dead_worker", retry_after=60)

    if global_count >= ESCRI_QUEUE_MAX_GLOBAL:
        raise EscriQueueFullError("global_cap", retry_after=30)

    if global_count >= ESCRI_QUEUE_DEGRADED_THRESHOLD:
        raise EscriQueueFullError("degraded_threshold", retry_after=30)

    if senales.supera_sla:
        proyectada = senales.espera_proyectada_s()
        logger.warning(
            "escri_queue.sla_superado espera_proyectada=%.0fs sla=%ds — %s",
            proyectada, ESCRI_QUEUE_SLA_SECONDS, senales.resumen(schema_name),
        )
        raise EscriQueueFullError(
            "wait_over_sla",
            retry_after=max(10, int(proyectada - ESCRI_QUEUE_SLA_SECONDS)),
        )

    if tenant_count >= ESCRI_QUEUE_MAX_PER_TENANT:
        raise EscriQueueFullError("tenant_cap", retry_after=10)


async def _estimated_wait_seconds(schema_name: str) -> int | None:
    try:
        from services.documents.signing.queue_signals import medir_cola
        senales = await medir_cola(schema_name=schema_name)
        return max(0, int(senales.espera_proyectada_s()))
    except Exception as exc:  # noqa: BLE001 — ver docstring
        logger.debug("GDI-257: no se pudo estimar la espera (%s)", exc)
        return None


async def _maybe_alert_degraded(global_count: int) -> None:
    with _degraded_alert_lock:
        if _degraded_alert_state["alerted"]:
            return
        _degraded_alert_state["alerted"] = True
        _degraded_alert_state["last_alert_at"] = time.time()

    logger.error(
        "escri_queue.degraded global_count=%d threshold=%d — encolado cortado, alertando",
        global_count, ESCRI_QUEUE_DEGRADED_THRESHOLD,
    )
    try:
        await send_alert_mail(
            subject=f"[GDI ESCRI] Cola de firma async en modo degradado ({global_count} sesiones)",
            body=(
                f"La cola global de firma async (public.signing_sessions, "
                f"job_type='sign', pending+processing) llegó a {global_count} "
                f"sesiones, superando ESCRI_QUEUE_DEGRADED_THRESHOLD="
                f"{ESCRI_QUEUE_DEGRADED_THRESHOLD}.\n\n"
                f"El encolado de NUEVAS firmas está cortado (HTTP 429) hasta que "
                f"la cola baje. Esto suele indicar un problema sistémico (Notary/"
                f"DigiCert caído o degradado, worker escri trabado o caído) — "
                f"revisar /api/v1/system/health y los logs del worker escri."
            ),
        )
    except Exception as _ae:
        logger.error("escri_queue.degraded_alert_failed: %s", _ae)


def _reset_degraded_alert_if_recovered(global_count: int) -> None:
    if global_count < ESCRI_QUEUE_DEGRADED_THRESHOLD:
        with _degraded_alert_lock:
            _degraded_alert_state["alerted"] = False
