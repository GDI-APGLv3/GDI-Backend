
from shared.logging import get_logger
from starlette.concurrency import run_in_threadpool

from database import transaction as db_transaction, execute, fetch_one
from shared.exceptions import (
    NumeratorPreCasError,
    NumeratorUploadError,
    StaleReservationError,
    DocumentRejectedWhileInQueueError,
)
from shared.numbering import confirm_number, finalize_number

log = get_logger(__name__)


def clave_pdf_pendiente(document_id: str) -> str:
    return f"signed-pending/{document_id}.pdf"


async def subir_pdf_a_oficial(
    schema_name: str,
    official_number: str,
    signed_pdf: bytes,
    document_id: str | None = None,
) -> None:
    from services.storage.cloudflare import get_tenant_r2_client

    from services.storage.pdf_location import (
        target_pdf_location, persist_pdf_location, effective_pdf_location,
    )

    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    _target_loc = target_pdf_location()
    oficial_filename = f"{official_number}.pdf"
    _upload_res = await run_in_threadpool(
        r2_client.upload_oficial, signed_pdf, oficial_filename, _target_loc
    )
    _effective_loc = effective_pdf_location(_upload_res, _target_loc)
    await persist_pdf_location(
        document_id, _effective_loc, schema_name=schema_name, official_number=official_number
    )
    log.info(f"cierre_digital: PDF subido a {_effective_loc}/{oficial_filename}")

    if document_id:
        from services.storage.publish_public import maybe_publish_official_pdf
        await maybe_publish_official_pdf(
            schema_name=schema_name,
            official_number=official_number,
            document_id=document_id,
            signed_pdf_bytes=signed_pdf,
        )


async def actualizar_numerador_en_bd(
    document_id: str,
    user_id: str,
    official_number: str,
    *,
    schema_name: str,
) -> None:
    async with db_transaction(schema_name=schema_name) as conn:
        await conn.execute(
            """
            UPDATE official_documents
            SET signed_at = CURRENT_TIMESTAMP
            WHERE id = $1 AND signed_at IS NULL
            """,
            document_id,
        )
        _draft_rows = await conn.fetch(
            """
            UPDATE document_draft
            SET status = 'signed',
                document_number = $1,
                numbered_at = CURRENT_TIMESTAMP,
                numbered_by = $2,
                last_modified_at = CURRENT_TIMESTAMP
            WHERE id = $3
              AND status = 'sent_to_sign'
            RETURNING id
            """,
            official_number, user_id, document_id,
        )
        if not _draft_rows:
            raise DocumentRejectedWhileInQueueError(document_id)
    log.info(
        f"cierre_digital: document_draft y official_documents actualizados "
        f"doc={document_id[:8]}... number={official_number}"
    )


