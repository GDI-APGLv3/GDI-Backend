"""
Helper genérico para creación de documentos oficiales de expedientes.

Este módulo centraliza la lógica compartida para crear documentos de case:
- Carátulas (CAEX)
- Pases de transferencia/asignación (PV)
- Futuros tipos de documentos oficiales

ARQUITECTURA:
- Función base genérica que maneja el flujo completo
- Funciones específicas la usan pasando configuración
- Evita duplicación de código
- Facilita mantenimiento y testing

FLUJO GENERAL (7 pasos):
1. Crear documento draft
2. Guardar contenido HTML y firmante
3. Generar número oficial (shared/numbering.py con advisory lock)
4. Construir payload para Legal Orchestrator
5. INSERT en official_documents (reservar número)
6. Llamar Legal Orchestrator
7. Actualizar a 'signed'

IMPORTANTE: Este helper NO vincula el documento al case.
El llamador debe usar CaseService.link_official_document() después
para que el order_number se calcule correctamente con SELECT FOR UPDATE.
"""

from typing import Dict, Any, Callable
from datetime import datetime
import os
import httpx
import json
from fastapi.concurrency import run_in_threadpool

from database import get_db_connection
from shared.exceptions import (
    ValidationError, ExternalServiceError, DocumentNotFoundError
)
from shared.logging import get_logger
from shared.config import get_external_api_config
from services.documents.lifecycle.creation import create_document
from shared.numbering import generate_official_number

logger = get_logger(__name__)


