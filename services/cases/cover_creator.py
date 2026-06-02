"""Servicio para creación automática de carátulas de expedientes"""

from shared.logging import get_logger
from typing import Dict, Any
from datetime import datetime
from fastapi.concurrency import run_in_threadpool

from database import get_conn
from shared.exceptions import (
    ValidationError, ExternalServiceError, DocumentNotFoundError
)
from shared.numbering import generate_official_number
from services.shared.signer_data import get_signer_data
from services.documents.lifecycle.creation import create_document
from config.constants import (
    DOCUMENT_TYPE_CAEX,
    CASE_COVER_REFERENCE_TEMPLATE,
    CASE_COVER_CREATED_SUCCESS,
    CASE_COVER_CREATION_ERROR,
    DEFAULT_LOGO_URL
)
from services.shared.settings_utils import get_city_from_settings

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
    schema_name: str
) -> Dict[str, Any]:
    """
    Crea carátula automática para un expediente.

    Flujo:
    MOMENTO 1 (lock corto ~5ms):
      1. Crear documento CAEX en draft
      2. Guardar contenido HTML
      3. Construir cover_data y llamar generate_official_number()
         → INSERT en official_documents con signed_at=NULL
         (la función abre su propia conexión, lock ultra corto)
    MOMENTO 2 (sin lock):
      4. PDFComposer + Notary + R2
      5. Si OK: UPDATE official_documents SET signed_at, signers
      6. Si falla: re-raise (la fila queda con signed_at=NULL como hueco aceptable)
      7. Actualizar document_draft y document_signers a 'signed'
      8. Vincular carátula al expediente

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
        ExternalServiceError: Si falla PDFComposer/Notary/R2
    """
    logger.info(f"Iniciando creación de carátula para expediente {case_number}")
    logger.info(f"Case ID: {case_id[:8]}")
    logger.info(f"User ID: {user_id[:8]}")

    # ================================================================
    # PASO 1: Crear documento CAEX en draft
    # ================================================================
    logger.info("PASO 1: Creando documento CAEX...")
    caex_document = await create_document(
        document_type_acronym=DOCUMENT_TYPE_CAEX,
        reference=CASE_COVER_REFERENCE_TEMPLATE.format(case_number=case_number),
        creator_id=user_id,
        schema_name=schema_name
    )

    document_id = caex_document['document_id']
    document_type_name = caex_document['document_type_name']
    logger.info(f"Documento CAEX creado: {document_id[:8]}...")

    try:
        # ================================================================
        # PASO 2: Guardar contenido HTML en document_draft
        # ================================================================
        logger.info("PASO 2: Construyendo HTML de carátula...")

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

        async with get_conn(schema_name=schema_name) as conn:
            content_structure = {
                "html": caratula_html,
                "format_version": "2.0",
                "updated_at": datetime.now().isoformat()
            }
            await conn.execute(
                """
                UPDATE document_draft
                SET content = $1::jsonb,
                    last_modified_at = CURRENT_TIMESTAMP
                WHERE id = $2
                """,
                content_structure,
                document_id
            )

        logger.info("Contenido HTML guardado exitosamente")

        # ================================================================
        # PASO 3: Recolectar datos para generate_official_number
        # ================================================================
        logger.info("PASO 3: Recolectando datos para generate_official_number...")
        current_year = datetime.now().year

        async with get_conn(schema_name=schema_name) as conn:
            # Obtener document_type_id
            type_result = await conn.fetchrow(
                "SELECT id as document_type_id FROM document_types WHERE acronym = $1",
                DOCUMENT_TYPE_CAEX
            )
            if not type_result:
                raise ValidationError(f"Tipo de documento {DOCUMENT_TYPE_CAEX} no encontrado")
            document_type_id = type_result['document_type_id']

            # Obtener reference del documento
            doc_ref_row = await conn.fetchrow(
                "SELECT reference FROM document_draft WHERE id = $1",
                document_id
            )
            if not doc_ref_row:
                raise DocumentNotFoundError(document_id)
            reference_text = doc_ref_row['reference']

            # Obtener firmantes
            signers_result = await conn.fetchrow(
                """
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
                WHERE ds.document_id = $1
                """,
                document_id
            )
            signers_data = signers_result['signers'] if signers_result else []

            # Obtener sector_ids de los firmantes
            signer_sectors_result = await conn.fetchrow(
                """
                SELECT ARRAY_AGG(DISTINCT u.sector_id) FILTER (WHERE u.sector_id IS NOT NULL) as sector_ids
                FROM document_signers ds
                JOIN users u ON ds.user_id = u.id
                WHERE ds.document_id = $1
                """,
                document_id
            )
            signer_sector_ids = signer_sectors_result['sector_ids'] if signer_sectors_result else None

            # Obtener logo y ciudad
            settings_result = await conn.fetchrow("SELECT logo_url, city FROM settings LIMIT 1")
            logo_url = (
                settings_result['logo_url']
                if settings_result and settings_result.get('logo_url')
                else DEFAULT_LOGO_URL
            )
            city = await get_city_from_settings(conn=conn, schema_name=schema_name)

        # Obtener datos del firmante
        signer_data_for_payload = await get_signer_data(user_id, schema_name=schema_name)

        # PASO 3: Reservar numero oficial
        logger.info("PASO 3: Generando numero oficial (lock ultra corto ~5ms)...")

        cover_data = {
            "municipality_logo_url": logo_url,
            "document_type_acronym": "CAEX",
            "document_type_name": document_type_name,
            "document_reference": f"Creación {case_number}",
            "case_number": case_number,
            "case_type_acronym": case_template_acronym,
            "case_type_name": case_template_name,
            "case_motive": case_reference,
            "initiating_department": signer_data_for_payload['department_name'],
            "case_creator": signer_data_for_payload['full_name'],
            "signer_full_name": signer_data_for_payload['full_name'],
            "signer_seal": signer_data_for_payload['seal'],
            "signer_department": signer_data_for_payload['department_name'],
            "signer_municipality": signer_data_for_payload['municipality_name'],
            "city_name": city
        }

        official_number, department_id, global_sequence = await generate_official_number(
            document_type_acronym="CAEX",
            user_id=user_id,
            year=current_year,
            schema_name=schema_name,
            document_id=document_id,
            reference=reference_text,
            document_type_id=document_type_id,
            content=cover_data,
            signers=signers_data,
            signer_sector_ids=signer_sector_ids,
        )
        logger.info(f"Número oficial generado: {official_number}")
        logger.info(f"Global sequence: {global_sequence}")

        # ================================================================
        # PASO 5: PDFComposer + Notary + R2
        # ================================================================
        logger.info("PASO 5: Generando, firmando y publicando carátula...")
        try:
            # 5.1: Generar PDF con PDFComposer
            logger.info("5.1: Generando PDF con PDFComposer /create-case/...")
            from services.shared.pdfcomposer_api import call_pdfcomposer_create_case

            pdf_bytes = await call_pdfcomposer_create_case(cover_data, schema_name=schema_name)
            logger.info(f"[OK] PDF generado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

            # 5.2: Firmar PDF con Notary
            logger.info("5.2: Firmando PDF con Notary...")
            from services.shared.notary_api import call_notary_sign_pdf

            signed_pdf_bytes = await call_notary_sign_pdf(
                pdf_bytes=pdf_bytes,
                signer_name=cover_data["signer_full_name"],
                signer_seal=cover_data["signer_seal"],
                signer_department=cover_data["signer_department"],
                signer_municipality=cover_data["signer_municipality"],
                official_number=official_number,
                city=cover_data["city_name"],
                tenant_id=schema_name,
                schema_name=schema_name
            )
            logger.info(f"[OK] PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

            # 5.3: Subir a R2 oficial
            logger.info("5.3: Subiendo a R2 oficial...")
            from services.storage.cloudflare import get_tenant_r2_client
            r2_client = await get_tenant_r2_client(schema_name=schema_name)

            filename_oficial = f"{official_number}.pdf"
            await run_in_threadpool(r2_client.upload_oficial, signed_pdf_bytes, filename_oficial)
            logger.info(f"[OK] Subido a R2 oficial: {filename_oficial}")

        except Exception as e:
            # PDFComposer/Notary/R2 falló.
            # NO hacemos DELETE de official_documents.
            # La fila queda con signed_at=NULL (hueco aceptable por diseño).
            logger.error(f"[ERR] Fallo en generacion/firma/publicacion: {str(e)}")
            logger.error(
                f"official_documents queda con signed_at=NULL para doc={document_id}. "
                f"Número {official_number} reservado como hueco aceptable."
            )
            raise ExternalServiceError(CASE_COVER_CREATION_ERROR.format(error=str(e)))

        # ================================================================
        # PASO 6: UPDATE official_documents (signed_at + signers)
        # ================================================================
        logger.info("PASO 6: Confirmando firma en official_documents (UPDATE)...")
        async with get_conn(schema_name=schema_name) as conn:
            # Marcar como firmado
            await conn.execute(
                """
                UPDATE official_documents
                SET signed_at = CURRENT_TIMESTAMP
                WHERE id = $1 AND signed_at IS NULL
                """,
                document_id
            )

            # Actualizar el firmante en el array signers con jsonb_set
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
                user_id, document_id
            )
        logger.info("official_documents actualizado con signed_at y signers")

        # ================================================================
        # PASO 7: Actualizar document_draft y document_signers a 'signed'
        # ================================================================
        logger.info("PASO 7: Actualizando estados a 'signed'...")
        async with get_conn(schema_name=schema_name) as conn:
            await conn.execute(
                "UPDATE document_draft SET status = 'signed' WHERE id = $1",
                document_id
            )
            await conn.execute(
                """
                UPDATE document_signers
                SET status = 'signed', signed_at = CURRENT_TIMESTAMP
                WHERE document_id = $1 AND user_id = $2
                """,
                document_id, user_id
            )
        logger.info("Estados actualizados a 'signed'")

        # ================================================================
        # PASO 8: Vincular carátula al expediente
        # ================================================================
        logger.info("PASO 8: Vinculando carátula al expediente...")
        async with get_conn(schema_name=schema_name) as conn:
            await conn.execute(
                """
                INSERT INTO case_official_documents (
                    case_id, official_document_id, linking_user_id, order_number,
                    linking_date, is_active
                ) VALUES ($1, $2, $3, 1, CURRENT_TIMESTAMP, true)
                """,
                case_id, document_id, user_id
            )
        logger.info(f"Carátula vinculada al expediente")
        logger.info(f"Link: case_id={case_id[:8]} -> document_id={document_id[:8]}")
        logger.info("Proceso completado exitosamente")

        return {
            "success": True,
            "document_id": document_id,
            "official_number": official_number,
            "message": CASE_COVER_CREATED_SUCCESS.format(official_number=official_number)
        }

    except Exception as e:
        # Error general: NO limpiar document_draft ni document_signers.
        #
        # Si el error ocurrió ANTES de generate_official_number:
        #   - document_draft queda en estado 'draft' (sin número oficial)
        #   - document_signers queda intacto
        #   - No hay fila en official_documents → estado consistente
        #
        # Si el error ocurrió DESPUÉS de generate_official_number (PDFComposer/Notary/R2):
        #   - official_documents queda con signed_at=NULL (hueco aceptable por diseño)
        #   - document_draft queda en estado 'draft' → consistente con el hueco
        #   - document_signers queda intacto → consistente
        #
        # El caller (creation.py) maneja el error: expediente queda inactive,
        # log + mail de alerta. El usuario reintenta manualmente.
        logger.error(
            f"ERROR GENERAL en create_case_cover: {str(e)} | "
            f"doc={document_id} queda como draft con signed_at=NULL en official_documents "
            f"(si generate_official_number ya ejecutó). Hueco aceptable por diseño."
        )
        raise