async def completar_numerador(
    document_id: str,
    user_id: str,
    schema_name: str,
    official_number: str,
    signed_pdf: bytes,
    reservation_id: str | None = None,
    cas_pre_done: bool = False,
) -> list[dict]:

    if reservation_id:
        if not cas_pre_done:
            try:
                await confirm_number(document_id, reservation_id, schema_name=schema_name)
            except StaleReservationError:
                raise
            except Exception as _e:
                raise NumeratorPreCasError(
                    f"confirm_number falló para doc={document_id[:8]}"
                ) from _e
            log.info(
                f"cierre_digital: CAS RESERVED→CONFIRMING doc={document_id[:8]}... "
                f"ticket={reservation_id[:8]}..."
            )
        else:
            log.info(
                f"cierre_digital: CAS pre-done, continuando upload "
                f"doc={document_id[:8]}... ticket={reservation_id[:8]}..."
            )

        try:
            await subir_pdf_a_oficial(schema_name, official_number, signed_pdf, document_id)
        except Exception as _e:
            raise NumeratorUploadError(
                f"upload_oficial falló para doc={document_id[:8]}"
            ) from _e

        await finalize_number(document_id, reservation_id, schema_name=schema_name)
        log.info(f"cierre_digital: número {official_number} CONFIRMED")
    else:
        log.warning(
            f"cierre_digital: reservation_id ausente para doc={document_id[:8]}... "
            f"— usando flujo legacy (pre-migración 073). Aplicar migración 073 para CAS completo."
        )
        try:
            await subir_pdf_a_oficial(schema_name, official_number, signed_pdf, document_id)
        except Exception as _e:
            raise NumeratorUploadError(
                f"upload_oficial legacy falló para doc={document_id[:8]}"
            ) from _e

        async with db_transaction(schema_name=schema_name) as conn:
            od_row = await conn.fetchrow(
                """
                SELECT numbering_regime, document_type_id, year, department_id
                FROM official_documents
                WHERE id = $1
                """,
                document_id,
            )
            result_upd = await conn.execute(
                """
                UPDATE official_documents
                SET reservation_status = 'CONFIRMED'
                WHERE id = $1 AND reservation_status = 'RESERVED'
                """,
                document_id,
            )
            rows_updated = int(result_upd.split()[-1]) if result_upd else 0
            if rows_updated == 0:
                log.warning(
                    f"cierre_digital legacy: fila no estaba RESERVED "
                    f"doc={document_id[:8]}... (quizás ya CONFIRMED por otro path)"
                )
            elif od_row and od_row['numbering_regime'] == 'SPECIAL':
                await conn.execute(
                    """
                    UPDATE document_number_counters
                    SET active_reservation_document_id = NULL,
                        updated_at = NOW()
                    WHERE document_type_id = $1
                      AND year = $2
                      AND department_id = $3
                      AND active_reservation_document_id = $4
                    """,
                    od_row['document_type_id'], od_row['year'],
                    od_row['department_id'], document_id,
                )
        log.info(f"cierre_digital: número {official_number} CONFIRMED (legacy)")

    await actualizar_numerador_en_bd(document_id, user_id, official_number, schema_name=schema_name)

    auto_link_results: list[dict] = []
    try:
        from services.shared.auto_link_trigger import collect_auto_link_results
        auto_link_results = await collect_auto_link_results(
            document_id, schema_name=schema_name
        )
    except Exception as _al_err:
        log.warning(f"collect_auto_link_results soft-fail (no bloquea firma): {_al_err}")


    return auto_link_results


async def guardar_pdf_firmado(
    *, schema_name: str, document_id: str, signed_pdf: bytes
) -> str:
    from services.r2_client import r2_put

    key = clave_pdf_pendiente(document_id)
    await r2_put(schema_name=schema_name, key=key, body=signed_pdf, bucket="tosign")
    log.info(
        "cierre_digital.pdf_persistido doc=%s key=%s bytes=%d",
        document_id[:8], key, len(signed_pdf),
    )
    return key


async def leer_pdf_firmado(*, schema_name: str, document_id: str) -> bytes:
    from services.r2_client import r2_get_object

    return await r2_get_object(
        schema_name=schema_name, key=clave_pdf_pendiente(document_id), bucket="tosign"
    )


async def borrar_pdf_firmado(*, schema_name: str, document_id: str) -> None:
    from services.r2_client import r2_delete

    try:
        await r2_delete(
            schema_name=schema_name, key=clave_pdf_pendiente(document_id), bucket="tosign"
        )
    except Exception as e:
        log.warning("cierre_digital.borrado_pendiente soft-fail doc=%s: %s", document_id[:8], e)


async def encolar_cierre_digital(
    *,
    schema_name: str,
    document_id: str,
    user_id: str,
    reservation_id: str | None,
    official_number: str | None,
    digital_session_id: str,
    is_numerator: bool,
    cas_pre_done: bool,
    cert: dict,
    file_id: str | None = None,
) -> str | None:
    import os
    import uuid as _uuid

    from database import get_conn

    ttl_minutos = int(os.getenv("ESCRI_PENDING_TTL_MINUTES", "30"))

    session_id = str(_uuid.uuid4())
    payload = {
        "official_number": official_number,
        "digital_session_id": digital_session_id,
        "is_numerator": is_numerator,
        "cas_pre_done": cas_pre_done,
        "cert": cert,
        "file_id": file_id,
    }

    async with get_conn(schema_name="public") as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO public.signing_sessions
                (session_id, schema_name, document_id, reservation_id, user_id,
                 job_type, status, expires_at, payload)
                VALUES ($1::uuid, $2, $3::uuid, $4::uuid, $5::uuid,
                        'digital_complete', 'pending', NOW() + $6::text::interval,
                        $7)
                ON CONFLICT (schema_name, document_id, user_id)
                  WHERE job_type = 'digital_complete'
                    AND status IN ('pending', 'processing')
                DO NOTHING
                RETURNING session_id::text
                """,
                session_id,
                schema_name,
                document_id,
                reservation_id,
                user_id,
                f"{ttl_minutos} minutes",
                payload,
            )
            if row is None:
                existente = await conn.fetchrow(
                    """
                    SELECT session_id::text AS sid
                    FROM public.signing_sessions
                    WHERE schema_name = $1
                      AND document_id = $2::uuid
                      AND user_id     = $3::uuid
                      AND job_type    = 'digital_complete'
                      AND status IN ('pending', 'processing')
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    schema_name, document_id, user_id,
                )
                log.info(
                    "cierre_digital.ya_encolado doc=%s user=%s — se reusa %s",
                    document_id[:8], user_id[:8],
                    existente["sid"][:8] if existente else "ninguna",
                )
                return existente["sid"] if existente else None
            await conn.execute("SELECT pg_notify('escri', $1)", schema_name)

    log.info(
        "cierre_digital.encolado doc=%s cola=%s numerador=%s",
        document_id[:8], session_id[:8], is_numerator,
    )
    return session_id


