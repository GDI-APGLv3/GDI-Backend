"""
Servicios para la numeración de documentos.
Maneja la lógica de negocio para numerar documentos y completar el proceso.
"""

from typing import Dict, Any
from database import get_db_connection, execute_transaction, execute_query
from shared.exceptions import (
    DocumentNotFoundError, ValidationError, DocumentStateError,
    NumeratorRequiredError, DocumentAlreadyRejectedError,
    AuthorizationError
)
from shared.validation import validate_document_id, validate_user_id
from shared.utils import generate_uuid
import uuid
from fastapi.concurrency import run_in_threadpool
from shared.logging import get_logger

# Import de función centralizada de numeración
from shared.numbering import generate_official_number
from services.shared.signer_data import get_signer_data

logger = get_logger(__name__)

async def numerate_document(document_id: str, numerator_user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Numera un documento asignándole un número oficial.

    Args:
        document_id: UUID del documento
        numerator_user_id: UUID del usuario numerador
        schema_name: Schema del tenant (multi-tenant, compatible con PgBouncer transaction mode)

    Returns:
        Dict con el resultado de la numeración

    Raises:
        DocumentNotFoundError: Si el documento no existe
        DocumentStateError: Si el documento no puede numerarse
        ValidationError: Si los datos son inválidos
    """
    # Validaciones básicas
    doc_error = validate_document_id(document_id)
    if doc_error:
        raise ValidationError(doc_error)

    user_error = validate_user_id(numerator_user_id)
    if user_error:
        raise ValidationError(user_error)

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Verificar documento y su estado
            doc_query = """
                SELECT d.id, d.reference, d.status, d.official_number,
                       dt.name as document_type_name, dt.acronym as document_type_acronym
                FROM documents d
                JOIN document_types dt ON d.document_type_id = dt.id
                WHERE d.id = %s
            """
            cursor.execute(doc_query, (document_id,))
            document = cursor.fetchone()

            if not document:
                raise DocumentNotFoundError(document_id)

            # Verificar estado del documento
            if document['status'] == 'rejected':
                raise DocumentAlreadyRejectedError(document_id)

            if document['status'] not in ['signed', 'pending_numeration']:
                raise DocumentStateError(
                    f"Documento en estado '{document['status']}' no puede numerarse",
                    current_state=document['status'],
                    required_state="signed o pending_numeration"
                )

            # Verificar que ya no tiene número oficial
            if document['official_number']:
                raise ValidationError(f"El documento ya tiene número oficial: {document['official_number']}")

            # Verificar que el usuario es numerador
            numerator_query = """
                SELECT user_id, is_numerator
                FROM document_signers
                WHERE document_id = %s AND user_id = %s AND is_numerator = true
            """
            cursor.execute(numerator_query, (document_id, numerator_user_id))
            numerator_info = cursor.fetchone()

            if not numerator_info:
                raise NumeratorRequiredError(document_id)

            # Verificar que todas las firmas requeridas estén completadas
            signatures_check_query = """
                SELECT
                    COUNT(ds.*) as required_signers,
                    COUNT(dsig.*) as completed_signatures
                FROM document_signers ds
                LEFT JOIN document_signatures dsig ON ds.document_id = dsig.document_id
                                                  AND ds.user_id = dsig.user_id
                WHERE ds.document_id = %s AND ds.is_numerator = false
            """
            cursor.execute(signatures_check_query, (document_id,))
            signatures_status = cursor.fetchone()

            if signatures_status['required_signers'] != signatures_status['completed_signatures']:
                raise DocumentStateError(
                    "No se puede numerar: faltan firmas requeridas",
                    current_state=f"{signatures_status['completed_signatures']}/{signatures_status['required_signers']} firmas",
                    required_state="Todas las firmas completadas"
                )

            # Generar número oficial
            from datetime import datetime
            current_year = datetime.now().year
            official_number, department_id, global_sequence = await _generate_official_number(
                document['document_type_acronym'],
                numerator_user_id,
                current_year
            )

            # Preparar operaciones de base de datos
            operations = []

            # Actualizar documento con número oficial y cambiar estado
            update_doc_query = """
                UPDATE documents
                SET official_number = %s,
                    status = 'pending_numeration',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """
            operations.append((update_doc_query, [official_number, document_id]))

            # Registrar la numeración
            numeration_id = str(uuid.uuid4())
            insert_numeration = """
                INSERT INTO document_numerations (id, document_id, numerator_id, official_number, created_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            """
            operations.append((insert_numeration, [numeration_id, document_id, numerator_user_id, official_number]))

            # Ejecutar operaciones
            with execute_transaction(schema_name=schema_name) as (conn, cursor):
                for query, params in operations:
                    cursor.execute(query, params)

            return {
                "success": True,
                "message": "Documento numerado exitosamente",
                "document_id": document_id,
                "official_number": official_number,
                "numeration_id": numeration_id,
                "status": "pending_numeration"
            }

async def numerate_and_reserve_document(document_id: str, numerator_user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Numera y reserva un documento (proceso completo).

    Args:
        document_id: UUID del documento
        numerator_user_id: UUID del usuario numerador
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con el resultado del proceso completo
    """
    # Primero numerar el documento
    numeration_result = await numerate_document(document_id, numerator_user_id)

    if not numeration_result.get("success"):
        return numeration_result

    official_number = numeration_result["official_number"]

    try:
        # Llamar a la API externa para el proceso final
        api_result = await call_legal_orchestrator_sign_document_numerator(
            document_id,
            numerator_user_id,
            official_number
        )

        if api_result.get("success"):
            # Actualizar documento a estado completado
            update_query = """
                UPDATE documents
                SET status = 'completed', updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """
            execute_query(update_query, [document_id], schema_name=schema_name)

            return {
                "success": True,
                "message": "Documento numerado y completado exitosamente",
                "document_id": document_id,
                "official_number": official_number,
                "status": "completed",
                "api_result": api_result
            }
        else:
            return {
                "success": False,
                "message": f"Error en API externa: {api_result.get('message', 'Error desconocido')}",
                "document_id": document_id,
                "official_number": official_number
            }

    except Exception as e:
        return {
            "success": False,
            "message": f"Error en proceso de numeración: {str(e)}",
            "document_id": document_id,
            "official_number": official_number
        }

async def sign_document_as_numerator(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Firma un documento como numerador (proceso final).

    REFACTORIZADO: Usa función centralizada de numeración y rollback automático.

    Proceso:
    1. Validaciones previas (numerador, estado del documento, firmantes pendientes)
    2. Generar número oficial con lock ultra corto (10-20ms)
    3. Insertar en official_documents
    4. Llamar API externa de firma (sin lock, en paralelo)
    5. Actualizar document_signers y document_draft
    6. Commit (o rollback automático si falla)

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

    with get_db_connection(schema_name) as conn:
        cursor = conn.cursor()

        try:
            # ============================================================
            # PASO 1: VALIDACIONES PREVIAS
            # ============================================================
            logger.info("PASO 1/6: Validando numerador y estado del documento...")

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
            logger.info("PASO 1b: Validando permisos de rank y departamento...")

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
            # PASO 2: VERIFICAR SI YA EXISTE NÚMERO OFICIAL
            # ============================================================
            logger.info("PASO 2/6: Verificando si ya existe número oficial...")

            official_check_query = """
                SELECT official_number, department_id
                FROM official_documents
                WHERE id = %s
            """
            cursor.execute(official_check_query, (document_id,))
            existing_official = cursor.fetchone()

            if existing_official:
                # Ya existe entrada (caso de reintento), usar número existente
                logger.info(f"Número oficial ya existe: {existing_official['official_number']}")
                official_number = existing_official['official_number']
                department_id = existing_official['department_id']
            else:
                # ============================================================
                # PASO 3: GENERAR NÚMERO OFICIAL CON LOCK ULTRA CORTO
                # ============================================================
                logger.info("PASO 3/6: Generando número oficial...")

                # Obtener el tipo de documento para generar el número
                type_query = """
                    SELECT dt.acronym, dt.type as source_type
                    FROM document_draft dd
                    JOIN document_types dt ON dd.document_type_id = dt.id
                    WHERE dd.id = %s
                """
                cursor.execute(type_query, (document_id,))
                type_result = cursor.fetchone()

                # Generar número con año actual usando función centralizada
                from datetime import datetime
                current_year = datetime.now().year

                document_type_acronym = type_result['acronym'] if type_result else "DOC"
                source_type = type_result['source_type'] if type_result else "HTML"

                # ✅ USAR FUNCIÓN CENTRALIZADA (con lock ultra corto 10-20ms)
                official_number, department_id, global_sequence = await generate_official_number(
                    document_type_acronym=document_type_acronym,
                    user_id=user_id,
                    year=current_year,
                    connection=conn,  # Pasar conexión para transacción atómica
                    schema_name=schema_name  # Multi-tenant
                )

                logger.info(f"Número generado: {official_number}")

                # ============================================================
                # PASO 4: INSERTAR EN OFFICIAL_DOCUMENTS
                # ============================================================
                logger.info("PASO 4/6: Reservando número en official_documents...")

                # Obtener datos del documento (incluyendo resume para copiar a official)
                doc_data_query = """
                    SELECT dd.reference, dd.content::text as content_text, dd.document_type_id, dd.resume
                    FROM document_draft dd
                    WHERE dd.id = %s
                """
                cursor.execute(doc_data_query, (document_id,))
                doc_data = cursor.fetchone()

                import json
                content_json = doc_data['content_text'] if doc_data['content_text'] else '{}'

                # Obtener firmantes del documento
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

                # Obtener sector_ids de todos los firmantes (para filtro por sector)
                signer_sectors_query = """
                    SELECT ARRAY_AGG(DISTINCT u.sector_id) FILTER (WHERE u.sector_id IS NOT NULL) as sector_ids
                    FROM document_signers ds
                    JOIN users u ON ds.user_id = u.id
                    WHERE ds.document_id = %s
                """
                cursor.execute(signer_sectors_query, (document_id,))
                signer_sectors_result = cursor.fetchone()
                signer_sector_ids = signer_sectors_result['sector_ids'] if signer_sectors_result else None

                # Insertar en official_documents (incluyendo resume del draft)
                reserve_official_doc = """
                    INSERT INTO official_documents (
                        id, reference, content, official_number, year,
                        department_id, numerator_id, signed_at, document_type_id, global_sequence, signers, signer_sector_ids, resume
                    ) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s::jsonb, %s, %s)
                """
                cursor.execute(reserve_official_doc, (
                    document_id, doc_data['reference'], content_json,
                    official_number, current_year, department_id, user_id, doc_data['document_type_id'],
                    global_sequence,
                    json.dumps(signers_data) if signers_data else None,
                    signer_sector_ids,
                    doc_data['resume']  # Copiar resume del draft
                ))

                logger.info("Número reservado en BD")

                # Limpiar resume del draft (ya está copiado a official_documents)
                if doc_data['resume']:
                    cursor.execute("""
                        UPDATE document_draft
                        SET resume = NULL
                        WHERE id = %s
                    """, (document_id,))
                    logger.info("Resume copiado a official y limpiado del draft")

            # ============================================================
            # PASO 5: FIRMAR CON NOTARY Y PUBLICAR EN R2 OFICIAL
            # ============================================================
            logger.info("PASO 5/6: Firmando con Notary y publicando en R2...")

            # 5.1 - Obtener PDF desde R2 tosign usando document_id
            logger.info("5.1 - Obteniendo PDF desde R2 tosign...")
            logger.info(f"Usando document_id: {document_id}")

            # Descargar PDF desde R2 tosign
            try:
                from services.storage.cloudflare import get_tenant_r2_client
                import httpx

                r2_client = get_tenant_r2_client(schema_name=schema_name)
                filename = document_id.replace('-', '') + '.pdf'

                # Obtener URL firmada temporal
                pdf_url = await run_in_threadpool(r2_client.get_tosign_url, filename)
                if not pdf_url:
                    raise ValidationError("No se pudo obtener URL del PDF desde R2 tosign")

                logger.info(f"Descargando PDF: {filename}")

                async with httpx.AsyncClient(timeout=30.0) as client:
                    pdf_response = await client.get(pdf_url)
                    pdf_response.raise_for_status()
                    pdf_bytes = pdf_response.content

                logger.info(f"PDF descargado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

            except Exception as e:
                logger.error(f"ERROR descargando PDF de R2 tosign: {e}")
                raise ValidationError(f"Error descargando PDF de R2: {str(e)}")

            # 5.2 - Obtener datos del firmante numerador
            logger.info("5.2 - Obteniendo datos del numerador...")

            # Obtener datos del firmante usando función compartida
            try:
                signer_data = get_signer_data(user_id, schema_name=schema_name)
                signer_name = signer_data['full_name']
                signer_seal = signer_data['seal']
                signer_department = signer_data['department_name']
                signer_municipality = signer_data['municipality_name']
            except ValidationError as e:
                logger.error("Datos del numerador no encontrados")
                raise ValidationError("No se encontraron datos del numerador")

            logger.info(f"Firmante: {signer_name}")
            logger.info(f"Sello: {signer_seal}")
            logger.info(f"Departamento: {signer_department}")
            logger.info(f"Municipalidad: {signer_municipality}")

            # 5.3 - Firmar con Notary (incluye manejo automático de FULLPAGE)
            logger.info("5.3 - Firmando con Notary...")

            try:
                from services.shared.notary_api import call_notary_sign_pdf
                from services.shared.settings_utils import get_city_from_settings

                # Obtener city desde settings del tenant
                city = get_city_from_settings(cursor=cursor)
                logger.info(f"City desde settings: {city}")

                # Obtener source_type para determinar stamp_position
                # (Documentos importados usan stamp_position="last")
                source_type_query = """
                    SELECT dt.type as source_type
                    FROM document_draft dd
                    JOIN document_types dt ON dd.document_type_id = dt.id
                    WHERE dd.id = %s
                """
                cursor.execute(source_type_query, (document_id,))
                source_type_result = cursor.fetchone()
                source_type = source_type_result['source_type'] if source_type_result else 'HTML'

                # Documentos importados: firmas en página final
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
                    tenant_id=schema_name  # Para firma PAdES
                )

                logger.info(f"PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

            except Exception as e:
                logger.error(f"ERROR firmando con Notary: {e}")
                raise ValidationError(f"Error firmando con Notary: {str(e)}")

            # 5.4 - Subir PDF firmado a R2 oficial
            logger.info("5.4 - Subiendo PDF firmado a R2 oficial...")

            try:
                oficial_filename = f"{official_number}.pdf"
                upload_result = await run_in_threadpool(r2_client.upload_oficial, signed_pdf_bytes, oficial_filename)

                logger.info(f"PDF publicado en R2 oficial: {oficial_filename}")

            except Exception as e:
                logger.error(f"ERROR subiendo PDF a R2 oficial: {e}")
                raise ValidationError(f"Error subiendo PDF firmado a R2 oficial: {str(e)}")

            # 5.5 - Eliminar PDF del bucket tosign (SOFT-FAIL: no bloquea si falla)
            logger.info("5.5 - Eliminando PDF de R2 tosign...")

            try:
                delete_result = r2_client.delete_tosign(filename)
                logger.info("PDF eliminado de R2 tosign")
            except Exception as e:
                # SOFT-FAIL: Logear advertencia pero continuar
                logger.warning(f"No se pudo eliminar PDF de R2 tosign: {e}")
                logger.warning("Continuando con el proceso (soft-fail)")

            logger.info("Proceso de firma completo")

            # ============================================================
            # PASO 6: ACTUALIZAR TABLAS
            # ============================================================
            logger.info("PASO 6/6: Actualizando estados...")

            # Marcar numerador como firmado
            update_signer = """
                UPDATE document_signers
                SET status = 'signed', signed_at = CURRENT_TIMESTAMP
                WHERE document_id = %s AND user_id = %s
            """
            cursor.execute(update_signer, (document_id, user_id))

            # Actualizar documento con número oficial y estado final
            update_document = """
                UPDATE document_draft
                SET status = 'signed',
                    document_number = %s,
                    numbered_at = CURRENT_TIMESTAMP,
                    numbered_by = %s,
                    last_modified_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """
            cursor.execute(update_document, (official_number, user_id, document_id))

            # ============================================================
            # COMMIT FINAL
            # ============================================================
            conn.commit()
            logger.info("Transacción exitosa - Documento firmado y numerado")
            logger.info(f"Número oficial: {official_number}")

            return {
                "success": True,
                "message": "Documento firmado y numerado exitosamente por el numerador",
                "document_id": document_id,
                "numerator_id": user_id,
                "official_number": official_number,
                "document_status": "signed"
            }

        except Exception as e:
            # ============================================================
            # ROLLBACK AUTOMÁTICO
            # ============================================================
            conn.rollback()
            logger.error("Error - Rollback automático ejecutado")
            logger.error(f"Error: {str(e)}")
            # Re-lanzar la excepción para que el endpoint la maneje
            raise

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
