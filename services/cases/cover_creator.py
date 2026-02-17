"""Servicio para creación automática de carátulas de expedientes"""

from shared.logging import get_logger
from typing import Dict, Any
from datetime import datetime
import os
import httpx
import json
from fastapi.concurrency import run_in_threadpool

from database import get_db_connection
from shared.exceptions import (
    ValidationError, ExternalServiceError, DocumentNotFoundError
)
from services.shared.signer_data import get_signer_data
from shared.config import get_external_api_config
from services.documents.lifecycle.creation import create_document
from shared.numbering import generate_official_number
from config.constants import (
    DOCUMENT_TYPE_CAEX,
    CASE_COVER_REFERENCE_TEMPLATE,
    CASE_COVER_CREATED_SUCCESS,
    CASE_COVER_CREATION_ERROR,
    DEFAULT_LOGO_URL
)
from services.shared.settings_utils import get_city_from_settings
from services.case_queries import (
    get_document_type_id_query,
    get_document_reference_query,
    get_document_signers_query,
    update_document_content_query,
    insert_official_document_query,
    delete_official_document_query,
    update_document_status_signed_query,
    update_signer_status_signed_query,
    insert_case_official_document_query,
    delete_document_signers_query,
    delete_document_draft_query
)

logger = get_logger(__name__)


