
import json
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from database import fetch_all, fetch_one, execute
from shared.exceptions import ValidationError
from shared.logging import get_logger

log = get_logger(__name__)


MAX_DOCUMENTOS_POR_TANDA = 5

TANDA_TTL_MINUTOS = 10


def _clave_manifiesto(schema_name: str, manifest_id: str) -> str:
    return f"firma:storage:{schema_name}:{manifest_id}"


async def abrir_tanda(
    document_ids: list[str],
    user_id: str,
    *,
    schema_name: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    from services.documents.signing.dispatcher import dispatch_digital_signing

    if not document_ids:
        raise ValidationError("Elegí al menos un documento para firmar.")

    vistos: set[str] = set()
    ordenados: list[str] = []
    for d in document_ids:
        if d not in vistos:
            vistos.add(d)
            ordenados.append(d)

    if len(ordenados) > MAX_DOCUMENTOS_POR_TANDA:
        raise ValidationError(
            f"Con el token se pueden firmar hasta {MAX_DOCUMENTOS_POR_TANDA} "
            f"documentos por vez. Elegiste {len(ordenados)}."
        )

    batch_id = str(_uuid.uuid4())
    preparados: list[dict] = []

    try:
        for document_id in ordenados:
            sesion = await dispatch_digital_signing(
                document_id,
                user_id,
                schema_name=schema_name,
                ip_address=ip_address,
                user_agent=user_agent,
                batch_id=batch_id,
            )
            preparados.append({
                "document_id": document_id,
                "session_id": sesion["session_id"],
                "file_id": sesion["file_id"],
                "official_number": sesion.get("official_number"),
            })
    except Exception as exc:
        log.warning(
            "tanda.apertura_fallida batch=%s preparados=%d/%d: %s",
            batch_id[:8], len(preparados), len(ordenados), exc,
        )
        await _revertir_apertura(batch_id, schema_name=schema_name)
        raise

    manifest_id = await _publicar_manifiesto(
        batch_id, preparados, schema_name=schema_name
    )

    log.info(
        "tanda.abierta batch=%s docs=%d manifiesto=%s",
        batch_id[:8], len(preparados), manifest_id,
    )

    return {
        "batch_id": batch_id,
        "user_payload": _uri_de_tanda(manifest_id, batch_id),
        "documents": preparados,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=TANDA_TTL_MINUTOS),
    }


def _uri_de_tanda(manifest_id: str, batch_id: str) -> str:
    from urllib.parse import quote
    from services.documents.signing.providers.firmador_gdi import STORAGE_BASE_URL

    servlet = quote(STORAGE_BASE_URL, safe="")
    return (
        f"gdifirma://batch?ver=1_1"
        f"&manifest={manifest_id}"
        f"&rtservlet={servlet}"
        f"&stservlet={servlet}"
        f"&id={_id_alfanumerico(batch_id)}"
        f"&keystore=PKCS11"
    )


def _id_alfanumerico(batch_id: str) -> str:
    return "BAT" + batch_id.replace("-", "").upper()


async def _publicar_manifiesto(
    batch_id: str, preparados: list[dict], *, schema_name: str
) -> str:
    import json

    from fastapi.concurrency import run_in_threadpool
    from services.cache import redis_client

    manifest_id = "MAN" + batch_id.replace("-", "").upper()

    items = "".join(
        f'<d fileid="{p["file_id"]}" id="{p["session_id"]}"/>' for p in preparados
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<batch v="1_1" n="{len(preparados)}">{items}</batch>'
    )

    if redis_client:
        ttl = TANDA_TTL_MINUTOS * 60

        def _guardar():
            pipe = redis_client.pipeline()
            pipe.setex(_clave_manifiesto(schema_name, manifest_id), ttl, xml)
            pipe.setex(
                f"firma:storage:meta:{schema_name}:{manifest_id}",
                ttl,
                json.dumps({"schema_name": schema_name, "batch_id": batch_id}),
            )
            pipe.execute()

        await run_in_threadpool(_guardar)

    return manifest_id


async def _revertir_apertura(batch_id: str, *, schema_name: str) -> None:
    await cancelar_tanda(
        batch_id,
        schema_name=schema_name,
        motivo="batch_open_failed",
        borrar_pdfs=True,
    )


