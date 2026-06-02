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

FLUJO GENERAL:
MOMENTO 1 (lock corto ~5ms):
  1. Crear documento draft
  2. Guardar contenido HTML
  3. Recolectar datos (document_type_id, reference, signers, sector_ids)
  4. Construir payload (sin official_document_number) y llamar generate_official_number()
     → INSERT en official_documents con signed_at=NULL

MOMENTO 2 (sin lock):
  5. Generar PDF + Firmar (PDFComposer + Notary) + Subir a R2
  6. Si OK: UPDATE official_documents SET signed_at, signers
  7. Si falla: re-raise (la fila queda con signed_at=NULL como hueco aceptable)
  8. Actualizar a 'signed'

IMPORTANTE: Este helper NO vincula el documento al case.
El llamador debe usar CaseService.link_official_document() después
para que el order_number se calcule correctamente con SELECT FOR UPDATE.
"""

from typing import Dict, Any, Callable
from datetime import datetime
from fastapi.concurrency import run_in_threadpool

from database import get_conn, fetch_one, execute
from shared.exceptions import (
    ValidationError, ExternalServiceError, DocumentNotFoundError
)
from shared.logging import get_logger
from shared.numbering import generate_official_number
from services.documents.lifecycle.creation import create_document

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

    Flujo:
    MOMENTO 1: Crear draft → recolectar datos → construir payload → generate_official_number
               (la función abre su propia conexión, lock ultra corto ~5ms)
    MOMENTO 2: PDFComposer + Notary + R2 → UPDATE oficial si OK
               Si falla: re-raise, signed_at=NULL queda como hueco aceptable.

    Args:
        document_type_acronym: Acrónimo del tipo (ej: "CAEX", "PV")
        reference: Referencia del documento
        html_builder: Función que retorna el HTML del documento
        payload_builder: Función que construye payload para PDFComposer
            Recibe: (document_id, document_type_name, official_number, user_id)
            Retorna: Dict con payload completo
        orchestrator_endpoint: Endpoint del flujo directo (ej: "/create-case-cover")
        case_id: UUID del expediente
        user_id: UUID del usuario creador (será numerador)
        connection: Ignorado (se mantiene por compatibilidad de firma).
                    La función usa siempre su propia conexión.

    Returns:
        Dict con:
            - success: bool
            - document_id: str
            - official_number: str
            - message: str

    Raises:
        ValidationError: Si faltan datos o validaciones fallan
        ExternalServiceError: Si falla PDFComposer/Notary/R2
    """
    logger.info(f"Iniciando creación de documento {document_type_acronym}")
    logger.info(f"Case ID: {case_id[:8]}...")
    logger.info(f"User ID: {user_id[:8]}...")

    # ================================================================
    # PASO 1: Crear documento en draft
    # ================================================================
    logger.info(f"PASO 1: Creando documento {document_type_acronym}...")
    document = await create_document(
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

        html_content = html_builder()

        logger.info("Guardando contenido...")

        content_structure = {
            "html": html_content,
            "format_version": "2.0",
            "updated_at": datetime.now().isoformat()
        }

        await execute(
            """
            UPDATE document_draft
            SET content = $1::jsonb,
                last_modified_at = CURRENT_TIMESTAMP
            WHERE id = $2
            """,
            content_structure,
            document_id,
            schema_name=schema_name,
        )

        logger.info("Contenido guardado")

        # ================================================================
        # PASO 3: Recolectar datos para generate_official_number
        #         (document_type_id, reference, signers, signer_sector_ids)
        # ================================================================
        logger.info("PASO 3: Recolectando datos para generate_official_number...")
        current_year = datetime.now().year

        # Obtener document_type_id
        type_result = await fetch_one(
            "SELECT id as document_type_id FROM document_types WHERE acronym = $1",
            document_type_acronym,
            schema_name=schema_name,
        )
        if not type_result:
            raise ValidationError(f"Tipo de documento {document_type_acronym} no encontrado")
        document_type_id = type_result['document_type_id']

        # Obtener reference del documento
        doc_data = await fetch_one(
            "SELECT reference FROM document_draft WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )
        if not doc_data:
            raise DocumentNotFoundError(document_id)
        reference_text = doc_data['reference']

        # Obtener firmantes
        signers_result = await fetch_one(
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
            document_id,
            schema_name=schema_name,
        )
        signers_data = signers_result['signers'] if signers_result else []

        # Obtener sector_ids de los firmantes
        signer_sectors_result = await fetch_one(
            """
            SELECT ARRAY_AGG(DISTINCT u.sector_id) FILTER (WHERE u.sector_id IS NOT NULL) as sector_ids
            FROM document_signers ds
            JOIN users u ON ds.user_id = u.id
            WHERE ds.document_id = $1
            """,
            document_id,
            schema_name=schema_name,
        )
        signer_sector_ids = signer_sectors_result['sector_ids'] if signer_sectors_result else None

        logger.info("Datos recolectados")

        # ================================================================
        # PASO 4: Construir payload para PDFComposer y reservar numero oficial
        #         generate_official_number abre su propia conexión,
        #         lock ultra corto ~5ms, INSERT con signed_at=NULL.
        # ================================================================
        logger.info("PASO 4: Construyendo payload y generando número oficial (lock ultra corto ~5ms)...")

        # Construir payload sin official_document_number: ese campo
        # no es usado por PDFComposer (no está en sus modelos Pydantic
        # ni en los templates). El INSERT en official_documents guarda
        # este payload limpio como content.
        payload = payload_builder(document_id, document_type_name, None, user_id)
        # Quitar official_document_number si el builder lo incluyó
        payload.pop("official_document_number", None)
        logger.info(f"Payload construido con {len(payload)} campos")

        official_number, department_id, global_sequence = await generate_official_number(
            document_type_acronym=document_type_acronym,
            user_id=user_id,
            year=current_year,
            schema_name=schema_name,
            document_id=document_id,
            reference=reference_text,
            document_type_id=document_type_id,
            content=payload,
            signers=signers_data,
            signer_sector_ids=signer_sector_ids,
        )

        logger.info(f"Número oficial: {official_number}")
        logger.info(f"Global sequence: {global_sequence}")

        # Inyectar official_number en payload para uso de Notary y R2
        # (no se persiste en official_documents.content, solo se usa en memoria)
        payload["official_document_number"] = official_number

        # ================================================================
        # PASO 6: Generar PDF, firmar y subir a R2 (MOMENTO 2)
        # ================================================================
        logger.info(f"PASO 6: Ejecutando flujo directo {orchestrator_endpoint}...")

        try:
            api_result = await _route_document_creation(orchestrator_endpoint, payload, schema_name=schema_name)
            logger.info("Flujo directo exitoso")

        except Exception as e:
            # PDFComposer/Notary/R2 falló.
            # NO hacemos DELETE de official_documents.
            # La fila queda con signed_at=NULL (hueco aceptable por diseño).
            logger.error(f"Flujo directo falló: {str(e)}")
            logger.error(
                f"official_documents queda con signed_at=NULL para doc={document_id}. "
                f"Número {official_number} reservado como hueco aceptable."
            )
            raise ExternalServiceError(f"Error en flujo directo: {str(e)}")

        # ================================================================
        # PASO 7: UPDATE official_documents (signed_at + signers)
        #         NO INSERT - generate_official_number ya insertó con signed_at=NULL
        # ================================================================
        logger.info("PASO 7: Confirmando firma en official_documents (UPDATE)...")

        await execute(
            """
            UPDATE official_documents
            SET signed_at = CURRENT_TIMESTAMP
            WHERE id = $1 AND signed_at IS NULL
            """,
            document_id,
            schema_name=schema_name,
        )

        # Actualizar el firmante en el array signers con jsonb_set
        # Formato ISO con T y Z para consistencia con numerator.py (commit 490cd24)
        await execute(
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
            schema_name=schema_name,
        )

        logger.info("official_documents actualizado con signed_at y signers")

        # ================================================================
        # PASO 8: Actualizar a 'signed'
        # ================================================================
        logger.info("PASO 8: Actualizando estados a 'signed'...")

        await execute(
            "UPDATE document_draft SET status = 'signed' WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )
        await execute(
            """
            UPDATE document_signers
            SET status = 'signed', signed_at = CURRENT_TIMESTAMP
            WHERE document_id = $1 AND user_id = $2
            """,
            document_id,
            user_id,
            schema_name=schema_name,
        )

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
        # El llamador maneja el error. El usuario reintenta manualmente.
        logger.error(
            f"ERROR GENERAL en create_and_sign_case_document ({document_type_acronym}): {str(e)} | "
            f"doc={document_id} queda como draft con signed_at=NULL en official_documents "
            f"(si generate_official_number ya ejecutó). Hueco aceptable por diseño."
        )
        raise


async def _route_document_creation(endpoint: str, payload: Dict[str, Any], *, schema_name: str) -> Dict[str, Any]:
    """
    Enrutador de flujos directos PDFComposer + Notary + R2.

    Detecta el endpoint y ejecuta flujo directo correspondiente:
    - /create-case-cover → PDFComposer /create-case/ + Notary + R2 oficial
    - /create-transfer-document → PDFComposer /move/ + Notary + R2 oficial
    - /create-ifrlm → PDFComposer /ifrlm/ + Notary + R2 oficial

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
        logger.info("Ejecutando flujo directo para CAEX...")
        from services.shared.pdfcomposer_api import call_pdfcomposer_create_case
        return await _direct_pdf_flow(
            call_pdfcomposer_create_case, payload,
            schema_name=schema_name, label="caratula"
        )

    elif endpoint == "/create-transfer-document":
        logger.info("Ejecutando flujo directo para PV...")
        from services.shared.pdfcomposer_api import call_pdfcomposer_create_transfer
        return await _direct_pdf_flow(
            call_pdfcomposer_create_transfer, payload,
            schema_name=schema_name, label="pase"
        )

    elif endpoint == "/create-ifrlm":
        logger.info("Ejecutando flujo directo para IFRLM...")
        from services.shared.pdfcomposer_api import call_pdfcomposer_create_ifrlm
        return await _direct_pdf_flow(
            call_pdfcomposer_create_ifrlm, payload,
            schema_name=schema_name, label="informe de legajo"
        )

    else:
        raise NotImplementedError(
            f"Endpoint {endpoint} no soportado. "
            f"Endpoints soportados: /create-case-cover, /create-transfer-document, /create-ifrlm"
        )


async def _direct_pdf_flow(
    pdfcomposer_call,
    payload: Dict[str, Any],
    *,
    schema_name: str,
    label: str = "documento"
) -> Dict[str, Any]:
    """
    Flujo generico: PDFComposer -> Notary -> R2.

    Ejecuta:
    1. PDFComposer (via pdfcomposer_call) -> Generar PDF
    2. Notary /sign-pdf -> Firmar PDF (CON official_number + city)
    3. Upload a R2 oficial

    Args:
        pdfcomposer_call: Funcion async que llama a PDFComposer
        payload: Dict con datos del documento
        schema_name: Schema del tenant para R2
        label: Etiqueta para logs (ej: "caratula", "pase", "informe de legajo")

    Returns:
        Dict con status=success

    Raises:
        ExternalServiceError: Si falla algun paso
    """
    logger.info(f"Iniciando flujo directo para {label}...")

    try:
        # Paso 1: Generar PDF con PDFComposer
        logger.info(f"1/3: Generando PDF para {label}...")
        pdf_bytes = await pdfcomposer_call(payload, schema_name=schema_name)
        logger.info(f"PDF generado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

        # Paso 2: Firmar PDF con Notary
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
            tenant_id=schema_name,
            schema_name=schema_name
        )
        logger.info(f"PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

        # Paso 3: Subir a R2 oficial
        logger.info("3/3: Subiendo a R2 oficial...")
        from services.storage.cloudflare import get_tenant_r2_client
        r2_client = await get_tenant_r2_client(schema_name=schema_name)

        filename_oficial = f"{payload['official_document_number']}.pdf"
        await run_in_threadpool(r2_client.upload_oficial, signed_pdf_bytes, filename_oficial)
        logger.info(f"Subido a R2 oficial: {filename_oficial}")

        logger.info(f"Flujo completado exitosamente para {label}")

        return {
            "status": "success",
            "message": f"{label.capitalize()} creado exitosamente: {payload['official_document_number']}",
            "data": {
                "official_document_number": payload['official_document_number'],
                "signed_url": f"https://cloudflare.r2/oficial/{filename_oficial}"
            }
        }

    except Exception as e:
        logger.error(f"Error en flujo directo: {str(e)}")
        raise ExternalServiceError(f"Error creando {label}: {str(e)}")
