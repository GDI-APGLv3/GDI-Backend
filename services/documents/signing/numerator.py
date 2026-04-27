"""
Servicios para la numeración de documentos.
Maneja la lógica de negocio para numerar documentos y completar el proceso.
"""

import json
from typing import Dict, Any
from database import get_db_connection, execute_query
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
    doc_error = validate_document_id(document_id, schema_name=schema_name)
    if doc_error:
        raise ValidationError(doc_error)

    user_error = validate_user_id(user_id, schema_name=schema_name)
    if user_error:
        raise ValidationError(user_error)

    logger.info("Iniciando firma de documento como numerador")
    logger.info(f"Document ID: {document_id[:8]}...")
    logger.info(f"User ID: {user_id[:8]}...")

    # ================================================================
    # MOMENTO 1: VALIDACIONES + RESERVAR NÚMERO (conexión corta)
    # ================================================================

    logger.info("MOMENTO 1: Validaciones y reserva de número...")

    document_type_acronym = None
    source_type = None
    doc_data = None
    signers_data = None
    signer_sector_ids = None
    current_year = None

    with get_db_connection(schema_name) as conn:
        cursor = conn.cursor()

        # ============================================================
        # PASO 1: VALIDACIONES PREVIAS
        # ============================================================
        logger.info("PASO 1/2 (M1): Validando numerador y estado del documento...")

        # Verificar que es numerador y el documento está listo
        check_query = """
            SELECT dd.id as document_id, dd.status, dd.document_number,
                   ds.is_numerator, ds.status as signer_status
            FROM document_draft dd
            JOIN document_signers ds ON dd.id = ds.document_id
            WHERE dd.id = %s AND ds.user_id = %s
        """
        cursor.execute(check_query, (document_id, user_id))
        doc_info = cursor.fetchone()

        if not doc_info:
            logger.error("Documento no encontrado o numerador inválido")
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

        # Verificar que todos los firmantes no-numeradores hayan firmado
        remaining_signers_query = """
            SELECT COUNT(*) as pending_count
            FROM document_signers
            WHERE document_id = %s
            AND is_numerator = false
            AND (status = 'pending' OR status IS NULL)
        """
        cursor.execute(remaining_signers_query, (document_id,))
        pending_result = cursor.fetchone()

        if pending_result['pending_count'] > 0:
            logger.error(f"Hay {pending_result['pending_count']} firmantes pendientes")
            raise ValidationError("Aún hay firmantes pendientes. El numerador debe firmar al final.")

        logger.info("Validaciones OK")

        # ============================================================
        # PASO 1b: VALIDAR RANK Y DEPARTAMENTO DEL NUMERADOR
        # ============================================================
        logger.info("PASO 1b (M1): Validando permisos de rank y departamento...")

        rank_dept_query = """
            SELECT
                ur.name as user_rank_name,
                ur.level as user_rank_level,
                rr.name as required_rank_name,
                rr.level as required_rank_level,
                dt.name as doc_type_name,
                dep.id as user_department_id,
                dep.name as user_department_name,
                CASE
                    WHEN rr.level IS NULL THEN true
                    WHEN ur.level IS NULL THEN false
                    WHEN ur.level <= rr.level THEN true
                    ELSE false
                END as has_rank_permission,
                CASE
                    WHEN NOT EXISTS(
                        SELECT 1 FROM enabled_document_types_by_sector
                        WHERE document_type_id = dd.document_type_id
                    ) THEN true
                    WHEN EXISTS(
                        SELECT 1 FROM enabled_document_types_by_sector
                        WHERE document_type_id = dd.document_type_id
                        AND sector_id = u.sector_id
                    ) THEN true
                    WHEN EXISTS(
                        SELECT 1 FROM enabled_document_types_by_sector edts
                        WHERE edts.document_type_id = dd.document_type_id
                        AND edts.sector_id IN (
                            SELECT usp.sector_id
                            FROM user_sector_permissions usp
                            WHERE usp.user_id = u.id AND usp.can_edit = true
                        )
                    ) THEN true
                    ELSE false
                END as has_sector_permission
            FROM document_draft dd
            JOIN document_types dt ON dd.document_type_id = dt.id
            JOIN users u ON u.id = %s
            LEFT JOIN user_seals us ON u.id = us.user_id
            LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
            LEFT JOIN ranks ur ON cs.rank_id = ur.id
            LEFT JOIN sectors sec ON u.sector_id = sec.id
            LEFT JOIN departments dep ON sec.department_id = dep.id
            LEFT JOIN document_types_allowed_by_rank dtabr ON dt.id = dtabr.document_type_id
            LEFT JOIN ranks rr ON dtabr.rank_id = rr.id
            WHERE dd.id = %s
        """
        cursor.execute(rank_dept_query, (user_id, document_id))
        perm_result = cursor.fetchone()

        if perm_result:
            # Validar RANK
            if not perm_result['has_rank_permission']:
                user_rank = perm_result['user_rank_name'] or "sin rank"
                required_rank = perm_result['required_rank_name'] or "desconocido"
                doc_type = perm_result['doc_type_name']
                raise AuthorizationError(
                    f"Rank insuficiente para numerar '{doc_type}'. "
                    f"Se requiere '{required_rank}' o superior, "
                    f"pero el usuario tiene rank '{user_rank}'"
                )

            # Validar SECTOR
            if not perm_result['has_sector_permission']:
                dept_name = perm_result['user_department_name'] or "sin departamento"
                doc_type = perm_result['doc_type_name']
                raise AuthorizationError(
                    f"El sector del departamento '{dept_name}' no tiene habilitado "
                    f"el tipo de documento '{doc_type}'"
                )

            logger.info(f"Permisos OK - rank: {perm_result['user_rank_name'] or 'N/A'}, dept: {perm_result['user_department_name'] or 'N/A'}")

        # ============================================================
        # PASO 2 (M1): OBTENER DATOS DEL DOCUMENTO PARA generate_official_number
        # ============================================================
        logger.info("PASO 2/2 (M1): Recopilando datos del documento...")

        # Tipo de documento + source_type (necesario para stamp_position en MOMENTO 2)
        # También se lee special_numbering del tenant (tabla del tenant, NO global)
        type_query = """
            SELECT dt.acronym, dt.type as source_type,
                   dt.special_numbering
            FROM document_draft dd
            JOIN document_types dt ON dd.document_type_id = dt.id
            WHERE dd.id = %s
        """
        cursor.execute(type_query, (document_id,))
        type_result = cursor.fetchone()

        document_type_acronym = type_result['acronym'] if type_result else "DOC"
        source_type = type_result['source_type'] if type_result else "HTML"
        is_special_numbering = bool(type_result['special_numbering']) if type_result else False

        # Datos del documento (reference, content, document_type_id, resume)
        doc_data_query = """
            SELECT dd.reference, dd.content::text as content_text,
                   dd.document_type_id, dd.resume
            FROM document_draft dd
            WHERE dd.id = %s
        """
        cursor.execute(doc_data_query, (document_id,))
        doc_data_row = cursor.fetchone()

        doc_data = {
            'reference': doc_data_row['reference'],
            'content': json.loads(doc_data_row['content_text']) if doc_data_row['content_text'] else {},
            'document_type_id': doc_data_row['document_type_id'],
            'resume': doc_data_row['resume'],
        }

        # Firmantes del documento (con status='pending' y signed_at=null tal como están)
        signers_query = """
            SELECT json_agg(
                json_build_object(
                    'user_id', ds.user_id,
                    'full_name', u.full_name,
                    'status', ds.status,
                    'is_numerator', ds.is_numerator,
                    'signing_order', ds.signing_order,
                    'signed_at', ds.signed_at
                )
            ) as signers
            FROM document_signers ds
            JOIN users u ON ds.user_id = u.id
            WHERE ds.document_id = %s
        """
        cursor.execute(signers_query, (document_id,))
        signers_result = cursor.fetchone()
        signers_data = signers_result['signers'] if signers_result else []

        # Sector IDs de todos los firmantes
        signer_sectors_query = """
            SELECT ARRAY_AGG(DISTINCT u.sector_id) FILTER (WHERE u.sector_id IS NOT NULL) as sector_ids
            FROM document_signers ds
            JOIN users u ON ds.user_id = u.id
            WHERE ds.document_id = %s
        """
        cursor.execute(signer_sectors_query, (document_id,))
        signer_sectors_result = cursor.fetchone()
        signer_sector_ids = signer_sectors_result['sector_ids'] if signer_sectors_result else None

        # Año actual
        from datetime import datetime
        current_year = datetime.now().year

        logger.info("Datos recopilados - cerrando conexión de validaciones")
    # Conexión de validaciones cerrada y devuelta al pool

    # ================================================================
    # RESERVAR NÚMERO OFICIAL
    # ================================================================
    # reserve_number() detecta reintentos internamente (via reservation_status='RESERVED')
    # y reutiliza la reserva existente. No es necesario chequear huérfanos antes.
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
        with get_db_connection(schema_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE document_draft SET resume = NULL WHERE id = %s",
                (document_id,)
            )
            conn.commit()
        logger.info("Resume copiado a official y limpiado del draft")

    # ================================================================
    # MOMENTO 2: FIRMAR + CONFIRMAR (sin lock, retry inteligente)
    # ================================================================
    logger.info("MOMENTO 2: Firmando con Notary y confirmando en BD...")

    from services.storage.cloudflare import get_tenant_r2_client
    import httpx

    r2_client = get_tenant_r2_client(schema_name=schema_name)
    filename = document_id.replace('-', '') + '.pdf'

    signed_pdf_bytes = None  # Cache para retry inteligente
    last_error = None

    for attempt in range(2):
        try:
            # ----------------------------------------------------------
            # 2a. Descargar PDF de R2 tosign + firmar con Notary
            #     Solo si todavía no tenemos signed_pdf_bytes (retry inteligente)
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
                    signer_data = get_signer_data(user_id, schema_name=schema_name)
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

                # City desde settings del tenant (requiere conexión corta)
                with get_db_connection(schema_name) as conn:
                    city_cursor = conn.cursor()
                    city = get_city_from_settings(cursor=city_cursor)

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
            #     Si falla aquí (y signed_pdf_bytes ya existe), el retry
            #     no vuelve a firmar: solo reintenta el upload.
            # ----------------------------------------------------------
            logger.info(f"Intento {attempt + 1}/2 - Subiendo PDF a R2 oficial...")

            oficial_filename = f"{official_number}.pdf"
            await run_in_threadpool(r2_client.upload_oficial, signed_pdf_bytes, oficial_filename)

            logger.info(f"PDF publicado en R2 oficial: {oficial_filename}")

            # Confirmar reserva en BD (reservation_status RESERVED -> CONFIRMED)
            await confirm_number(document_id, schema_name=schema_name)
            logger.info("Reserva confirmada en official_documents")

            # ----------------------------------------------------------
            # 2e. Confirmar en BD: UPDATE (NO INSERT - ya se hizo en MOMENTO 1)
            # ----------------------------------------------------------
            logger.info("Confirmando firma en BD (UPDATE)...")

            with get_db_connection(schema_name) as conn:
                cursor = conn.cursor()

                # UPDATE official_documents: signed_at + signers
                # content NO se toca (ya tiene datos reales del MOMENTO 1)
                cursor.execute(
                    """
                    UPDATE official_documents
                    SET signed_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND signed_at IS NULL
                    """,
                    (document_id,)
                )

                # Actualizar el numerador en el array signers con jsonb_set
                # (no pisa el array completo, solo modifica el elemento del numerador)
                cursor.execute(
                    """
                    UPDATE official_documents
                    SET signers = (
                        SELECT jsonb_agg(
                            CASE WHEN s->>'user_id' = %s
                                THEN jsonb_set(
                                    jsonb_set(s, '{status}', '"signed"'),
                                    '{signed_at}', to_jsonb(to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'))
                                )
                                ELSE s
                            END
                        )
                        FROM jsonb_array_elements(signers) s
                    )
                    WHERE id = %s
                    """,
                    (user_id, document_id)
                )

                # Marcar numerador como signed en document_signers
                cursor.execute(
                    """
                    UPDATE document_signers
                    SET status = 'signed', signed_at = CURRENT_TIMESTAMP
                    WHERE document_id = %s AND user_id = %s
                    """,
                    (document_id, user_id)
                )

                # Actualizar document_draft con número oficial y estado final
                cursor.execute(
                    """
                    UPDATE document_draft
                    SET status = 'signed',
                        document_number = %s,
                        numbered_at = CURRENT_TIMESTAMP,
                        numbered_by = %s,
                        last_modified_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (official_number, user_id, document_id)
                )

                conn.commit()

            logger.info("BD actualizada - firma confirmada")

            # ----------------------------------------------------------
            # 2f. Eliminar PDF de R2 tosign (soft-fail)
            # ----------------------------------------------------------
            try:
                r2_client.delete_tosign(filename)
                logger.info("PDF eliminado de R2 tosign")
            except Exception as e:
                logger.warning(f"No se pudo eliminar PDF de R2 tosign: {e} (soft-fail)")

            logger.info(f"Documento firmado y numerado exitosamente: {official_number}")

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
                # Si signed_pdf_bytes es None: Notary falló → reintentar todo desde descarga
                # Si signed_pdf_bytes existe: R2 falló → próximo intento solo reintenta R2
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

    # Cancelar reserva (reservation_status RESERVED -> CANCELLED, número queda reciclable)
    # cancel_number() también envía mail de alerta internamente (soft-fail)
    try:
        await cancel_number(
            document_id,
            schema_name=schema_name,
            reason=f"firma_fallida_2_intentos: {str(last_error)[:400]}",
        )
    except Exception as cancel_err:
        logger.error(f"cancel_number fallo (soft-fail): {cancel_err}")

    raise ValidationError("Error al firmar documento, por favor intente mas tarde")


def get_numerator_documents(numerator_user_id: str, status_filter: str = None, *, schema_name: str) -> Dict[str, Any]:
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
    user_error = validate_user_id(numerator_user_id, schema_name=schema_name)
    if user_error:
        raise ValidationError(user_error)

    # Construir query con filtros
    where_conditions = ["ds.user_id = %s AND ds.is_numerator = true"]
    params = [numerator_user_id]

    if status_filter:
        where_conditions.append("d.status = %s")
        params.append(status_filter)

    where_clause = " AND ".join(where_conditions)

    query = f"""
        SELECT d.id, d.reference, d.status, d.official_number, d.created_at, d.updated_at,
               dt.name as document_type_name, dt.acronym as document_type_acronym,
               creator.first_name || ' ' || creator.last_name as creator_name,
               (SELECT COUNT(*) FROM document_signatures dsig
                WHERE dsig.document_id = d.id) as completed_signatures,
               (SELECT COUNT(*) FROM document_signers dsign
                WHERE dsign.document_id = d.id AND dsign.is_numerator = false) as required_signatures,
               (SELECT COUNT(*) FROM document_signatures dsig
                WHERE dsig.document_id = d.id AND dsig.user_id = %s) as numerator_signed
        FROM documents d
        JOIN document_types dt ON d.document_type_id = dt.id
        JOIN users creator ON d.creator_id = creator.id
        JOIN document_signers ds ON d.id = ds.document_id
        WHERE {where_clause}
        ORDER BY d.updated_at DESC, d.created_at DESC
    """

    # Agregar numerator_user_id para la subconsulta de firma del numerador
    final_params = params + [numerator_user_id]

    documents_data = execute_query(query, final_params, schema_name=schema_name)

    # Procesar documentos
    documents = []
    for doc in documents_data:
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