async def _sesiones_de_la_tanda(batch_id: str, *, schema_name: str) -> list[dict]:
    filas = await fetch_all(
        """
        SELECT session_id, file_id, schema_name, user_id::text, document_id::text,
               is_numerator, number, status, expires_at, consumed_at,
               user_cuit, failure_reason, reservation_id::text, batch_id::text,
               cas_pre_done, cert_payload, cancelled_at
        FROM public.digital_signature_sessions
        WHERE batch_id = $1::uuid AND schema_name = $2
        ORDER BY created_at ASC
        """,
        batch_id, schema_name,
        schema_name="public",
    )
    return [dict(f) for f in filas]


ESTADOS_VIVOS = ("pending", "waiting_batch", "completing")

ESTADOS_CAIDOS = ("failed", "cancelled", "expired")


def _cert_de(sesion: dict) -> dict:
    bruto = sesion.get("cert_payload")
    if not bruto:
        return {}
    if isinstance(bruto, dict):
        return bruto
    try:
        cargado = json.loads(bruto)
        return cargado if isinstance(cargado, dict) else {}
    except (ValueError, TypeError):
        log.warning(
            "tanda.cert_ilegible session=%s — la firma se cierra igual, sin auditoría de certificado",
            str(sesion.get("session_id"))[:12],
        )
        return {}


async def registrar_firma_de_una(session: dict) -> dict:
    batch_id = session["batch_id"]
    schema_name = session["schema_name"]

    marcada = await execute(
        """
        UPDATE public.digital_signature_sessions
        SET status = 'waiting_batch',
            updated_at = NOW(),
            cas_pre_done = $2,
            cert_payload = $3::jsonb
        WHERE session_id = $1 AND status = 'pending'
        """,
        session["session_id"],
        bool(session.get("cas_pre_done")),
        json.dumps(session.get("cert") or {}),
        schema_name="public",
    )
    if not (marcada and int(marcada.split()[-1]) > 0):
        log.info(
            "tanda.ya_marcada session=%s — otro poll la movió",
            session["session_id"][:12],
        )

    hermanas = await _sesiones_de_la_tanda(batch_id, schema_name=schema_name)

    caidas = [s for s in hermanas if s["status"] in ESTADOS_CAIDOS]
    if caidas:
        motivo = (
            caidas[0].get("failure_reason")
            or f"cayó un documento de la tanda ({len(caidas)} de {len(hermanas)})"
        )
        log.warning(
            "tanda.arrastre batch=%s caidas=%d de %d motivo=%s",
            str(batch_id)[:8], len(caidas), len(hermanas), motivo,
        )
        await cancelar_tanda(batch_id, schema_name=schema_name, motivo=motivo)
        return {"estado": "failed", "faltan": 0, "motivo": motivo}

    faltan = [s for s in hermanas if s["status"] == "pending"]

    if faltan:
        log.info(
            "tanda.esperando batch=%s faltan=%d de %d",
            str(batch_id)[:8], len(faltan), len(hermanas),
        )
        return {"estado": "waiting_batch", "faltan": len(faltan)}

    await _cerrar_tanda_completa(batch_id, hermanas, schema_name=schema_name)
    return {"estado": "completing", "faltan": 0}


async def _cerrar_tanda_completa(
    batch_id: str, sesiones: list[dict], *, schema_name: str
) -> None:
    from services.documents.signing.digital_completion import encolar_cierre_digital

    encolados = 0
    for s in sesiones:
        if s["status"] not in ("waiting_batch", "completing"):
            continue
        try:
            await encolar_cierre_digital(
                schema_name=schema_name,
                document_id=s["document_id"],
                user_id=s["user_id"],
                reservation_id=s.get("reservation_id"),
                official_number=s.get("number"),
                digital_session_id=s["session_id"],
                is_numerator=bool(s["is_numerator"]),
                cas_pre_done=bool(s.get("cas_pre_done")),
                cert=_cert_de(s),
                file_id=s.get("file_id"),
            )
            await execute(
                """
                UPDATE public.digital_signature_sessions
                SET status = 'completing', updated_at = NOW()
                WHERE session_id = $1 AND status = 'waiting_batch'
                """,
                s["session_id"],
                schema_name="public",
            )
            encolados += 1
        except Exception as exc:
            log.error(
                "tanda.encolar_cierre_fallo batch=%s doc=%s encolados=%d: %s "
                "— se cancela la tanda entera",
                str(batch_id)[:8], s["document_id"][:8], encolados, exc,
            )
            await cancelar_tanda(
                batch_id,
                schema_name=schema_name,
                motivo="no se pudo encolar el cierre de la tanda",
            )
            return

    log.info(
        "tanda.cerrada batch=%s cierres_encolados=%d de %d",
        str(batch_id)[:8], encolados, len(sesiones),
    )


