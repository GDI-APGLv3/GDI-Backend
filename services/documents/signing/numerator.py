"""
Servicios para la numeración de documentos.
Maneja la lógica de negocio para numerar documentos y completar el proceso.
"""

from typing import Dict, Any
from database import fetch_one, fetch_all, execute, transaction
from shared.exceptions import (
    DocumentNotFoundError, ValidationError, DocumentStateError,
    AuthorizationError
)
from shared.validation import validate_document_id, validate_user_id
from fastapi.concurrency import run_in_threadpool
from shared.logging import get_logger

# Import de función centralizada de numeración
from shared.numbering import generate_official_number, reserve_number, confirm_number, cancel_number
from services.shared.signer_data import get_signer_data

logger = get_logger(__name__)

async def sign_document_as_numerator(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Firma un documento como numerador (proceso final).

    MOMENTO 1 (lock corto ~5ms): validaciones + reservar número + INSERT en official_documents.
    MOMENTO 2 (sin lock, retry inteligente): firmar con Notary + subir R2 + UPDATE BD.

    Bugs corregidos en este refactor:
    1. signed_at ahora se pone cuando la firma ocurre realmente (no antes en el INSERT).
    2. signers[].signed_at ahora se llena con la fecha real de firma.
    3. signers[].status ahora pasa de 'pending' a 'signed' correctamente.
    4. El advisory lock dura ~5ms (antes duraba ~3.8s incluyendo Notary + R2).
    5. Retry inteligente: si Notary firmó pero R2 falló, no re-firma, solo reintenta R2.

    Args:
        document_id: UUID del documento
        user_id: UUID del numerador
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con el resultado de la operación

    Raises:
        DocumentNotFoundError: Si el documento no existe o numerador inválido
        ValidationError: Si las validaciones fallan
        DocumentStateError: Si el documento no está en estado correcto
    """
    # Validaciones básicas de formato
    doc_error = await validate_document_id(document_id, schema_name=schema_name)
    if doc_error:
        raise ValidationError(doc_error)

    user_error = await validate_user_id(user_id, schema_name=schema_name)
    if user_error:
        raise ValidationError(user_error)

    logger.info("Iniciando firma de documento como numerador")
    logger.info(f"Document ID: {document_id[:8]}...")
    logger.info(f"User ID: {user_id[:8]}...")

    # ================================================================
    # MOMENTO 1: VALIDACIONES + RESERVAR NÚMERO
    # ================================================================

    logger.info("MOMENTO 1: Validaciones y reserva de número...")

    document_type_acronym = None
    source_type = None
    doc_data = None
    signers_data = None
    signer_sector_ids = None
    current_year = None

    # ============================================================
    # FUSIÓN 1 (P1+P2+P6+P7): rol/estado + firmantes pendientes +
    #   JSON de firmantes + array sector_ids — 1 sola query
    # ============================================================
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
        logger.error("Documento no encontrado o numerador inválido")
        raise DocumentNotFoundError("Documento no encontrado o numerador inválido")

    # --- Guard clauses en el mismo orden que antes ---
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
        logger.error(f"Hay {doc_info['pending_count']} firmantes pendientes")
        raise ValidationError("Aún hay firmantes pendientes. El numerador debe firmar al final.")

    logger.info("Validaciones OK")

    # ============================================================
    # QUERY 2a (M1): datos del documento
    #   reference, content, document_type_id, acronym, source_type,
    #   special_numbering, resume — todo de document_draft + document_types.
    #   Separada de los permisos para que el validador único sea
    #   reutilizable sin necesidad de un draft.
    # ============================================================
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

    # ============================================================
    # QUERY 2b (M1): validador único de permisos de numeración
    #   Usa numbering_permissions.can_user_number_document_type —
    #   fuente única de verdad para TODOS los paths de numeración.
    # ============================================================
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

    # Datos de firmantes ya disponibles desde Fusión 1
    signers_data = doc_info['signers_json'] if doc_info['signers_json'] else []
    signer_sector_ids = doc_info['signer_sector_ids'] if doc_info else None

    from datetime import datetime
    current_year = datetime.now().year

    logger.info("Datos recopilados")

    # ================================================================
    # RESERVAR NÚMERO OFICIAL
    # ================================================================
    logger.info("Reservando número oficial...")

    official_number = None
    department_id = None

    official_number, department_id, sequence = await reserve_number(
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
    logger.info(f"Número reservado: {official_number} (seq={sequence})")

    # Limpiar resume del draft (ya está copiado a official_documents por reserve_number)
    if doc_data.get('resume'):
        await execute(
            "UPDATE document_draft SET resume = NULL WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )
        logger.info("Resume copiado a official y limpiado del draft")

    # ================================================================
    # MOMENTO 2: FIRMAR + CONFIRMAR (sin lock, retry inteligente)
    # ================================================================
    logger.info("MOMENTO 2: Firmando con Notary y confirmando en BD...")

    from services.storage.cloudflare import get_tenant_r2_client
    import httpx

    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    filename = document_id.replace('-', '') + '.pdf'

    signed_pdf_bytes = None  # Cache para retry inteligente
    last_error = None

    for attempt in range(2):
        try:
            # ----------------------------------------------------------
            # 2a. Descargar PDF de R2 tosign + firmar con Notary
            # ----------------------------------------------------------
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

                # 2b. Obtener datos del firmante numerador
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

                # 2c. Firmar con Notary
                logger.info("Firmando con Notary...")
                from services.shared.notary_api import call_notary_sign_pdf
                from services.shared.settings_utils import get_city_from_settings

                city = await get_city_from_settings(schema_name=schema_name)
                logger.info(f"City desde settings: {city}")

                # Documentos importados: firma en página final
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
                    schema_name=schema_name
                )

                logger.info(f"PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

            # ----------------------------------------------------------
            # 2d. Subir PDF firmado a R2 oficial
            # ----------------------------------------------------------
            logger.info(f"Intento {attempt + 1}/2 - Subiendo PDF a R2 oficial...")

            oficial_filename = f"{official_number}.pdf"
            await run_in_threadpool(r2_client.upload_oficial, signed_pdf_bytes, oficial_filename)

            logger.info(f"PDF publicado en R2 oficial: {oficial_filename}")

            # Confirmar reserva en BD (reservation_status RESERVED -> CONFIRMED)
            await confirm_number(document_id, schema_name=schema_name)
            logger.info("Reserva confirmada en official_documents")

            # ----------------------------------------------------------
            # 2e. Confirmar en BD: UPDATEs ATÓMICOS
            # Los 4 UPDATEs de estado del documento van en UNA sola
            # transacción para evitar estados inconsistentes (ej: signed_at
            # seteado pero document_draft sin actualizar) si el worker crashea
            # entre statements. No hay I/O externo aquí (R2/Notary ya completaron).
            # ----------------------------------------------------------
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

                await conn.execute(
                    """
                    UPDATE document_draft
                    SET status = 'signed',
                        document_number = $1,
                        numbered_at = CURRENT_TIMESTAMP,
                        numbered_by = $2,
                        last_modified_at = CURRENT_TIMESTAMP
                    WHERE id = $3
                    """,
                    official_number,
                    user_id,
                    document_id,
                )

            logger.info("BD actualizada - firma confirmada (transacción atómica)")

            # ----------------------------------------------------------
            # 2f. Eliminar PDF de R2 tosign (soft-fail)
            # ----------------------------------------------------------
            try:
                await run_in_threadpool(r2_client.delete_tosign, filename)
                logger.info("PDF eliminado de R2 tosign")
            except Exception as e:
                logger.warning(f"No se pudo eliminar PDF de R2 tosign: {e} (soft-fail)")

            logger.info(f"Documento firmado y numerado exitosamente: {official_number}")

            # Audit log - firma electrónica numerador exitosa
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

            return {
                "success": True,
                "message": "Documento firmado y numerado exitosamente por el numerador",
                "document_id": document_id,
                "numerator_id": user_id,
                "official_number": official_number,
                "document_status": "signed"
            }

        except Exception as e:
            last_error = e
            logger.error(f"Firma intento {attempt + 1}/2 falló: {e}")

            if attempt == 0:
                if signed_pdf_bytes is None:
                    logger.info("Notary falló - próximo intento reintentará desde descarga de PDF")
                else:
                    logger.info("R2 falló después de firma exitosa - próximo intento solo reintentará R2")
                continue

    # ================================================================
    # AMBOS INTENTOS FALLARON
    # ================================================================
    logger.critical(
        f"FIRMA FALLIDA 2 VECES: doc={document_id}, num={official_number}, "
        f"schema={schema_name}, error={last_error}"
    )
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

    # Audit log - firma electrónica numerador fallida
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
    """
    Obtiene documentos asignados a un numerador.

    Args:
        numerator_user_id: UUID del numerador
        status_filter: Filtro por estado (opcional)
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con documentos del numerador
    """
    # Validar usuario
    user_error = await validate_user_id(numerator_user_id, schema_name=schema_name)
    if user_error:
        raise ValidationError(user_error)

    # Construir query con filtros
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

    # Procesar documentos
    documents = []
    for doc in (documents_data or []):
        # Determinar si puede ser numerado
        can_numerate = (
            doc['status'] in ['signed', 'pending_numeration'] and
            doc['completed_signatures'] >= doc['required_signatures'] and
            not doc['official_number']
        )

        # Determinar si puede firmar como numerador
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
