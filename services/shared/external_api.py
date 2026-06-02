"""
Servicios para integración con APIs externas.
Maneja todas las llamadas a servicios externos y su integración con el sistema.
"""

from typing import Dict, Any, List, Optional
from shared.exceptions import ExternalServiceError, ValidationError
from shared.validation import validate_document_id, validate_user_id
from shared.utils import generate_uuid
from shared.context import get_correlation_id
from fastapi.concurrency import run_in_threadpool
import uuid
import os
import asyncio
import httpx

from config.constants import DEFAULT_LOGO_URL
from shared.logging import get_logger

# Configurar logger para este módulo (usa el formatter con correlation_id)
logger = get_logger(__name__)

async def generate_final_document_pdf(document_id: str, document_data: Dict[str, Any], signers: List[Dict[str, Any]], *, schema_name: str) -> Dict[str, Any]:
    """
    Genera el PDF final del documento para iniciar firma.

    Para documentos HTML: Llama a PDFComposer y sube a R2.
    Para documentos IMPORTADOS: El PDF ya existe en R2, solo verifica y genera URL.

    Args:
        document_id: UUID del documento
        document_data: Datos del documento (referencia, contenido, etc.)
        signers: Lista de firmantes del documento
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Diccionario con el document_id y metadata del documento generado

    Raises:
        ExternalServiceError: Si hay error en la API externa
    """
    # NOTA: document_id ya fue validado por el servicio llamador (signing.py)
    # No re-validamos aquí para evitar llamadas duplicadas a BD

    # Agregar document_id a document_data si no está
    if 'document_id' not in document_data:
        document_data['document_id'] = document_id

    logger.info(f"Generando PDF para documento {document_id[:8]}, tipo={document_data.get('type_name', 'N/A')}, firmantes={len(signers)}")

    # Detectar si es documento importado (content=NULL o vacío)
    content = document_data.get('content')
    is_imported = content is None or content == '' or content == {}

    from services.storage.cloudflare import get_tenant_r2_client
    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    filename = document_id.replace('-', '') + '.pdf'

    try:
        if is_imported:
            # ============================================================
            # DOCUMENTO IMPORTADO: El PDF ya existe en R2, solo verificar
            # ============================================================
            # Verificar que el archivo existe en R2
            exists = await run_in_threadpool(r2_client.exists_tosign, filename)

            if not exists:
                raise ExternalServiceError(
                    f"PDF de documento importado no encontrado en R2: {filename}. "
                    "El documento debe tener un PDF subido antes de iniciar firma."
                )

            logger.debug(f"PDF importado verificado en R2: {filename}")

            # Generar URL firmada temporal
            document_url = await run_in_threadpool(r2_client.get_tosign_url, filename)

            if not document_url:
                raise ExternalServiceError("No se pudo generar URL firmada para el PDF importado")

            logger.debug(f"URL firmada generada para PDF importado (expira en 600s)")

            return {
                "status": "success",
                "document_generate_id": document_id,
                "document_url": document_url,
                "api_mode": "imported_r2",  # Indica documento importado
                "upload_info": {"filename": filename, "already_existed": True},
                "file_size": 0  # No conocemos el tamaño sin descargarlo
            }

        else:
            # ============================================================
            # DOCUMENTO HTML: Generar PDF con PDFComposer y subir a R2
            # ============================================================
            # Detectar si es NOTA para usar endpoint específico con recipients
            type_acronym = document_data.get('type_acronym') or document_data.get('document_type_acronym')

            if type_acronym in ('NOTA', 'MEMO'):
                # NOTA y MEMO usan /note/ con header de recipients
                logger.debug(f"Generando {type_acronym} con recipients")
                from services.shared.pdfcomposer_api import call_pdfcomposer_note_final

                if type_acronym == 'NOTA':
                    from services.notes.recipients import format_recipients_for_pdf
                    recipients = await format_recipients_for_pdf(document_id, schema_name=schema_name)
                else:
                    from services.memos.recipients import format_memo_recipients_for_pdf
                    recipients = await format_memo_recipients_for_pdf(document_id, schema_name=schema_name)

                pdf_bytes = await call_pdfcomposer_note_final(
                    document_data,
                    para=recipients['para'],
                    cc=recipients.get('cc'),
                    schema_name=schema_name
                )
            else:
                # Otros tipos usan /generate-pdf/ genérico
                pdf_bytes = await call_pdfcomposer_generate_pdf(document_data, signers, schema_name=schema_name)

            logger.debug(f"PDF generado: {len(pdf_bytes)} bytes")

            # Subir PDF a Cloudflare R2 tosign
            upload_result = await run_in_threadpool(r2_client.upload_tosign, pdf_bytes, filename)
            logger.debug(f"PDF subido a R2: {filename}")

            # Generar URL firmada temporal
            document_url = await run_in_threadpool(r2_client.get_tosign_url, filename)

            if not document_url:
                raise ExternalServiceError("No se pudo generar URL firmada para el PDF")

            logger.debug(f"URL firmada generada (expira en 600s)")

            return {
                "status": "success",
                "document_generate_id": document_id,
                "document_url": document_url,
                "api_mode": "direct_r2",  # Indica documento HTML
                "upload_info": upload_result,
                "file_size": len(pdf_bytes)
            }

    except ValidationError:
        # Re-raise validaciones sin modificar
        raise
    except ExternalServiceError:
        # Re-raise errores de servicios externos sin modificar
        raise
    except Exception as e:
        # Cualquier otro error, wrap en ExternalServiceError
        logger.error(f"Error generando PDF: {type(e).__name__} - {str(e)}")
        raise ExternalServiceError(f"Error generando PDF: {str(e)}")