async def cancelar_tanda(
    batch_id: str,
    *,
    schema_name: str,
    motivo: str,
    user_id: str | None = None,
    borrar_pdfs: bool = True,
) -> dict:
    from services.documents.signing.audit_logger import log_signature_event
    from services.documents.signing.digital_completion import borrar_pdf_firmado
    from services.documents.signing.r2_lock import release_signing_lock_R2_fail
    from shared.numbering import cancel_number

    sesiones = await _sesiones_de_la_tanda(batch_id, schema_name=schema_name)
    if user_id is not None:
        ajenas = [s for s in sesiones if str(s["user_id"]) != str(user_id)]
        if ajenas:
            raise ValidationError("Esta tanda no es tuya.")

    caidas = 0
    numeros_liberados = 0
    for s in sesiones:
        if s["status"] == "signed":
            continue

        viva = s["status"] in ESTADOS_VIVOS
        caida_sucia = s["status"] in ESTADOS_CAIDOS and not s.get("cancelled_at")
        if not (viva or caida_sucia):
            continue

        if viva:
            tomada = await execute(
                """
                UPDATE public.digital_signature_sessions
                SET status = 'failed',
                    failure_reason = COALESCE($2, failure_reason),
                    cancelled_at = NOW(),
                    updated_at = NOW()
                WHERE session_id = $1 AND status = ANY($3::text[])
                """,
                s["session_id"], motivo, list(ESTADOS_VIVOS),
                schema_name="public",
            )
        else:
            tomada = await execute(
                """
                UPDATE public.digital_signature_sessions
                SET cancelled_at = NOW(), updated_at = NOW()
                WHERE session_id = $1 AND cancelled_at IS NULL
                """,
                s["session_id"],
                schema_name="public",
            )
        if not (tomada and int(tomada.split()[-1]) > 0):
            log.info("tanda.ya_movida session=%s", s["session_id"][:12])
            continue

        if borrar_pdfs:
            try:
                await borrar_pdf_firmado(
                    schema_name=schema_name, document_id=s["document_id"]
                )
            except Exception as exc:
                log.warning("tanda.borrar_pdf soft-fail doc=%s: %s",
                            s["document_id"][:8], exc)

        if s["is_numerator"] and s.get("number"):
            if s["status"] == "completing":
                log.warning(
                    "tanda.numero_no_cancelado_cierre_en_vuelo doc=%s num=%s "
                    "— lo resuelve el sweeper mirando R2",
                    s["document_id"][:8], s.get("number"),
                )
            else:
                try:
                    filas = await cancel_number(
                        s["document_id"],
                        schema_name=schema_name,
                        reason=motivo,
                        reservation_id=s.get("reservation_id"),
                        from_states=("RESERVED", "CONFIRMING"),
                    )
                except Exception as exc:
                    log.warning("tanda.cancel_number soft-fail doc=%s: %s",
                                s["document_id"][:8], exc)
                else:
                    liberadas = int(filas or 0)
                    if liberadas > 0:
                        numeros_liberados += liberadas
                    else:
                        estado_reserva = None
                        try:
                            fila = await fetch_one(
                                """
                                SELECT reservation_status
                                FROM official_documents
                                WHERE id = $1::uuid
                                """,
                                s["document_id"], schema_name=schema_name,
                            )
                            estado_reserva = (
                                fila["reservation_status"] if fila else None
                            )
                        except Exception as exc:
                            log.warning(
                                "tanda.estado_reserva_ilegible doc=%s: %s",
                                s["document_id"][:8], exc,
                            )

                        if estado_reserva in ("CANCELLED", "CONFIRMED"):
                            log.info(
                                "tanda.numero_ya_resuelto batch=%s doc=%s num=%s "
                                "estado=%s — lo liberó otro camino, nada que hacer",
                                str(batch_id)[:8], s["document_id"][:8],
                                s.get("number"), estado_reserva,
                            )
                        else:
                            log.error(
                                "tanda.numero_no_liberado batch=%s doc=%s num=%s "
                                "reserva=%s estado_sesion=%s estado_reserva=%s — "
                                "el número sigue sin volver al pozo",
                                str(batch_id)[:8], s["document_id"][:8],
                                s.get("number"), s.get("reservation_id"),
                                s["status"], estado_reserva,
                            )

        try:
            await release_signing_lock_R2_fail(
                schema_name=schema_name, doc_id=s["document_id"]
            )
        except Exception as exc:
            log.warning("tanda.release_lock soft-fail doc=%s: %s",
                        s["document_id"][:8], exc)

        if viva:
            try:
                await log_signature_event(
                    schema_name=schema_name,
                    document_id=s["document_id"],
                    user_id=s["user_id"],
                    signature_method="digital_token",
                    result="fail",
                    failure_reason=motivo,
                    session_id=s["session_id"],
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("tanda.audit_cierre soft-fail doc=%s: %s",
                            s["document_id"][:8], exc)

        caidas += 1

    await _limpiar_manifiesto(batch_id, schema_name=schema_name)

    log.info(
        "tanda.cancelada batch=%s motivo=%s sesiones_caidas=%d numeros_liberados=%d",
        str(batch_id)[:8], motivo, caidas, numeros_liberados,
    )
    return {
        "batch_id": batch_id,
        "cancelled": caidas,
        "numeros_liberados": numeros_liberados,
        "reason": motivo,
    }


async def _limpiar_manifiesto(batch_id: str, *, schema_name: str) -> None:
    from fastapi.concurrency import run_in_threadpool
    from services.cache import redis_client

    if not redis_client:
        return
    manifest_id = "MAN" + str(batch_id).replace("-", "").upper()
    try:
        await run_in_threadpool(
            redis_client.delete,
            _clave_manifiesto(schema_name, manifest_id),
            f"firma:storage:meta:{schema_name}:{manifest_id}",
        )
    except Exception as exc:
        log.warning("tanda.limpiar_manifiesto soft-fail: %s", exc)


async def estado_de_tanda(
    batch_id: str, *, schema_name: str, user_id: str
) -> dict | None:
    sesiones = await _sesiones_de_la_tanda(batch_id, schema_name=schema_name)
    if not sesiones:
        return None
    if any(str(s["user_id"]) != str(user_id) for s in sesiones):
        return None

    estados = [s["status"] for s in sesiones]
    documentos = [
        {
            "document_id": s["document_id"],
            "session_id": s["session_id"],
            "status": s["status"],
            "official_number": s.get("number"),
            "failure_reason": s.get("failure_reason"),
        }
        for s in sesiones
    ]

    if any(e in ("failed", "cancelled", "expired") for e in estados):
        fallada = next(
            (s for s in sesiones if s["status"] in ("failed", "cancelled", "expired")),
            None,
        )
        return {
            "batch_id": batch_id,
            "status": "failed",
            "failure_reason": (fallada or {}).get("failure_reason") or "batch_failed",
            "documents": documentos,
            "total": len(sesiones),
            "signed": sum(1 for e in estados if e == "signed"),
        }

    if any(e == "pending" for e in estados):
        estado = "pending"
    elif all(e == "signed" for e in estados):
        estado = "signed"
    else:
        estado = "completing"

    return {
        "batch_id": batch_id,
        "status": estado,
        "failure_reason": None,
        "documents": documentos,
        "total": len(sesiones),
        "signed": sum(1 for e in estados if e == "signed"),
        "firmador_abierto": await _firmador_ya_abrio(batch_id, schema_name=schema_name),
    }


async def _firmador_ya_abrio(batch_id: str, *, schema_name: str) -> bool:
    from fastapi.concurrency import run_in_threadpool
    from services.cache import redis_client
    from endpoints.digital_signature.storage import clave_firmador_visto

    if not redis_client:
        return False
    manifest_id = "MAN" + str(batch_id).replace("-", "").upper()
    try:
        return bool(
            await run_in_threadpool(
                redis_client.exists, clave_firmador_visto(schema_name, manifest_id)
            )
        )
    except Exception as exc:
        log.warning("tanda.firmador_visto soft-fail batch=%s: %s", str(batch_id)[:8], exc)
        return False
