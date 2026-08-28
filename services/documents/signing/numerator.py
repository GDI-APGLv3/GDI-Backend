
from typing import Dict, Any
from database import fetch_one, fetch_all, execute, transaction
from shared.exceptions import (
    DocumentNotFoundError, ValidationError, DocumentStateError,
    AuthorizationError, StaleReservationError, NotaryBreakerOpenError,
    DocumentRejectedWhileInQueueError, SignerTurnPendingError,
)
from shared.validation import validate_document_id, validate_user_id
from fastapi.concurrency import run_in_threadpool
from shared.logging import get_logger

from shared.numbering import reserve_number, confirm_number, finalize_number, cancel_number
from services.shared.signer_data import get_signer_data
from services.documents.signing.lookup_guard import confirm_document_missing

logger = get_logger(__name__)

async def sign_document_as_numerator(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    doc_error = await validate_document_id(document_id, schema_name=schema_name)
    if doc_error:
        raise ValidationError(doc_error)

    user_error = await validate_user_id(user_id, schema_name=schema_name)
    if user_error:
        raise ValidationError(user_error)

    logger.info("Iniciando firma de documento como numerador")
    logger.info(f"Document ID: {document_id[:8]}...")
    logger.info(f"User ID: {user_id[:8]}...")


    logger.info("MOMENTO 1: Validaciones y reserva de número...")

    document_type_acronym = None
    source_type = None
    doc_data = None
    signers_data = None
    signer_sector_ids = None
    current_year = None

    logger.info("PASO 1/2 (M1): Validando numerador y estado del documento...")

    doc_info = await fetch_one(
        """
        SELECT
            dd.id              AS document_id,
            dd.status,
            dd.document_number,
            ds_num.is_numerator,
            ds_num.status      AS signer_status,
            -- P2: count de firmantes no-numerador pendientes
            (
                SELECT COUNT(*)
                FROM document_signers
                WHERE document_id = $1
                  AND is_numerator = false
                  AND (status = 'pending' OR status IS NULL)
            )                  AS pending_count,
            -- P6: snapshot JSON de todos los firmantes para official_documents
            (
                SELECT json_agg(
                    json_build_object(
                        'user_id',       ds2.user_id,
                        'full_name',     u2.full_name,
                        'status',        ds2.status,
                        'is_numerator',  ds2.is_numerator,
                        'signing_order', ds2.signing_order,
                        'signed_at',     ds2.signed_at
                    )
                )
                FROM document_signers ds2
                JOIN users u2 ON ds2.user_id = u2.id
                WHERE ds2.document_id = $1
            )                  AS signers_json,
            -- P7: array de sector_ids de los firmantes para official_documents
            (
                SELECT ARRAY_AGG(DISTINCT u3.sector_id)
                       FILTER (WHERE u3.sector_id IS NOT NULL)
                FROM document_signers ds3
                JOIN users u3 ON ds3.user_id = u3.id
                WHERE ds3.document_id = $1
            )                  AS signer_sector_ids
        FROM document_draft dd
        JOIN document_signers ds_num
          ON dd.id = ds_num.document_id AND ds_num.user_id = $2
        WHERE dd.id = $1
        """,
        document_id,
        user_id,
        schema_name=schema_name,
    )

    if not doc_info:
        logger.warning("Documento no encontrado o numerador inválido — confirmando")
        await confirm_document_missing(
            document_id, schema_name=schema_name, context="numerator.doc_info"
        )
        raise DocumentNotFoundError("Documento no encontrado o numerador inválido")

    if not doc_info['is_numerator']:
        logger.error("Usuario no es numerador del documento")
        raise ValidationError("Usuario no es numerador del documento")

    if doc_info['signer_status'] == 'signed':
        logger.error("El numerador ya firmó este documento")
        raise ValidationError("El numerador ya firmó este documento")

    if doc_info['status'] != 'sent_to_sign':
        logger.error(f"Documento en estado incorrecto: {doc_info['status']}")
        raise DocumentStateError(
            f"Documento en estado '{doc_info['status']}' no puede firmarse como numerador",
            current_state=doc_info['status'],
            required_state="sent_to_sign"
        )

    if doc_info['pending_count'] > 0:
        logger.info(f"Firma fuera de turno: hay {doc_info['pending_count']} firmante(s) pendiente(s)")
        raise SignerTurnPendingError(int(doc_info['pending_count']))

    logger.info("Validaciones OK")

    logger.info("PASO 2/2 (M1): Recopilando datos del documento...")

    doc_data_row = await fetch_one(
        """
        SELECT
            dd.reference,
            dd.content,
            dd.document_type_id,
            dd.resume,
            dt.acronym         AS document_type_acronym,
            dt.type            AS source_type,
            dt.special_numbering
        FROM document_draft dd
        JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE dd.id = $1
        """,
        document_id,
        schema_name=schema_name,
    )

    if not doc_data_row:
        await confirm_document_missing(
            document_id, schema_name=schema_name, context="numerator.doc_data"
        )
        raise DocumentNotFoundError(f"Documento {document_id} no encontrado al leer datos del draft")

    document_type_acronym = doc_data_row['document_type_acronym'] or "DOC"
    source_type = doc_data_row['source_type'] or "HTML"
    is_special_numbering = bool(doc_data_row['special_numbering'])

    doc_data = {
        'reference':        doc_data_row['reference'],
        'content':          doc_data_row['content'] or {},
        'document_type_id': doc_data_row['document_type_id'],
        'resume':           doc_data_row['resume'],
    }

    logger.info("PASO 1b (M1): Validando permisos de titular de repartición y sector...")

    from services.documents.signing.numbering_permissions import (
        can_user_number_document_type,
    )

    has_rank, has_sector, reason = await can_user_number_document_type(
        user_id,
        doc_data['document_type_id'],
        schema_name=schema_name,
    )

    if not has_rank:
        raise AuthorizationError(reason)

    if not has_sector:
        raise AuthorizationError(reason)

    logger.info(f"Permisos OK")

    signers_data = doc_info['signers_json'] if doc_info['signers_json'] else []
    signer_sector_ids = doc_info['signer_sector_ids'] if doc_info else None

    from datetime import datetime
    current_year = datetime.now().year

    logger.info("Datos recopilados")

    logger.info("Reservando número oficial...")

    official_number = None
    department_id = None

    field_defs_row = await fetch_one(
        "SELECT field_definitions FROM document_type_fields WHERE document_type_id = $1",
        doc_data['document_type_id'],
        schema_name=schema_name,
    )
    if field_defs_row is not None and field_defs_row['field_definitions']:
        field_defs = field_defs_row['field_definitions']
        logger.info("Formulario controlado detectado — armando snapshot...")

        from services.documents.ffcc_validator import validate_ffcc_content
        validate_ffcc_content(
            doc_data['content'] if isinstance(doc_data['content'], dict) else {},
            field_defs,
            schema_name=schema_name,
            enforce_required=True,
        )
        logger.info("Validacion FFCC OK (enforce_required=True)")

        doc_data['content'] = {
            "schema": field_defs,
            "data": doc_data['content'] if isinstance(doc_data['content'], dict) else {},
        }
        logger.info(f"Snapshot FFCC armado: {len(field_defs)} campos en schema")

    official_number, department_id, sequence, reservation_id = await reserve_number(
        document_type_acronym=document_type_acronym,
        user_id=user_id,
        year=current_year,
        schema_name=schema_name,
        document_id=document_id,
        reference=doc_data['reference'],
        document_type_id=doc_data['document_type_id'],
        content=doc_data['content'],
        resume=doc_data.get('resume'),
        signers=signers_data,
        signer_sector_ids=signer_sector_ids,
    )
    logger.info(f"Número reservado: {official_number} (seq={sequence}) ticket={reservation_id[:8]}...")

    if doc_data.get('resume'):
        await execute(
            "UPDATE document_draft SET resume = NULL WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )
        logger.info("Resume copiado a official y limpiado del draft")

    logger.info("MOMENTO 2: Firmando con Notary y confirmando en BD...")

    from services.storage.cloudflare import get_tenant_r2_client
    import httpx

    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    filename = document_id.replace('-', '') + '.pdf'


    signed_pdf_bytes = None
    is_confirming = False
    last_error = None

    for attempt in range(2):
        try:
            if signed_pdf_bytes is None:
                logger.info(f"Intento {attempt + 1}/2 - Descargando PDF de R2 tosign...")

                pdf_url = await run_in_threadpool(r2_client.get_tosign_url, filename)
                if not pdf_url:
                    raise ValidationError("No se pudo obtener URL del PDF desde R2 tosign")

                async with httpx.AsyncClient(timeout=30.0) as client:
                    pdf_response = await client.get(pdf_url)
                    pdf_response.raise_for_status()
                    pdf_bytes = pdf_response.content

                logger.info(f"PDF descargado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

                logger.info("Obteniendo datos del numerador...")
                try:
                    signer_data = await get_signer_data(user_id, schema_name=schema_name)
                    signer_name = signer_data['full_name']
                    signer_seal = signer_data['seal']
                    signer_department = signer_data['department_name']
                    signer_municipality = signer_data['municipality_name']
                except ValidationError:
                    logger.error("Datos del numerador no encontrados")
                    raise ValidationError("No se encontraron datos del numerador")

                logger.info(f"Firmante: {signer_name} | Depto: {signer_department} | Municipio: {signer_municipality}")

                logger.info("Firmando con Notary...")
                from services.shared.notary_api import call_notary_sign_pdf
                from services.shared.settings_utils import get_city_from_settings

                city = await get_city_from_settings(schema_name=schema_name)
                logger.info(f"City desde settings: {city}")

                stamp_position = "last" if source_type == 'Importado' else ""
                if stamp_position:
                    logger.info(f"Documento importado detectado - stamp_position={stamp_position}")


                signed_pdf_bytes = await call_notary_sign_pdf(
                    pdf_bytes=pdf_bytes,
                    signer_name=signer_name,
                    signer_seal=signer_seal,
                    signer_department=signer_department,
                    signer_municipality=signer_municipality,
                    official_number=official_number,
                    city=city,
                    stamp_position=stamp_position,
                    tenant_id=schema_name,
                    schema_name=schema_name,
                    defer_timestamp=True,
                )

                logger.info(f"PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

            if not is_confirming:
                logger.info(f"Intento {attempt + 1}/2 - CAS RESERVED→CONFIRMING...")
                await confirm_number(document_id, reservation_id, schema_name=schema_name)
                is_confirming = True
                logger.info("CAS OK: reserva en CONFIRMING")

            logger.info(f"Intento {attempt + 1}/2 - Subiendo PDF a R2 oficial...")

            from services.storage.pdf_location import (
                target_pdf_location, persist_pdf_location, effective_pdf_location,
            )
            _target_loc = target_pdf_location()
            oficial_filename = f"{official_number}.pdf"
            _upload_res = await run_in_threadpool(
                r2_client.upload_oficial, signed_pdf_bytes, oficial_filename, _target_loc
            )
            _effective_loc = effective_pdf_location(_upload_res, _target_loc)
            await persist_pdf_location(document_id, _effective_loc, schema_name=schema_name)

            logger.info(f"PDF publicado en R2 {_effective_loc}: {oficial_filename}")

            await finalize_number(document_id, reservation_id, schema_name=schema_name)
            logger.info("Reserva CONFIRMED en official_documents")

            import asyncio as _asyncio
            from services.storage.publish_public import maybe_publish_official_pdf
            from config.constants import PUBLISH_PUBLIC_MAX_RETRIES

            for _attempt in range(1, PUBLISH_PUBLIC_MAX_RETRIES + 1):
                _published = await maybe_publish_official_pdf(
                    schema_name=schema_name,
                    official_number=official_number,
                    document_id=document_id,
                    signed_pdf_bytes=signed_pdf_bytes,
                )
                if _published:
                    break
                logger.warning(
                    f"numerator.publish_public_retry num={official_number} "
                    f"attempt={_attempt}/{PUBLISH_PUBLIC_MAX_RETRIES}"
                )
                if _attempt < PUBLISH_PUBLIC_MAX_RETRIES:
                    await _asyncio.sleep(1.0)
            else:
                logger.error(
                    f"publish_public_failed document_id={document_id} "
                    f"schema={schema_name} num={official_number} "
                    f"attempts={PUBLISH_PUBLIC_MAX_RETRIES}"
                )

            logger.info("Confirmando firma en BD (UPDATE atómico)...")

            async with transaction(
                schema_name=schema_name, user_id=user_id, auth_source="numerator"
            ) as conn:
                await conn.execute(
                    """
                    UPDATE official_documents
                    SET signed_at = CURRENT_TIMESTAMP
                    WHERE id = $1 AND signed_at IS NULL
                    """,
                    document_id,
                )

                await conn.execute(
                    """
                    UPDATE official_documents
                    SET signers = (
                        SELECT jsonb_agg(
                            CASE WHEN s->>'user_id' = $1
                                THEN jsonb_set(
                                    jsonb_set(s, '{status}', '"signed"'),
                                    '{signed_at}', to_jsonb(to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'))
                                )
                                ELSE s
                            END
                        )
                        FROM jsonb_array_elements(signers) s
                    )
                    WHERE id = $2
                    """,
                    user_id,
                    document_id,
                )

                await conn.execute(
                    """
                    UPDATE document_signers
                    SET status = 'signed', signed_at = CURRENT_TIMESTAMP
                    WHERE document_id = $1 AND user_id = $2
                    """,
                    document_id,
                    user_id,
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
                    official_number,
                    user_id,
                    document_id,
                )
                if not _draft_rows:
                    raise DocumentRejectedWhileInQueueError(document_id)

            logger.info("BD actualizada - firma confirmada (transacción atómica)")

            try:
                await run_in_threadpool(r2_client.delete_tosign, filename)
                logger.info("PDF eliminado de R2 tosign")
            except Exception as e:
                logger.warning(f"No se pudo eliminar PDF de R2 tosign: {e} (soft-fail)")

            try:
                from services.documents.lifecycle.images import purge_document_images
                await purge_document_images(document_id, schema_name=schema_name)
            except Exception as e:
                logger.warning(f"No se pudieron purgar imagenes de documento (soft-fail): {e}")

            from services.documents.lifecycle.embedded_files import promote_embedded_files_to_official
            await promote_embedded_files_to_official(document_id, document_id, schema_name=schema_name)

            logger.info(f"Documento firmado y numerado exitosamente: {official_number}")

            try:
                from services.documents.signing.audit_logger import log_signature_event
                await log_signature_event(
                    schema_name=schema_name,
                    document_id=document_id,
                    user_id=user_id,
                    signature_method="electronic",
                    result="ok",
                    official_number=official_number,
                    r2_object_key=f"{official_number}.pdf",
                )
            except Exception as _audit_err:
                logger.warning(f"audit_log fallo (soft-fail): {_audit_err}")

            auto_link_results: list = []
            try:
                from services.shared.auto_link_trigger import collect_auto_link_results
                auto_link_results = await collect_auto_link_results(
                    document_id, schema_name=schema_name
                )
            except Exception as _al_err:
                logger.warning(f"collect_auto_link_results soft-fail (no bloquea firma): {_al_err}")


            return {
                "success": True,
                "message": "Documento firmado y numerado exitosamente por el numerador",
                "document_id": document_id,
                "numerator_id": user_id,
                "official_number": official_number,
                "document_status": "signed",
                "auto_link_results": auto_link_results,
            }

        except StaleReservationError as e:
            logger.error(
                f"FIRMA ABORTADA por reserva vencida: doc={document_id[:8]}... "
                f"ticket={reservation_id[:8]}... error={e}"
            )
            raise ValidationError(
                "Tu reserva de número expiró mientras se procesaba la firma. "
                "Por favor, volvé a intentar la firma desde el documento."
            )

        except NotaryBreakerOpenError:
            logger.critical(
                f"NOTARY BREAKER OPEN: doc={document_id[:8]}... "
                f"num={official_number} is_confirming={is_confirming} "
                "— propagando NotaryBreakerOpenError sin reintentar"
            )
            if not is_confirming:
                try:
                    await cancel_number(
                        document_id,
                        schema_name=schema_name,
                        reason="breaker_open_before_cas",
                    )
                    logger.info(f"Número {official_number} cancelado (breaker_open_path)")
                except Exception as cancel_err:
                    logger.error(f"cancel_number soft-fail (breaker_open): {cancel_err}")
            raise

        except DocumentRejectedWhileInQueueError:
            logger.critical(
                f"numerator.confirmed_and_rejected: doc={document_id[:8]}... "
                f"num={official_number} schema={schema_name} — "
                "draft rechazado tras CONFIRMED; PDF preservado, número NO cancelado. "
                "Requiere revisión manual."
            )
            try:
                from shared.alerts import send_alert_mail
                await send_alert_mail(
                    subject=f"[GDI Numerador] Conflicto CONFIRMED+rechazado — {official_number}",
                    body=(
                        f"doc={document_id}\nnum={official_number}\nschema={schema_name}\n\n"
                        f"El documento fue numerado (CONFIRMED, PDF en oficial/) "
                        f"pero también fue rechazado durante la ventana de firma síncrona. "
                        f"El número NO fue cancelado y el PDF NO fue borrado. "
                        f"Requiere revisión y resolución manual."
                    ),
                )
            except Exception as _ae:
                logger.error(f"numerator.confirmed_and_rejected.alert_err: {_ae}")
            raise ValidationError(
                "El documento fue rechazado mientras se procesaba la numeración. "
                "El número ya fue emitido. Contacte a soporte para resolución manual."
            )

        except Exception as e:
            last_error = e
            logger.error(f"Firma intento {attempt + 1}/2 falló: {e}")

            if attempt == 0:
                if signed_pdf_bytes is None:
                    logger.info("Notary falló - próximo intento reintentará desde descarga de PDF")
                elif is_confirming:
                    logger.info("R2/finalize falló en estado CONFIRMING - próximo intento reintenta upload/finalize")
                else:
                    logger.info("Falló antes del CAS - próximo intento reintenta desde Notary")
                continue

    logger.critical(
        f"FIRMA FALLIDA 2 VECES: doc={document_id}, num={official_number}, "
        f"schema={schema_name}, error={last_error}"
    )

    if is_confirming:
        logger.critical(
            f"El número {official_number} queda en estado CONFIRMING para doc={document_id}. "
            f"El sweeper lo resolverá al expirar la sesión."
        )
    else:
        logger.critical(
            f"official_documents queda con signed_at=NULL para doc={document_id}. "
            f"El número {official_number} será cancelado y quedará reciclable."
        )
        try:
            await cancel_number(
                document_id,
                schema_name=schema_name,
                reason=f"firma_fallida_2_intentos: {str(last_error)[:400]}",
            )
        except Exception as cancel_err:
            logger.error(f"cancel_number fallo (soft-fail): {cancel_err}")

    try:
        from services.documents.signing.audit_logger import log_signature_event
        await log_signature_event(
            schema_name=schema_name,
            document_id=document_id,
            user_id=user_id,
            signature_method="electronic",
            result="fail",
            failure_reason=f"firma_fallida_2_intentos: {str(last_error)[:300]}",
            official_number=official_number,
        )
    except Exception as _audit_err:
        logger.warning(f"audit_log fallo en path de error (soft-fail): {_audit_err}")

    raise ValidationError("Error al firmar documento, por favor intente mas tarde")


async def get_numerator_documents(numerator_user_id: str, status_filter: str = None, *, schema_name: str) -> Dict[str, Any]:
    user_error = await validate_user_id(numerator_user_id, schema_name=schema_name)
    if user_error:
        raise ValidationError(user_error)

    where_conditions = ["ds.user_id = $1 AND ds.is_numerator = true"]
    params: list = [numerator_user_id]

    if status_filter:
        params.append(status_filter)
        where_conditions.append(f"d.status = ${len(params)}")

    where_clause = " AND ".join(where_conditions)
    params.append(numerator_user_id)
    numerator_param_idx = len(params)

    query = f"""
        SELECT d.id, d.reference, d.status, d.official_number, d.created_at, d.updated_at,
               dt.name as document_type_name, dt.acronym as document_type_acronym,
               creator.first_name || ' ' || creator.last_name as creator_name,
               (SELECT COUNT(*) FROM document_signatures dsig
                WHERE dsig.document_id = d.id) as completed_signatures,
               (SELECT COUNT(*) FROM document_signers dsign
                WHERE dsign.document_id = d.id AND dsign.is_numerator = false) as required_signatures,
               (SELECT COUNT(*) FROM document_signatures dsig
                WHERE dsig.document_id = d.id AND dsig.user_id = ${numerator_param_idx}) as numerator_signed
        FROM documents d
        JOIN document_types dt ON d.document_type_id = dt.id
        JOIN users creator ON d.creator_id = creator.id
        JOIN document_signers ds ON d.id = ds.document_id
        WHERE {where_clause}
        ORDER BY d.updated_at DESC, d.created_at DESC
    """

    documents_data = await fetch_all(query, *params, schema_name=schema_name)

    documents = []
    for doc in (documents_data or []):
        can_numerate = (
            doc['status'] in ['signed', 'pending_numeration'] and
            doc['completed_signatures'] >= doc['required_signatures'] and
            not doc['official_number']
        )

        can_sign_as_numerator = (
            doc['status'] == 'pending_numeration' and
            doc['official_number'] and
            doc['numerator_signed'] == 0
        )

        documents.append({
            "document_id": doc['id'],
            "reference": doc['reference'],
            "status": doc['status'],
            "official_number": doc['official_number'],
            "document_type": {
                "name": doc['document_type_name'],
                "acronym": doc['document_type_acronym']
            },
            "creator_name": doc['creator_name'],
            "signature_progress": {
                "completed": doc['completed_signatures'],
                "required": doc['required_signatures']
            },
            "numerator_actions": {
                "can_numerate": can_numerate,
                "can_sign_as_numerator": can_sign_as_numerator,
                "already_signed": doc['numerator_signed'] > 0
            },
            "created_at": doc['created_at'].isoformat() if doc['created_at'] else None,
            "updated_at": doc['updated_at'].isoformat() if doc['updated_at'] else None
        })

    return {
        "documents": documents,
        "total": len(documents),
        "filters_applied": {
            "status": status_filter
        }
    }