async def create_case_cover(
    case_id: str,
    case_number: str,
    case_reference: str,
    case_template_acronym: str,
    case_template_name: str,
    filing_department_id: str,
    user_id: str,
    *,
    schema_name: str,
    connection=None
) -> Dict[str, Any]:
    """
    Crea carátula automática para un expediente.

    Flujo:
    1. Crear documento CAEX en draft
    2. Guardar contenido y firmante (usuario como numerador)
    3. Generar número oficial
    4. INSERT en official_documents (reservar número)
    5. Obtener datos completos para Legal Orchestrator
    6. Llamar a Legal Orchestrator /create-case-cover
    7. Si exitoso: Actualizar document_draft y document_signers a 'signed'
    8. Si falla: DELETE de official_documents y raise error

    Args:
        case_id: UUID del expediente
        case_number: Número del expediente (ej: EXP-2025-000045)
        case_reference: Motivo/referencia del expediente
        case_template_acronym: Acrónimo del tipo de expediente
        case_template_name: Nombre del tipo de expediente
        filing_department_id: ID del departamento de radicación
        user_id: ID del usuario que crea el expediente (será el numerador)

    Returns:
        Dict con document_id y official_number de la carátula

    Raises:
        ValidationError: Si faltan datos o validaciones fallan
        ExternalServiceError: Si falla Legal Orchestrator
    """
    logger.info(f"Iniciando creación de carátula para expediente {case_number}")
    logger.info(f"Case ID: {case_id[:8]}")
    logger.info(f"User ID: {user_id[:8]}")

    logger.info("Creando documento CAEX...")
    caex_document = create_document(
        document_type_acronym=DOCUMENT_TYPE_CAEX,
        reference=CASE_COVER_REFERENCE_TEMPLATE.format(case_number=case_number),
        creator_id=user_id,
        schema_name=schema_name
    )

    document_id = caex_document['document_id']
    document_type_name = caex_document['document_type_name']
    logger.info(f"Documento CAEX creado: {document_id[:8]}...")

    try:
        # PASO 2: Guardar contenido HTML y firmante (INSERT directo, sin save_document_changes)
        logger.info("Construyendo HTML de carátula...")

        # Construir HTML descriptivo con todos los datos
        caratula_html = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h1 style="text-align: center; color: #333;">CARÁTULA DE EXPEDIENTE</h1>
            <hr style="border: 2px solid #333; margin: 20px 0;">

            <div style="margin: 20px 0;">
                <p><strong>Número de Expediente:</strong> {case_number}</p>
                <p><strong>Tipo de Expediente:</strong> {case_template_name} ({case_template_acronym})</p>
                <p><strong>Motivo/Referencia:</strong> {case_reference}</p>
            </div>

            <div style="margin: 20px 0;">
                <p><strong>Creado automáticamente el:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
            </div>
        </div>
        """

        logger.info(f"Guardando contenido y firmante (INSERT directo)...")

        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Actualizar document_draft con el content en formato correcto
                content_structure = {
                    "html": caratula_html,
                    "format_version": "2.0",
                    "updated_at": datetime.now().isoformat()
                }

                cursor.execute(update_document_content_query(), (
                    json.dumps(content_structure),
                    document_id
                ))

                # NOTA: NO insertar firmante aquí - create_document() ya lo asignó automáticamente
                # como firmante numerador (ver services/documents/creation.py líneas 76-84)

                conn.commit()

        logger.info(f"Contenido HTML guardado exitosamente (firmante ya asignado por create_document)")

        # PASO 3: Generar número oficial con función centralizada
        logger.info("Generando número oficial con función centralizada...")
        current_year = datetime.now().year

        # ✅ USAR FUNCIÓN CENTRALIZADA (con lock ultra corto 10-20ms)
        official_number, department_id, global_sequence = await generate_official_number(
            document_type_acronym="CAEX",
            user_id=user_id,
            year=current_year,
            connection=connection,  # Usar conexión de la transacción
            schema_name=schema_name  # Multi-tenant
        )
        logger.info(f"Número oficial generado: {official_number}")
        logger.info(f"Global sequence capturada: {global_sequence}")

        # PASO 4: Construir payload para Legal Orchestrator
        # MOVIDO AQUÍ para guardar en official_documents.content
        logger.info("Construyendo payload para Legal Orchestrator...")
        cover_data = _fetch_cover_data(
            case_id=case_id,
            case_number=case_number,
            case_reference=case_reference,
            case_template_acronym=case_template_acronym,
            case_template_name=case_template_name,
            document_id=document_id,
            document_type_name=document_type_name,
            official_number=official_number,
            user_id=user_id,
            filing_department_id=filing_department_id,
            schema_name=schema_name
        )
        logger.info(f"Payload construido exitosamente")
        logger.info(f"Claves del payload: {list(cover_data.keys())}")

        # PASO 5: INSERT en official_documents (RESERVAR NÚMERO)
        # Guardar el MISMO payload que enviamos a Legal Orchestrator
        logger.info("Reservando número en official_documents...")
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(get_document_type_id_query(), (DOCUMENT_TYPE_CAEX,))
                type_result = cursor.fetchone()

                if not type_result:
                    raise ValidationError(f"Tipo de documento {DOCUMENT_TYPE_CAEX} no encontrado")

                document_type_id = type_result['document_type_id']

                # Obtener reference del documento
                cursor.execute(get_document_reference_query(), (document_id,))
                doc_data = cursor.fetchone()

                if not doc_data:
                    raise DocumentNotFoundError(document_id)

                # Obtener firmantes del documento
                cursor.execute(get_document_signers_query(), (document_id,))
                signers_result = cursor.fetchone()
                signers_data = signers_result['signers'] if signers_result else []

                # Obtener sector_ids de los firmantes (para filtro por sector)
                signer_sectors_query = """
                    SELECT ARRAY_AGG(DISTINCT u.sector_id) FILTER (WHERE u.sector_id IS NOT NULL) as sector_ids
                    FROM document_signers ds
                    JOIN users u ON ds.user_id = u.id
                    WHERE ds.document_id = %s
                """
                cursor.execute(signer_sectors_query, (document_id,))
                signer_sectors_result = cursor.fetchone()
                signer_sector_ids = signer_sectors_result['sector_ids'] if signer_sectors_result else None

                # INSERT en official_documents
                cursor.execute(insert_official_document_query(), (
                    document_id,
                    doc_data['reference'],
                    json.dumps(cover_data),
                    official_number,
                    current_year,
                    department_id,
                    user_id,
                    document_type_id,
                    global_sequence,
                    json.dumps(signers_data) if signers_data else None,
                    signer_sector_ids
                ))
                conn.commit()

        logger.info(f"Número oficial reservado en BD")
        logger.info(f"Content guardado: Payload de Legal Orchestrator")

        # PASO 6: Generar, firmar y publicar carátula directamente
        # Phase 5: Eliminación de Legal Orchestrator
        logger.info("Generando y firmando caratula directamente...")
        try:
            # 6.1: Generar PDF con PDFComposer
            logger.info(f"6.1: Generando PDF con PDFComposer /create-case/...")
            from services.shared.pdfcomposer_api import call_pdfcomposer_create_case

            pdf_bytes = await call_pdfcomposer_create_case(cover_data)
            logger.info(f"[OK] PDF generado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

            # 6.2: Firmar PDF con Notary (CON official_number y city)
            logger.info(f"6.2: Firmando PDF con Notary...")
            from services.shared.notary_api import call_notary_sign_pdf

            signed_pdf_bytes = await call_notary_sign_pdf(
                pdf_bytes=pdf_bytes,
                signer_name=cover_data["signer_full_name"],
                signer_seal=cover_data["signer_seal"],
                signer_department=cover_data["signer_department"],
                signer_municipality=cover_data["signer_municipality"],
                official_number=cover_data["official_document_number"],
                city=cover_data["city_name"],
                tenant_id=schema_name  # Para firma PAdES
            )
            logger.info(f"[OK] PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

            # 6.3: Subir a R2 oficial
            logger.info(f"6.3: Subiendo a R2 oficial...")
            from services.storage.cloudflare import get_tenant_r2_client
            r2_client = get_tenant_r2_client(schema_name=schema_name)

            # Filename: official_number.pdf
            filename_oficial = f"{official_number}.pdf"

            await run_in_threadpool(r2_client.upload_oficial, signed_pdf_bytes, filename_oficial)
            logger.info(f"[OK] Subido a R2 oficial: {filename_oficial}")

            logger.info(f"[OK] Caratula generada, firmada y publicada exitosamente")

        except Exception as e:
            # Si falla generación/firma/publicación, hacer rollback del official_documents
            logger.info(f"[ERR] Fallo en generacion/firma/publicacion - {str(e)}")
            logger.info(f"Ejecutando ROLLBACK: Eliminando de official_documents...")

            with get_db_connection(schema_name) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(delete_official_document_query(), (document_id,))
                    conn.commit()

            logger.info(f"Rollback completado")
            raise ExternalServiceError(CASE_COVER_CREATION_ERROR.format(error=str(e)))

        # PASO 7: Actualizar document_draft y document_signers a 'signed'
        logger.info("Actualizando estados a 'signed'...")
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(update_document_status_signed_query(), (document_id,))
                cursor.execute(update_signer_status_signed_query(), (document_id, user_id))
                conn.commit()

        logger.info(f"Estados actualizados a 'signed'")

        # PASO 8: Vincular carátula al expediente
        logger.info("Vinculando carátula al expediente...")

        # Usar la conexión de la transacción si está disponible, sino crear una nueva
        if connection:
            cursor = connection.cursor()
            cursor.execute(insert_case_official_document_query(), (case_id, document_id, user_id))
        else:
            with get_db_connection(schema_name) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(insert_case_official_document_query(), (case_id, document_id, user_id))
                    conn.commit()

        logger.info(f"Carátula vinculada al expediente")
        logger.info(f"Link: case_id={case_id[:8]} -> document_id={document_id[:8]}")
        logger.info(f"Proceso completado exitosamente")

        return {
            "success": True,
            "document_id": document_id,
            "official_number": official_number,
            "message": CASE_COVER_CREATED_SUCCESS.format(official_number=official_number)
        }

    except Exception as e:
        # Si hay cualquier error después de crear el documento, eliminarlo
        logger.info(f"ERROR GENERAL: {str(e)}")
        logger.info(f"Limpiando documento creado...")

        try:
            with get_db_connection(schema_name) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(delete_document_signers_query(), (document_id,))
                    cursor.execute(delete_document_draft_query(), (document_id,))
                    conn.commit()
            logger.info(f"Documento limpiado")
        except Exception as cleanup_error:
            logger.info(f"ERROR en limpieza: {cleanup_error}")

        # Re-lanzar la excepción original
        raise


# ============================================================================
# FUNCIONES AUXILIARES
# ============================================================================

def _fetch_cover_data(
    case_id: str,
    case_number: str,
    case_reference: str,
    case_template_acronym: str,
    case_template_name: str,
    document_id: str,
    document_type_name: str,
    official_number: str,
    user_id: str,
    filing_department_id: str,
    schema_name: str
) -> Dict[str, Any]:
    """
    Recupera todos los datos necesarios para Legal Orchestrator /create-case-cover.

    Hace un query grande con JOINs para obtener toda la información de una vez.

    Returns:
        Dict con todos los campos requeridos por Legal Orchestrator
    """
    with get_db_connection(schema_name) as conn:
        # Obtener datos del firmante usando función compartida
        signer_data = get_signer_data(user_id, schema_name=schema_name)

        # Obtener logo del municipio desde settings
        with conn.cursor() as cursor:
            cursor.execute("SELECT logo_url FROM settings LIMIT 1")
            settings_result = cursor.fetchone()
            logo_url = settings_result['logo_url'] if settings_result and settings_result.get('logo_url') else DEFAULT_LOGO_URL

        # Obtener city desde settings del tenant
        with conn.cursor() as city_cursor:
            city = get_city_from_settings(cursor=city_cursor)

        # Construir payload para Legal Orchestrator usando datos del usuario
        cover_data = {
            "municipality_logo_url": logo_url,
            "document_type_acronym": "CAEX",
            "document_type_name": document_type_name,
            "document_reference": f"Creación {case_number}",
            "case_number": case_number,
            "case_type_acronym": case_template_acronym,
            "case_type_name": case_template_name,
            "case_motive": case_reference,
            "initiating_department": signer_data['department_name'],
            "case_creator": signer_data['full_name'],
            "signer_full_name": signer_data['full_name'],
            "signer_seal": signer_data['seal'],
            "signer_department": signer_data['department_name'],
            "signer_municipality": signer_data['municipality_name'],
            "official_document_number": official_number,
            "city_name": city
        }

        logger.info(f"Payload para Legal Orchestrator:")
        logger.info(f"  - Case number: {cover_data['case_number']}")
        logger.info(f"  - Official number: {cover_data['official_document_number']}")
        logger.info(f"  - Signer: {cover_data['signer_full_name']}")
        logger.info(f"  - Department: {cover_data['initiating_department']}")

        return cover_data