async def call_signature_stamping_api(document_id: str, user_id: str, current_pdf_id: str = None) -> Dict[str, Any]:
    """
    Llama a la API externa para estampar una firma en el documento.
    
    Args:
        document_id: UUID del documento
        user_id: UUID del usuario que firma
        current_pdf_id: ID del PDF actual (opcional)
        
    Returns:
        Dict con el resultado de la operación
        
    Raises:
        ExternalServiceError: Si hay error en la API externa
    """
    # NOTA: document_id y user_id ya fueron validados por el servicio llamador
    # No re-validamos aquí para evitar llamadas duplicadas a BD

    # URL de la API de estampado de firmas
    signature_api_url = os.getenv('SIGNATURE_STAMPING_API_URL')
    
    if not signature_api_url:
        # Modo MOCK
        logger.warning(f"MODO MOCK: Simulando estampado de firma para documento {document_id}")

        # Simular tiempo de procesamiento de firma
        await asyncio.sleep(1.0)
        
        mock_signed_pdf_id = str(uuid.uuid4())
        
        return {
            "success": True,
            "message": "Firma estampada exitosamente (MOCK)",
            "signed_pdf_id": mock_signed_pdf_id,
            "signature_info": {
                "signature_timestamp": "now",
                "signature_method": "digital",
                "certificate_info": "Mock Certificate"
            },
            "api_mode": "mock"
        }
    
    else:
        # Modo REAL
        try:
            import httpx
            
            payload = {
                "document_id": document_id,
                "user_id": user_id,
                "pdf_id": current_pdf_id,
                "signature_options": {
                    "include_timestamp": True,
                    "include_certificate": True,
                    "signature_type": "digital"
                }
            }
            
            async with httpx.AsyncClient(timeout=90.0) as client:  # Más tiempo para firmas
                response = await client.post(
                    f"{signature_api_url}/stamp-signature",
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": os.getenv('SIGNATURE_API_KEY', ''),
                        "X-Correlation-ID": get_correlation_id(),
                        "User-Agent": "GDI-Backend/1.0"
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    
                    return {
                        "success": True,
                        "message": "Firma estampada exitosamente",
                        "signed_pdf_id": result.get("signed_pdf_id"),
                        "signature_info": result.get("signature_info", {}),
                        "api_response": result,
                        "api_mode": "real"
                    }
                
                else:
                    raise ExternalServiceError(
                        f"Error en API de estampado: {response.status_code}",
                        details={"status_code": response.status_code, "response": response.text}
                    )
                    
        except httpx.TimeoutException:
            raise ExternalServiceError("Timeout en API de estampado de firmas")
        except httpx.RequestError as e:
            raise ExternalServiceError(f"Error de conexión con API de estampado: {str(e)}")
        except Exception as e:
            if isinstance(e, ExternalServiceError):
                raise
            else:
                raise ExternalServiceError(f"Error inesperado en API de estampado: {str(e)}")

async def call_external_signing_api_for_numerator(document_id: str, user_id: str, official_number: str) -> Dict[str, Any]:
    """
    Llama a la API externa para el proceso de firma del numerador.
    
    Args:
        document_id: UUID del documento
        user_id: UUID del numerador
        official_number: Número oficial del documento
        
    Returns:
        Dict con el resultado de la operación
    """
    # NOTA: document_id y user_id ya fueron validados por el servicio llamador
    # No re-validamos aquí para evitar llamadas duplicadas a BD

    # URL de la API de firma de numerador
    numerator_api_url = os.getenv('NUMERATOR_SIGNING_API_URL')
    
    if not numerator_api_url:
        # Modo MOCK
        logger.warning(f"MODO MOCK: Simulando firma de numerador para documento {document_id} con número {official_number}")

        # Simular proceso de numeración y firma
        await asyncio.sleep(1.5)
        
        mock_final_pdf_id = str(uuid.uuid4())
        
        return {
            "success": True,
            "message": "Numeración y firma completada exitosamente (MOCK)",
            "final_pdf_id": mock_final_pdf_id,
            "official_number": official_number,
            "signed_pdf_url": f"https://mock-api.example.com/documents/{document_id}/signed.pdf",
            "url_pdf_firmado_1": f"https://mock-api.example.com/documents/{document_id}/signed-v1.pdf",
            "completion_info": {
                "completed_at": "now",
                "numerator_signature": "Mock Numerator Signature",
                "final_document_url": f"/documents/{document_id}/final.pdf"
            },
            "api_mode": "mock"
        }
    
    else:
        # Modo REAL
        try:
            import httpx
            
            payload = {
                "document_id": document_id,
                "numerator_id": user_id,
                "official_number": official_number,
                "completion_options": {
                    "include_official_seal": True,
                    "generate_final_pdf": True,
                    "archive_document": True
                }
            }
            
            async with httpx.AsyncClient(timeout=120.0) as client:  # Más tiempo para proceso completo
                response = await client.post(
                    f"{numerator_api_url}/complete-numeration",
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": os.getenv('NUMERATOR_API_KEY', ''),
                        "X-Correlation-ID": get_correlation_id(),
                        "User-Agent": "GDI-Backend/1.0"
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    
                    return {
                        "success": True,
                        "message": "Numeración y firma completada exitosamente",
                        "final_pdf_id": result.get("final_pdf_id"),
                        "official_number": official_number,
                        "completion_info": result.get("completion_info", {}),
                        "api_response": result,
                        "api_mode": "real"
                    }
                
                else:
                    raise ExternalServiceError(
                        f"Error en API de numeración: {response.status_code}",
                        details={"status_code": response.status_code, "response": response.text}
                    )
                    
        except httpx.TimeoutException:
            raise ExternalServiceError("Timeout en API de numeración")
        except httpx.RequestError as e:
            raise ExternalServiceError(f"Error de conexión con API de numeración: {str(e)}")
        except Exception as e:
            if isinstance(e, ExternalServiceError):
                raise
            else:
                raise ExternalServiceError(f"Error inesperado en API de numeración: {str(e)}")

async def validate_external_document(document_id: str, validation_type: str = "integrity") -> Dict[str, Any]:
    """
    Valida un documento usando servicios externos.
    
    Args:
        document_id: UUID del documento
        validation_type: Tipo de validación ('integrity', 'signatures', 'full')
        
    Returns:
        Dict con resultado de validación
    """
    # URL de API de validación
    validation_api_url = os.getenv('DOCUMENT_VALIDATION_API_URL')
    
    if not validation_api_url:
        # Modo MOCK
        logger.warning(f"MODO MOCK: Simulando validación de documento {document_id}, tipo={validation_type}")

        await asyncio.sleep(0.8)
        
        return {
            "valid": True,
            "validation_type": validation_type,
            "validation_results": {
                "integrity_check": "passed",
                "signature_verification": "all_valid",
                "document_status": "authentic"
            },
            "api_mode": "mock"
        }
    
    else:
        # Modo REAL
        try:
            import httpx
            
            payload = {
                "document_id": document_id,
                "validation_type": validation_type,
                "validation_options": {
                    "check_signatures": True,
                    "verify_timestamps": True,
                    "validate_certificates": True
                }
            }
            
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{validation_api_url}/validate-document",
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": os.getenv('VALIDATION_API_KEY', ''),
                        "X-Correlation-ID": get_correlation_id(),
                        "User-Agent": "GDI-Backend/1.0"
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return {**result, "api_mode": "real"}
                else:
                    raise ExternalServiceError(
                        f"Error en API de validación: {response.status_code}",
                        details={"status_code": response.status_code}
                    )
                    
        except Exception as e:
            if isinstance(e, ExternalServiceError):
                raise
            else:
                raise ExternalServiceError(f"Error en validación externa: {str(e)}")

def get_external_services_status() -> Dict[str, Any]:
    """
    Verifica el estado de todos los servicios externos.
    
    Returns:
        Dict con estado de servicios externos
    """
    services = {
        "pdf_generation": {
            "url": os.getenv('PDF_GENERATION_API_URL'),
            "configured": bool(os.getenv('PDF_GENERATION_API_URL')),
            "mode": "real" if os.getenv('PDF_GENERATION_API_URL') else "mock"
        },
        "signature_stamping": {
            "url": os.getenv('SIGNATURE_STAMPING_API_URL'),
            "configured": bool(os.getenv('SIGNATURE_STAMPING_API_URL')),
            "mode": "real" if os.getenv('SIGNATURE_STAMPING_API_URL') else "mock"
        },
        "numerator_signing": {
            "url": os.getenv('NUMERATOR_SIGNING_API_URL'),
            "configured": bool(os.getenv('NUMERATOR_SIGNING_API_URL')),
            "mode": "real" if os.getenv('NUMERATOR_SIGNING_API_URL') else "mock"
        },
        "document_validation": {
            "url": os.getenv('DOCUMENT_VALIDATION_API_URL'),
            "configured": bool(os.getenv('DOCUMENT_VALIDATION_API_URL')),
            "mode": "real" if os.getenv('DOCUMENT_VALIDATION_API_URL') else "mock"
        }
    }
    
    # Contar servicios configurados
    configured_count = sum(1 for service in services.values() if service['configured'])
    total_services = len(services)
    
    return {
        "services": services,
        "summary": {
            "total_services": total_services,
            "configured_services": configured_count,
            "mock_services": total_services - configured_count,
            "all_configured": configured_count == total_services
        }
    }


async def get_document_pdf_url(document_id: str, document_generate_id: str, document_status: str, *, schema_name: str) -> Optional[str]:
    """
    Obtiene URL del PDF desde Cloudflare R2 directamente según el estado del documento.

    Esta función reemplaza las llamadas a Legal Orchestrator por acceso directo a R2 usando boto3.

    Flujo por estado:
    - sent_to_sign → bucket del tenant (tosign) con document_generate_id (sin guiones)
    - signed → bucket del tenant (oficial) con official_number

    Args:
        document_id: UUID del documento (para buscar official_number si está signed)
        document_generate_id: UUID del documento generado (para sent_to_sign)
        document_status: Estado del documento ('sent_to_sign' o 'signed')
        schema_name: Schema del tenant (para multi-tenant)

    Returns:
        URL firmada temporal (expira en 600 segundos) o None si falla

    Ejemplos:
        >>> # Para documento en firma
        >>> url = get_document_pdf_url(doc_id, "214c5d16-95ea-4865-876d-e8e826ef3ece", "sent_to_sign", "tenant_schema")
        >>>
        >>> # Para documento firmado
        >>> url = get_document_pdf_url(doc_id, generate_id, "signed", "tenant_schema")
    """
    from services.storage.cloudflare import get_tenant_r2_client

    logger.debug(f"Obteniendo URL de PDF: doc={document_id}, status={document_status}")

    r2_client = await get_tenant_r2_client(schema_name=schema_name)

    if document_status == "sent_to_sign":
        # Documentos en proceso de firma: bucket tosign
        # El filename debe ser el UUID sin guiones + .pdf
        filename = document_generate_id.replace('-', '') + '.pdf'
        logger.debug(f"Obteniendo URL de tosign: {filename}")
        return await run_in_threadpool(r2_client.get_tosign_url, filename)

    elif document_status == "signed":
        # Documentos firmados: bucket oficial
        # Necesitamos buscar el official_number en la BD
        from database import fetch_one

        logger.debug(f"Buscando official_number en BD...")

        query = """
            SELECT official_number
            FROM official_documents
            WHERE id = $1
              AND signed_at IS NOT NULL
        """

        result = await fetch_one(query, document_id, schema_name=schema_name)

        if result and result.get("official_number"):
            official_number = result["official_number"]
            logger.debug(f"Official number encontrado, obteniendo URL oficial")
            return await run_in_threadpool(r2_client.get_oficial_url, official_number)
        else:
            logger.error(f"No se encontró official_number para document_id {document_id}")
            return None

    else:
        logger.error(f"Status '{document_status}' no soportado (solo 'sent_to_sign' o 'signed')")
        return None


async def call_pdfcomposer_generate_pdf(
    document_data: Dict[str, Any],
    signers: List[Dict[str, Any]],
    *,
    schema_name: str
) -> bytes:
    """
    Llama directamente a PDFComposer para generar PDF.

    PHASE 2: Reemplaza la llamada a Legal Orchestrator /start-signatures
    manteniendo PDFComposer como servicio externo HTTP.

    Args:
        document_data: Dict con keys:
            - document_id: UUID del documento
            - reference: Referencia del documento
            - content: Contenido HTML o dict {html: ...}
            - type_name: Nombre del tipo de documento
            - type_acronym: Acrónimo del tipo
        signers: Lista de firmantes (no se envía a PDFComposer, solo para contexto)

    Returns:
        bytes: PDF generado por PDFComposer

    Raises:
        ValidationError: Si faltan campos requeridos
        ExternalServiceError: Si PDFComposer falla o no responde
    """
    import json
    import asyncio

    # Validar document_id (solo formato, no existencia en BD)
    document_id = document_data.get('document_id')
    if not document_id:
        raise ValidationError("document_id es requerido para generar PDF")

    # NOTA: No validamos existencia en BD aquí porque ya fue validado
    # por el servicio llamador (generate_final_document_pdf → signing.py)

    # Extraer campos para PDFComposer
    type_acronym = document_data.get('type_acronym') or document_data.get('document_type_acronym')
    type_name = document_data.get('type_name') or document_data.get('document_type_name')
    reference = document_data.get('reference')
    content_raw = document_data.get('content', '')

    # Validar campos requeridos
    if not type_acronym:
        raise ValidationError("type_acronym es requerido para generar PDF")
    if not type_name:
        raise ValidationError("type_name es requerido para generar PDF")
    if not reference:
        raise ValidationError("reference es requerido para generar PDF")

    # Extraer HTML del contenido si es dict
    if isinstance(content_raw, dict) and 'html' in content_raw:
        content_html = content_raw['html']
    elif isinstance(content_raw, str):
        content_html = content_raw
    else:
        content_html = str(content_raw) if content_raw else ""

    # Convertir contenido a formato JSON para PDFComposer
    # PDFComposer espera Text como JSON string
    text_json = json.dumps({"html": content_html})

    # Obtener credenciales desde .env
    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise RuntimeError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise RuntimeError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = document_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    # Preparar payload para PDFComposer (multipart/form-data)
    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": type_acronym,
        "TypeDocument": type_name,
        "Reference": reference,
        "Text": text_json
    }

    # Inyectar frase_anual desde settings del tenant
    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = (await get_tenant_settings(schema_name)).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"Generando PDF con PDFComposer: {reference}, tipo={type_name}")

    # Llamar a PDFComposer con retry (igual que Legal Orchestrator)
    timeout = httpx.Timeout(90.0)  # Timeout largo para generación de PDF
    headers = {
        "X-API-Key": pdfcomposer_api_key,
        "X-Correlation-ID": get_correlation_id()
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):  # 1 reintento
            try:
                logger.debug(f"Intento {attempt + 1}/2 de generación de PDF")

                response = await client.post(
                    f"{pdfcomposer_url}/generate-pdf/",
                    data=pdfcomposer_data,
                    headers=headers
                )

                response.raise_for_status()

                # Leer contenido directo en memoria sin archivos temporales
                pdf_bytes = await response.aread()
                pdf_size = len(pdf_bytes)

                logger.info(f"PDF generado exitosamente: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

                # Validar que recibimos un PDF válido
                if pdf_size == 0:
                    raise ExternalServiceError("PDFComposer retornó PDF vacío")

                # Validar magic bytes (opcional pero recomendado)
                if not pdf_bytes.startswith(b'%PDF'):
                    logger.warning(f"Respuesta de PDFComposer no parece ser PDF válido")

                # Validar tamaño máximo (10MB como Legal Orchestrator)
                MAX_PDF_SIZE = 10 * 1024 * 1024  # 10MB
                if pdf_size > MAX_PDF_SIZE:
                    raise ExternalServiceError(f"PDF excede tamaño máximo ({pdf_size/1024/1024:.2f}MB > 10MB)")

                return pdf_bytes

            except httpx.HTTPStatusError as e:
                logger.warning(f"HTTP Error {e.response.status_code} en PDFComposer (intento {attempt + 1})")

                if attempt == 1:  # Último intento
                    raise ExternalServiceError(
                        f"PDFComposer error: HTTP {e.response.status_code}",
                        details={"response": e.response.text[:500]}
                    )

            except httpx.TimeoutException:
                logger.warning(f"Timeout en PDFComposer (90s excedido) (intento {attempt + 1})")
                if attempt == 1:
                    raise ExternalServiceError("PDFComposer timeout después de 90 segundos")

            except httpx.RequestError as e:
                logger.warning(f"Error de conexión con PDFComposer (intento {attempt + 1}): {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error de conexión con PDFComposer: {str(e)}")

            except ExternalServiceError:
                # Re-raise ExternalServiceError sin modificar
                if attempt == 1:
                    raise

            except Exception as e:
                logger.warning(f"Error inesperado en PDFComposer (intento {attempt + 1}): {type(e).__name__}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error inesperado en PDFComposer: {str(e)}")

            # Si no es el último intento, esperar antes de retry
            if attempt == 0:
                logger.debug(f"Esperando 1s antes de reintentar PDFComposer...")
                await asyncio.sleep(1)

    # No debería llegar aquí, pero por si acaso
    raise ExternalServiceError("PDFComposer: error desconocido después de reintentos")