async def marcar_sesion_completing(digital_session_id: str) -> bool:
    resultado = await execute(
        """
        UPDATE public.digital_signature_sessions
        SET status = 'completing', updated_at = NOW()
        WHERE session_id = $1 AND status = 'pending'
        """,
        digital_session_id,
        schema_name="public",
    )
    gano = bool(resultado and int(resultado.split()[-1]) > 0)
    if not gano:
        log.info(
            "cierre_digital.cas_perdido session=%s — otro ya la movió de 'pending'",
            digital_session_id[:12],
        )
    return gano


async def actualizar_firmante(
    document_id: str, user_id: str, schema_name: str,
    session_id: str, cert_serial: str | None, cert_cuit: str | None, provider: str,
) -> None:
    await execute(
        """
        UPDATE document_signers
        SET signed_at = NOW(),
            status = 'signed',
            signed_with_provider = $1,
            cert_serial = $2,
            cert_subject_cuit = $3,
            signature_session_id = $4
        WHERE document_id = $5 AND user_id = $6
        """,
        provider, cert_serial, cert_cuit, session_id, document_id, user_id,
        schema_name=schema_name,
    )


async def marcar_sesion_digital(
    digital_session_id: str, estado: str, motivo: str | None = None
) -> bool:
    resultado = await execute(
        """
        UPDATE public.digital_signature_sessions
        SET status = $1,
            updated_at = NOW(),
            completed_at = CASE WHEN $1 = 'signed' THEN NOW() ELSE completed_at END,
            failure_reason = COALESCE($2, failure_reason)
        WHERE session_id = $3
          AND status IN ('pending', 'completing')
        """,
        estado, motivo, digital_session_id,
        schema_name="public",
    )
    return bool(resultado and int(resultado.split()[-1]) > 0)


async def limpiar_redis_de_la_sesion(
    *, schema_name: str, file_id: str, digital_session_id: str
) -> None:
    from services.cache import redis_client

    if not redis_client:
        return
    try:
        await run_in_threadpool(
            redis_client.delete,
            f"firma:storage:{schema_name}:{file_id}",
            f"firma:storage:{schema_name}:{digital_session_id}",
            f"firma:storage:meta:{schema_name}:{digital_session_id}",
        )
    except Exception as e:
        log.warning("cierre_digital.redis_cleanup soft-fail: %s", e)


async def _alertar(subject: str, body: str) -> None:
    try:
        from shared.alerts import send_alert_mail

        await send_alert_mail(subject=subject, body=body)
    except Exception:
        pass