async def create_and_sign_case_document(
    document_type_acronym: str,
    reference: str,
    html_builder: Callable[[], str],
    payload_builder: Callable[[str, str, str, str], Dict[str, Any]],
    orchestrator_endpoint: str,
    case_id: str,
    user_id: str,
    *,
    schema_name: str,
    connection=None
) -> Dict[str, Any]:
    """
    Función base genérica para crear documentos oficiales de expedientes.

    Esta función ejecuta el flujo completo de creación de un documento:
    - Crear draft
    - Numerar con advisory lock
    - Firmar vía Legal Orchestrator
    - Vincular al case

    Args:
        document_type_acronym: Acrónimo del tipo (ej: "CAEX", "PV")
        reference: Referencia del documento
        html_builder: Función que retorna el HTML del documento
        payload_builder: Función que construye payload para Legal Orchestrator
            Recibe: (document_id, document_type_name, official_number, user_id)
            Retorna: Dict con payload completo
        orchestrator_endpoint: Endpoint de Legal Orchestrator (ej: "/create-case-cover")
        case_id: UUID del expediente
        user_id: UUID del usuario creador (será numerador)
        connection: Conexión de transacción externa (opcional)

    Returns:
        Dict con:
            - success: bool
            - document_id: str
            - official_number: str
            - message: str

    Raises:
        ValidationError: Si faltan datos o validaciones fallan
        ExternalServiceError: Si falla Legal Orchestrator
    """
    logger.info(f"Iniciando creación de documento {document_type_acronym}")
    logger.info(f"Case ID: {case_id[:8]}...")
    logger.info(f"User ID: {user_id[:8]}...")

    # ================================================================
    # PASO 1: Crear documento en draft
    # ================================================================
    logger.info(f"PASO 1: Creando documento {document_type_acronym}...")
    document = create_document(
        document_type_acronym=document_type_acronym,
        reference=reference,
        creator_id=user_id,
        schema_name=schema_name
    )

    document_id = document['document_id']
    document_type_name = document['document_type_name']
    logger.info(f"Documento creado: {document_id[:8]}...")

    try:
        # ================================================================
        # PASO 2: Guardar contenido HTML y firmante
        # ================================================================
        logger.info("PASO 2: Construyendo HTML...")

        # Llamar al builder para obtener HTML específico del tipo de documento
        html_content = html_builder()

        logger.info("Guardando contenido y firmante...")

        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Actualizar document_draft con content
                content_structure = {
                    "html": html_content,
                    "format_version": "2.0",
                    "updated_at": datetime.now().isoformat()
                }

                update_content_query = """
                    UPDATE document_draft
                    SET content = %s::jsonb,
                        last_modified_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """
                cursor.execute(update_content_query, (
                    json.dumps(content_structure),
                    document_id
                ))

                # NOTA: NO insertar firmante aquí - create_document() ya lo asignó automáticamente
                # como firmante numerador (ver services/documents/creation.py líneas 76-84)

                conn.commit()

        logger.info("Contenido guardado")

        # ================================================================
        # PASO 3: Generar número oficial con función centralizada
        # ================================================================
        logger.info("PASO 3: Generando número oficial...")
        current_year = datetime.now().year

        official_number, department_id, global_sequence = await generate_official_number(
            document_type_acronym=document_type_acronym,
            user_id=user_id,
            year=current_year,
            connection=connection,  # Usar conexión de transacción si existe
            schema_name=schema_name  # Multi-tenant
        )

        logger.info(f"Número oficial: {official_number}")
        logger.info(f"Global sequence: {global_sequence}")

        # ================================================================
        # PASO 4: Construir payload para Legal Orchestrator
        # ================================================================
        logger.info("PASO 4: Construyendo payload...")

        # Llamar al builder específico del tipo de documento
        payload = payload_builder(document_id, document_type_name, official_number, user_id)

        logger.info(f"Payload construido con {len(payload)} campos")

        # ================================================================
        # PASO 5: INSERT en official_documents (RESERVAR NÚMERO)
        # ================================================================
        logger.info("PASO 5: Reservando número en official_documents...")

        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Obtener document_type_id
                cursor.execute("""
                    SELECT id as document_type_id
                    FROM document_types
                    WHERE acronym = %s
                """, (document_type_acronym,))
                type_result = cursor.fetchone()

                if not type_result:
                    raise ValidationError(f"Tipo de documento {document_type_acronym} no encontrado")

                document_type_id = type_result['document_type_id']

                # Obtener reference del documento
                cursor.execute("""
                    SELECT reference
                    FROM document_draft
                    WHERE id = %s
                """, (document_id,))
                doc_data = cursor.fetchone()

                if not doc_data:
                    raise DocumentNotFoundError(document_id)

                # Obtener firmantes
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
                insert_official = """
                    INSERT INTO official_documents (
                        id, reference, content, official_number, year,
                        department_id, numerator_id, signed_at, document_type_id,
                        global_sequence, signers, signer_sector_ids
                    ) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s::jsonb, %s)
                """
                cursor.execute(insert_official, (
                    document_id,
                    doc_data['reference'],
                    json.dumps(payload),  # Guardar payload completo
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

        logger.info("Número reservado en BD")

        # ================================================================
        # PASO 6: Llamar a Legal Orchestrator
        # ================================================================
        logger.info(f"PASO 6: Llamando Legal Orchestrator {orchestrator_endpoint}...")

        try:
            api_result = await _call_legal_orchestrator(orchestrator_endpoint, payload, schema_name=schema_name)
            logger.info("Legal Orchestrator exitoso")

        except Exception as e:
            # Si falla Legal Orchestrator, hacer rollback de official_documents
            logger.error(f"Legal Orchestrator falló - {str(e)}")
            logger.error("Ejecutando ROLLBACK...")

            with get_db_connection(schema_name) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        DELETE FROM official_documents
                        WHERE id = %s
                    """, (document_id,))
                    conn.commit()

            logger.error("Rollback completado")
            raise ExternalServiceError(f"Error en Legal Orchestrator: {str(e)}")

        # ================================================================
        # PASO 7: Actualizar a 'signed'
        # ================================================================
        logger.info("PASO 7: Actualizando estados a 'signed'...")

        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Actualizar document_draft
                cursor.execute("""
                    UPDATE document_draft
                    SET status = 'signed'
                    WHERE id = %s
                """, (document_id,))

                # Actualizar document_signers
                cursor.execute("""
                    UPDATE document_signers
                    SET status = 'signed', signed_at = CURRENT_TIMESTAMP
                    WHERE document_id = %s AND user_id = %s
                """, (document_id, user_id))

                conn.commit()

        logger.info("Estados actualizados")
        logger.info("Proceso completado exitosamente")
        logger.info("NOTA: El documento NO está vinculado al case aún")
        logger.info("El llamador debe usar CaseService.link_official_document()")

        return {
            "success": True,
            "document_id": document_id,
            "official_number": official_number,
            "message": f"Documento {document_type_acronym} creado exitosamente: {official_number}"
        }

    except Exception as e:
        # Si hay error después de crear el documento, limpiarlo
        logger.error(f"ERROR GENERAL: {str(e)}")
        logger.error("Limpiando documento...")

        try:
            with get_db_connection(schema_name) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM document_signers WHERE document_id = %s", (document_id,))
                    cursor.execute("DELETE FROM document_draft WHERE id = %s", (document_id,))
                    conn.commit()
            logger.error("Documento limpiado")
        except Exception as cleanup_error:
            logger.error(f"ERROR en limpieza: {cleanup_error}")

        raise


async def _call_legal_orchestrator(endpoint: str, payload: Dict[str, Any], *, schema_name: str) -> Dict[str, Any]:
    """
    Phase 6: Enrutador de flujos directos (sin Legal Orchestrator).

    Detecta el endpoint y ejecuta flujo directo correspondiente:
    - /create-case-cover → PDFComposer /create-case/ + Notary + R2 oficial
    - /create-transfer-document → PDFComposer /move/ + Notary + R2 oficial

    Args:
        endpoint: Ruta del endpoint (ej: "/create-case-cover")
        payload: Dict con datos a enviar
        schema_name: Schema del tenant para R2

    Returns:
        Dict con resultado de operación directa

    Raises:
        ExternalServiceError: Si falla la llamada
        NotImplementedError: Si el endpoint no está soportado
    """
    logger.info(f"Detectando endpoint: {endpoint}")

    if endpoint == "/create-case-cover":
        # Phase 5: CAEX - Flujo directo
        logger.info("Ejecutando flujo directo para CAEX...")
        return await _direct_pdf_flow_case_cover(payload, schema_name=schema_name)

    elif endpoint == "/create-transfer-document":
        # Phase 6: PV - Flujo directo
        logger.info("Ejecutando flujo directo para PV...")
        return await _direct_pdf_flow_transfer(payload, schema_name=schema_name)

    else:
        # Endpoint no soportado
        raise NotImplementedError(
            f"Endpoint {endpoint} no soportado. "
            f"Legal Orchestrator ha sido eliminado en Phase 6. "
            f"Endpoints soportados: /create-case-cover, /create-transfer-document"
        )


async def _direct_pdf_flow_case_cover(payload: Dict[str, Any], *, schema_name: str) -> Dict[str, Any]:
    """
    Phase 5: Flujo directo para generar carátulas CAEX.

    Ejecuta:
    1. PDFComposer /create-case/ → Generar PDF
    2. Notary /sign-pdf → Firmar PDF (CON official_number + city)
    3. Upload a R2 oficial

    Args:
        payload: Dict con datos del documento (mismo formato que Legal Orchestrator)
        schema_name: Schema del tenant para R2

    Returns:
        Dict con status=success (compatible con respuesta anterior)

    Raises:
        ExternalServiceError: Si falla algún paso
    """
    logger.info("Iniciando flujo directo para carátula...")

    try:
        # Paso 1: Generar PDF con PDFComposer /create-case/
        logger.info("1/3: Generando PDF con PDFComposer /create-case/...")

        from services.shared.pdfcomposer_api import call_pdfcomposer_create_case

        pdf_bytes = await call_pdfcomposer_create_case(payload)
        logger.info(f"PDF generado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

        # Paso 2: Firmar PDF con Notary (CON official_number y city)
        logger.info("2/3: Firmando PDF con Notary...")

        from services.shared.notary_api import call_notary_sign_pdf

        signed_pdf_bytes = await call_notary_sign_pdf(
            pdf_bytes=pdf_bytes,
            signer_name=payload["signer_full_name"],
            signer_seal=payload["signer_seal"],
            signer_department=payload["signer_department"],
            signer_municipality=payload["signer_municipality"],
            official_number=payload["official_document_number"],
            city=payload["city_name"],
            tenant_id=schema_name  # Para firma PAdES
        )
        logger.info(f"PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

        # Paso 3: Subir a R2 oficial
        logger.info("3/3: Subiendo a R2 oficial...")

        from services.storage.cloudflare import get_tenant_r2_client
        r2_client = get_tenant_r2_client(schema_name=schema_name)

        filename_oficial = f"{payload['official_document_number']}.pdf"
        await run_in_threadpool(r2_client.upload_oficial, signed_pdf_bytes, filename_oficial)
        logger.info(f"Subido a R2 oficial: {filename_oficial}")

        logger.info("Flujo completado exitosamente")

        # Retornar respuesta compatible con formato anterior
        return {
            "status": "success",
            "message": f"Carátula creada exitosamente: {payload['official_document_number']}",
            "data": {
                "official_document_number": payload['official_document_number'],
                "signed_url": f"https://cloudflare.r2/oficial/{filename_oficial}"
            }
        }

    except Exception as e:
        logger.error(f"Error en flujo directo: {str(e)}")
        raise ExternalServiceError(f"Error creando carátula: {str(e)}")


async def _direct_pdf_flow_transfer(payload: Dict[str, Any], *, schema_name: str) -> Dict[str, Any]:
    """
    Phase 6: Flujo directo para generar pases PV.

    Ejecuta:
    1. PDFComposer /move/ → Generar PDF
    2. Notary /sign-pdf → Firmar PDF (CON official_number + city)
    3. Upload a R2 oficial

    Args:
        payload: Dict con datos del documento (mismo formato que Legal Orchestrator)
        schema_name: Schema del tenant para R2

    Returns:
        Dict con status=success (compatible con respuesta anterior)

    Raises:
        ExternalServiceError: Si falla algún paso
    """
    logger.info("Iniciando flujo directo para pase...")

    try:
        # Paso 1: Generar PDF con PDFComposer /move/
        logger.info("1/3: Generando PDF con PDFComposer /move/...")

        from services.shared.pdfcomposer_api import call_pdfcomposer_create_transfer

        pdf_bytes = await call_pdfcomposer_create_transfer(payload)
        logger.info(f"PDF generado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

        # Paso 2: Firmar PDF con Notary (CON official_number y city)
        logger.info("2/3: Firmando PDF con Notary...")

        from services.shared.notary_api import call_notary_sign_pdf

        signed_pdf_bytes = await call_notary_sign_pdf(
            pdf_bytes=pdf_bytes,
            signer_name=payload["signer_full_name"],
            signer_seal=payload["signer_seal"],
            signer_department=payload["signer_department"],
            signer_municipality=payload["signer_municipality"],
            official_number=payload["official_document_number"],
            city=payload["city_name"],
            tenant_id=schema_name  # Para firma PAdES
        )
        logger.info(f"PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

        # Paso 3: Subir a R2 oficial
        logger.info("3/3: Subiendo a R2 oficial...")

        from services.storage.cloudflare import get_tenant_r2_client
        r2_client = get_tenant_r2_client(schema_name=schema_name)

        filename_oficial = f"{payload['official_document_number']}.pdf"
        await run_in_threadpool(r2_client.upload_oficial, signed_pdf_bytes, filename_oficial)
        logger.info(f"Subido a R2 oficial: {filename_oficial}")

        logger.info("Flujo completado exitosamente")

        # Retornar respuesta compatible con formato anterior
        return {
            "status": "success",
            "message": f"Pase creado exitosamente: {payload['official_document_number']}",
            "data": {
                "official_document_number": payload['official_document_number'],
                "signed_url": f"https://cloudflare.r2/oficial/{filename_oficial}"
            }
        }

    except Exception as e:
        logger.error(f"Error en flujo directo: {str(e)}")
        raise ExternalServiceError(f"Error creando pase: {str(e)}")