async def cerrar_firma_digital(
    *,
    schema_name: str,
    document_id: str,
    user_id: str,
    reservation_id: str | None,
    official_number: str | None,
    digital_session_id: str,
    is_numerator: bool,
    cas_pre_done: bool,
    cert: dict,
    file_id: str | None = None,
) -> dict:
    estado_actual = await fetch_one(
        """
        SELECT status FROM public.digital_signature_sessions
        WHERE session_id = $1
        """,
        digital_session_id,
        schema_name="public",
    )
    if estado_actual and estado_actual["status"] not in ("pending", "completing"):
        log.warning(
            "cierre_digital.abortado doc=%s session=%s estado=%s — no se promueve",
            document_id[:8], str(digital_session_id)[:12], estado_actual["status"],
        )
        return {
            "ok": False,
            "failure_reason": f"sesion_{estado_actual['status']}",
            "tanda_puede_caer": True,
            "auto_link_results": [],
        }

    signed_pdf = await leer_pdf_firmado(schema_name=schema_name, document_id=document_id)

    auto_link_results: list[dict] = []
    if is_numerator and official_number:
        try:
            auto_link_results = await completar_numerador(
                document_id, user_id, schema_name, official_number, signed_pdf,
                reservation_id=reservation_id,
                cas_pre_done=cas_pre_done,
            )
        except NumeratorPreCasError:
            log.error("cierre_digital.pre_cas_failed doc=%s", document_id[:8])
            try:
                from shared.numbering import cancel_number

                await cancel_number(
                    document_id, schema_name=schema_name,
                    reason="numerator_pre_cas_failure",
                    reservation_id=reservation_id,
                )
            except Exception as e:
                log.warning("cierre_digital.pre_cas cancel_number soft-fail: %s", e)
            await _alertar(
                f"[GDI Firma] Fallo pre-CAS numerador — doc={document_id[:8]}",
                f"schema={schema_name}\nEl número fue cancelado.",
            )
            return {"ok": False, "failure_reason": "numerator_partial_failure",
                    "tanda_puede_caer": True,
                    "auto_link_results": []}
        except NumeratorUploadError:
            log.error(
                "cierre_digital.upload_failed doc=%s — el sweeper reencola desde CONFIRMING",
                document_id[:8],
            )
            await _alertar(
                f"[GDI Firma] Fallo upload post-CAS — doc={document_id[:8]}",
                f"schema={schema_name}\nEl número queda CONFIRMING. El sweeper reencola.",
            )
            return {"ok": False, "failure_reason": "numerator_partial_failure",
                    "tanda_puede_caer": False,
                    "auto_link_results": []}
        except DocumentRejectedWhileInQueueError:
            log.critical(
                "cierre_digital.confirmed_and_rejected doc=%s num=%s — revisión manual",
                document_id[:8], official_number,
            )
            await _alertar(
                f"[GDI Firma] Conflicto CONFIRMED+rechazado — {official_number}",
                (f"doc={document_id}\nnum={official_number}\nschema={schema_name}\n\n"
                 "Se firmó (PDF en oficial/) y además fue rechazado. El número NO se "
                 "canceló y el PDF NO se borró. Requiere resolución manual."),
            )
        except StaleReservationError as exc:
            log.error(
                "cierre_digital.stale_reservation doc=%s — la reserva ya no es de "
                "este cierre: %s", document_id[:8], exc,
            )
            return {"ok": False, "failure_reason": "stale_reservation",
                    "tanda_puede_caer": True,
                    "auto_link_results": []}
        except Exception as exc:
            log.error(
                "cierre_digital.post_upload_failed (PDF en oficial/, sweeper reconcilia) "
                "doc=%s: %s", document_id[:8], exc,
            )

    from services.documents.signing.r2_lock import release_signing_lock_R2_success

    await release_signing_lock_R2_success(
        schema_name=schema_name,
        doc_id=document_id,
        signed_pdf=signed_pdf,
        is_numerator=is_numerator,
        number=official_number,
    )

    try:
        await actualizar_firmante(
            document_id, user_id, schema_name, digital_session_id,
            cert.get("cert_serial"), cert.get("cert_subject_cuit"), "autofirma",
        )
    except Exception as e:
        log.warning("cierre_digital.update_signer soft-fail: %s", e)

    try:
        from services.documents.signing.audit_logger import log_signature_event

        await log_signature_event(
            schema_name=schema_name,
            document_id=document_id,
            user_id=user_id,
            signature_method="digital_token",
            result="ok",
            session_id=digital_session_id,
            official_number=official_number,
            cert_serial=cert.get("cert_serial"),
            cert_subject_dn=cert.get("cert_subject_dn"),
            cert_issuer_dn=cert.get("cert_issuer_dn"),
            cert_subject_cuit=cert.get("cert_subject_cuit"),
            cert_not_after=cert.get("cert_not_after"),
            revocation_status=cert.get("revocation_status"),
            tsa_url=cert.get("tsa_url"),
            tsa_time=cert.get("tsa_time"),
        )
    except Exception as e:
        log.warning("cierre_digital.audit_log soft-fail: %s", e)

    await marcar_sesion_digital(digital_session_id, "signed")

    if file_id:
        await limpiar_redis_de_la_sesion(
            schema_name=schema_name, file_id=file_id,
            digital_session_id=digital_session_id,
        )
    await borrar_pdf_firmado(schema_name=schema_name, document_id=document_id)

    log.info(
        "cierre_digital.ok doc=%s num=%s session=%s",
        document_id[:8], official_number, digital_session_id[:12],
    )
    return {"ok": True, "failure_reason": None, "auto_link_results": auto_link_results}
