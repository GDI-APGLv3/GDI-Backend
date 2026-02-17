"""
Servicios para integración con APIs externas.
Maneja todas las llamadas a servicios externos y su integración con el sistema.
"""

from typing import Dict, Any, List, Optional
from shared.exceptions import ExternalServiceError, ValidationError
from shared.validation import validate_document_id, validate_user_id
from shared.utils import generate_uuid
from shared.config import get_external_api_config
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
    r2_client = get_tenant_r2_client(schema_name=schema_name)
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

            if type_acronym == 'NOTA':
                # NOTA usa /note/ con header de recipients
                logger.debug(f"Generando NOTA con recipients")
                from services.notes.recipients import format_recipients_for_pdf
                from services.shared.pdfcomposer_api import call_pdfcomposer_note_final

                recipients = format_recipients_for_pdf(document_id, schema_name=schema_name)

                pdf_bytes = await call_pdfcomposer_note_final(
                    document_data,
                    para=recipients['para'],
                    cc=recipients.get('cc')
                )
            else:
                # Otros tipos usan /generate-pdf/ genérico
                pdf_bytes = await call_pdfcomposer_generate_pdf(document_data, signers)

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

async def generate_document_preview_pdf(document_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Genera un PDF de previsualización del documento usando Legal Orchestrator API.
    
    Args:
        document_data: Datos completos del documento para generar la previsualización
        
    Returns:
        Dict con información del PDF generado
        
    Raises:
        ExternalServiceError: Si falla la generación del PDF
    """
    from shared.config import ExternalAPIConfig
    
    api_key = os.getenv("LEGAL_ORCHESTRATOR_API_KEY")
    
    try:
        if api_key:
            # Usar Legal Orchestrator API real
            return await call_legal_orchestrator_preview(document_data, api_key)
        else:
            # Usar generación mock si no hay API key configurada
            logger.warning("LEGAL_ORCHESTRATOR_API_KEY no configurada, usando MOCK")
            return await generate_mock_preview_pdf(document_data)

    except Exception as e:
        # Fallback a mock en caso de error
        logger.warning(f"Error con Legal Orchestrator API: {str(e)}, usando MOCK")
        return await generate_mock_preview_pdf(document_data)

async def call_legal_orchestrator_preview(document_data: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """
    Llama a Legal Orchestrator API para generar PDF de previsualización.
    
    Args:
        document_data: Datos del documento 
        api_key: API key para autenticación
        
    Returns:
        Dict con información del PDF generado
        
    Raises:
        ExternalServiceError: Si falla la llamada a la API
    """
    from shared.config import ExternalAPIConfig
    
    try:
        import httpx

        logger.info(f"Iniciando generación de preview PDF, documento={document_data.get('document_id', 'N/A')}")

        # Transformar document_data al formato que espera Legal Orchestrator
        payload = transform_to_start_signatures_request(document_data)

        # Llamar a la API
        async with httpx.AsyncClient(timeout=ExternalAPIConfig.LEGAL_ORCHESTRATOR_TIMEOUT) as client:
            response = await client.post(
                f"{ExternalAPIConfig.LEGAL_ORCHESTRATOR_BASE_URL}/preview-pdf",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": api_key,
                    "X-Correlation-ID": get_correlation_id(),
                    "User-Agent": "GDI-Backend/1.0"
                }
            )

            if response.status_code == 200:
                # PDF retornado como streaming - guardamos temporalmente
                pdf_content = response.content

                logger.info(f"PDF preview generado exitosamente - Size: {len(pdf_content)} bytes")

                return {
                    "success": True,
                    "pdf_content": pdf_content,
                    "message": "PDF de previsualización generado exitosamente"
                }
            else:
                error_msg = f"Legal Orchestrator error: HTTP {response.status_code}"
                logger.error(f"{error_msg}, response={response.text[:200]}")
                raise ExternalServiceError(error_msg, details={"response": response.text[:500]})

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP Error {e.response.status_code} en Legal Orchestrator preview")
        raise ExternalServiceError(f"Legal Orchestrator HTTP {e.response.status_code}")

    except httpx.TimeoutException:
        logger.error(f"Timeout en Legal Orchestrator preview ({ExternalAPIConfig.LEGAL_ORCHESTRATOR_TIMEOUT}s)")
        raise ExternalServiceError("Legal Orchestrator timeout")

    except httpx.RequestError as e:
        logger.error(f"Conexion error en Legal Orchestrator: {str(e)}")
        raise ExternalServiceError(f"Legal Orchestrator connection error: {str(e)}")

    except Exception as e:
        logger.error(f"Error inesperado en Legal Orchestrator preview: {type(e).__name__} - {str(e)}")
        raise ExternalServiceError(f"Legal Orchestrator error: {str(e)}")


async def call_legal_orchestrator_start_signatures(document_data: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """
    Llama al endpoint /start-signatures del Legal Orchestrator para generar PDFs persistentes
    y comenzar el proceso de firma.
    
    Args:
        document_data: Datos del documento desde _fetch_document_basic_info()
        api_key: API key para autenticación
        
    Returns:
        Dict con información del documento generado y URLs de firma
    """
    try:
        # Validar datos de entrada
        if not document_data:
            raise ValidationError("document_data es requerido")
        
        if not api_key:
            raise ValidationError("api_key es requerido")
        
        # Obtener configuración
        config = get_external_api_config()
        
        # Transformar datos al formato StartSignaturesRequest
        start_signatures_payload = transform_to_start_signatures_request(document_data)

        logger.info(f"Iniciando firma con Legal Orchestrator, documento={start_signatures_payload.get('document_reference', 'N/A')}, tipo={start_signatures_payload.get('document_type_name', 'N/A')}")

        # Realizar llamada HTTP
        async with httpx.AsyncClient(timeout=90.0) as client:  # Mayor timeout para procesamiento
            response = await client.post(
                f"{config.base_url}/start-signatures",
                json=start_signatures_payload,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": api_key,
                    "X-Correlation-ID": get_correlation_id(),
                    "User-Agent": "GDI-Backend/1.0"
                }
            )

            if response.status_code == 200:
                try:
                    result = response.json()
                except Exception as e:
                    logger.error(f"Error al parsear respuesta JSON de Legal Orchestrator: {e}")
                    raise ExternalServiceError(f"Legal Orchestrator devolvió respuesta inválida: {response.text}")

                # Legal Orchestrator devuelve estructura: {status, message, data: {filename, signed_url, expires_in}}
                data = result.get("data", {})
                document_id = data.get("filename")  # Legal Orchestrator usa filename como identificador
                document_url = data.get("signed_url")
                expires_info = data.get("expires_in")

                if document_id is None:
                    logger.error(f"Legal Orchestrator no devolvió filename en respuesta: {result}")
                    raise ExternalServiceError(
                        "Legal Orchestrator no devolvió filename del documento generado",
                        details={"response": result}
                    )

                logger.info(f"Firma iniciada exitosamente, document_id={document_id}, expires_in={expires_info}")
                
                return {
                    "document_id": document_id,  # filename from Legal Orchestrator
                    "document_url": document_url,
                    "signature_urls": [document_url] if document_url else [],  # signed_url as signature URL
                    "expires_at": expires_info,
                    "api_mode": "legal_orchestrator",
                    "status": "ready_for_signing"
                }
            
            elif response.status_code == 401:
                logger.error("API Key inválida para Legal Orchestrator")
                raise ExternalServiceError(
                    "API Key inválida para Legal Orchestrator",
                    details={"status_code": 401}
                )

            elif response.status_code == 422:
                logger.error(f"Error de validación en Legal Orchestrator: {response.text[:200]}")
                raise ExternalServiceError(
                    f"Error de validación en Legal Orchestrator: {response.text}",
                    details={"status_code": 422, "response": response.text}
                )

            else:
                logger.error(f"Error en Legal Orchestrator API: HTTP {response.status_code}")
                raise ExternalServiceError(
                    f"Error en Legal Orchestrator API: {response.status_code}",
                    details={"status_code": response.status_code, "response": response.text}
                )

    except httpx.TimeoutException:
        logger.error("Timeout en Legal Orchestrator API para start-signatures")
        raise ExternalServiceError("Timeout en Legal Orchestrator API para start-signatures")
    except httpx.RequestError as e:
        logger.error(f"Error de conexión con Legal Orchestrator: {str(e)}")
        raise ExternalServiceError(f"Error de conexión con Legal Orchestrator: {str(e)}")
    except Exception as e:
        if isinstance(e, ExternalServiceError):
            raise
        else:
            logger.error(f"Error inesperado en Legal Orchestrator start-signatures: {str(e)}")
            raise ExternalServiceError(f"Error inesperado en Legal Orchestrator start-signatures: {str(e)}")


def transform_to_start_signatures_request(document_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transforma document_data al formato EXACTO que requiere Legal Orchestrator /start-signatures.

    Args:
        document_data: Datos RAW del documento desde _fetch_document_basic_info()

    Returns:
        Dict en formato StartSignaturesRequest exacto para Legal Orchestrator
    """
    # Extraer campos exactos de la BD según los aliases de la consulta SQL
    type_acronym = document_data.get("document_type_acronym")
    type_name = document_data.get("document_type_name")
    reference = document_data.get("reference")
    content_raw = document_data.get("content", "")

    # Extraer HTML del contenido si es dict
    if isinstance(content_raw, dict) and 'html' in content_raw:
        content = content_raw['html']
    elif isinstance(content_raw, str):
        content = content_raw
    else:
        content = str(content_raw) if content_raw else ""

    # Convertir contenido a HTML
    html_content = convert_text_to_html(content)

    logger.debug(f"Transformando documento para start-signatures: acronym={type_acronym}, ref={reference}")

    # Validar que tenemos los datos requeridos de la BD - usar fallbacks si es necesario
    if not type_acronym:
        logger.warning(f"type_acronym no recibido, usando fallback 'DOC'")
        type_acronym = "DOC"
    if not type_name:
        logger.warning(f"type_name no recibido, usando fallback 'Documento'")
        type_name = "Documento"
    if not reference:
        logger.warning(f"reference no recibida, usando fallback")
        reference = f"Doc-{document_data.get('document_id', 'unknown')[:8]}"

    # Preparar document_id SIN guiones
    document_id_raw = document_data.get("document_id", "")
    document_id_clean = document_id_raw.replace("-", "") if document_id_raw else ""

    payload = {
        "document_id": document_id_clean,
        "document_content": {
            "html": html_content
        },
        "document_reference": reference,
        "document_type_acronym": type_acronym,
        "document_type_name": type_name,
        "municipality_logo_url": DEFAULT_LOGO_URL
    }

    return payload


def convert_text_to_html(text_content: str) -> str:
    """
    Convierte contenido de documento a HTML para Legal Orchestrator.
    Maneja formato TipTap JSON y texto plano.

    Args:
        text_content: Contenido del documento (puede ser TipTap JSON o texto plano)

    Returns:
        Contenido convertido a HTML limpio
    """
    # Asegurar que text_content es string
    if not text_content:
        return "<p>Contenido no disponible para previsualización</p>"

    # Convertir a string si no lo es
    if not isinstance(text_content, str):
        logger.debug(f"Contenido no es string: {type(text_content)}, convirtiendo...")
        text_content = str(text_content)

    if text_content.strip() == "":
        return "<p>Contenido no disponible para previsualización</p>"

    # Detectar si es JSON de TipTap
    if text_content.strip().startswith('{') and 'type' in text_content and 'doc' in text_content:
        try:
            import json

            # Parsear el JSON completo
            content_json = json.loads(text_content)

            # Extraer el contenido TipTap
            if 'tiptap' in content_json:
                # Usar la estructura TipTap limpia
                tiptap_content = content_json['tiptap']
                html_result = convert_tiptap_to_html(tiptap_content)
            elif 'html' in content_json:
                # Parsear el HTML interno que también puede ser JSON de TipTap
                html_data = content_json['html']
                if isinstance(html_data, str) and html_data.strip().startswith('{'):
                    # Es otro JSON de TipTap dentro del campo HTML
                    inner_json = json.loads(html_data)
                    html_result = convert_tiptap_to_html(inner_json)
                else:
                    html_result = str(html_data)
            else:
                # Tratar de parsear como TipTap directamente
                html_result = convert_tiptap_to_html(content_json)

            logger.debug(f"TipTap convertido a HTML")
            return html_result

        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"Error parseando TipTap JSON: {e}, usando fallback")
            # Fallback a texto plano
            return f"<p>{text_content}</p>"

    # Si ya parece ser HTML, devolverlo tal como está
    if text_content.strip().startswith('<') and '>' in text_content:
        return text_content

    # Convertir texto plano a HTML básico
    html_content = text_content.replace('\n', '<br>')

    # Envolver en párrafos si no tiene etiquetas HTML
    if '<' not in html_content:
        html_content = f"<p>{html_content}</p>"

    return html_content


def convert_tiptap_to_html(tiptap_json: dict) -> str:
    """
    Convierte estructura JSON de TipTap a HTML.
    
    Args:
        tiptap_json: Estructura JSON de TipTap
        
    Returns:
        HTML generado desde TipTap
    """
    try:
        if not isinstance(tiptap_json, dict) or 'content' not in tiptap_json:
            return "<p>Formato TipTap inválido</p>"
        
        html_parts = []
        
        for node in tiptap_json.get('content', []):
            html_parts.append(convert_tiptap_node_to_html(node))
        
        result = ''.join(html_parts)
        return result if result else "<p>Contenido vacío</p>"
        
    except Exception as e:
        logger.warning(f"Error convirtiendo TipTap: {e}")
        return "<p>Error procesando contenido</p>"


def convert_tiptap_node_to_html(node: dict) -> str:
    """
    Convierte un nodo individual de TipTap a HTML.
    
    Args:
        node: Nodo de TipTap
        
    Returns:
        HTML del nodo
    """
    node_type = node.get('type', '')
    
    if node_type == 'paragraph':
        # Extraer texto de los nodos hijos
        text_content = extract_text_from_tiptap_content(node.get('content', []))
        return f"<p>{text_content}</p>"
    
    elif node_type == 'heading':
        level = node.get('attrs', {}).get('level', 1)
        text_content = extract_text_from_tiptap_content(node.get('content', []))
        return f"<h{level}>{text_content}</h{level}>"
    
    elif node_type == 'text':
        return node.get('text', '')
    
    else:
        # Para otros tipos, extraer texto simple
        text_content = extract_text_from_tiptap_content(node.get('content', []))
        return f"<p>{text_content}</p>" if text_content else ""


def extract_text_from_tiptap_content(content_list: list) -> str:
    """
    Extrae texto plano de una lista de contenido TipTap.
    
    Args:
        content_list: Lista de nodos de contenido TipTap
        
    Returns:
        Texto concatenado
    """
    text_parts = []
    
    for item in content_list:
        if isinstance(item, dict):
            if item.get('type') == 'text':
                text_parts.append(item.get('text', ''))
            elif 'content' in item:
                # Recursivo para nodos anidados
                text_parts.append(extract_text_from_tiptap_content(item['content']))
        
    return ''.join(text_parts)


async def generate_mock_preview_pdf(document_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Genera un PDF de previsualización mock para testing.

    Args:
        document_data: Datos del documento

    Returns:
        Dict con información del PDF mock generado incluyendo pdf_content en bytes
    """
    document_id = document_data.get("document_id")

    logger.warning(f"MOCK: Generando preview PDF mock para documento {document_id}")

    # Simular tiempo de procesamiento
    await asyncio.sleep(0.1)

    # Generar un PDF mock simple (PDF vacío válido)
    mock_pdf_content = b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF'

    return {
        "success": True,
        "pdf_content": mock_pdf_content,
        "message": "PDF de previsualización MOCK generado (Legal Orchestrator no disponible)",
        "api_mode": "mock"
    }


async def call_legal_orchestrator_sign_document(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Llama a Legal Orchestrator para firmar un documento como firmante común.

    Recupera datos del documento y usuario desde BD y llama a Legal Orchestrator /sign-document
    para estampar la firma (sin official_document_number).

    Args:
        document_id: UUID del documento a firmar
        user_id: UUID del usuario firmante
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con el resultado de la operación:
        - success: bool
        - message: str
        - document_generate_id: str (nuevo UUID del PDF firmado)
        - api_response: dict (respuesta completa de Legal Orchestrator)

    Raises:
        ExternalServiceError: Si falla la API de Legal Orchestrator
        ValidationError: Si faltan datos o hay errores de validación
    """
    logger.info(f"Iniciando firma de documento común, doc={document_id[:8]}, user={user_id[:8]}")
    
    # Obtener configuración de API
    api_config = get_external_api_config()
    if not api_config or not api_config.base_url:
        return await _mock_sign_document_response(document_id, user_id)
    
    base_url = api_config.base_url
    api_key = os.getenv('LEGAL_ORCHESTRATOR_API_KEY')
    
    try:
        # Recuperar datos necesarios desde BD
        signing_data = await _fetch_signing_data(document_id, user_id, schema_name=schema_name)
        logger.debug(f"Datos de BD recuperados para firma")

        # Transformar datos a formato Legal Orchestrator
        request_payload = _transform_to_sign_document_request(signing_data, is_numerator=False)

        # Llamar a Legal Orchestrator con retry logic para error 404
        logger.debug(f"Llamando a Legal Orchestrator: {base_url}/sign-document")

        # Retry logic específico para error 404 (archivo no encontrado en to-sign)
        max_retries = 3
        retry_delay = 2  # segundos
        last_error = None

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.debug(f"Reintentando (intento {attempt}/{max_retries-1}), esperando {retry_delay}s...")
                    await asyncio.sleep(retry_delay)

                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        f"{base_url}/sign-document",
                        json=request_payload,
                        headers={
                            "Content-Type": "application/json",
                            "X-API-Key": api_key,
                            "X-Correlation-ID": get_correlation_id(),
                            "User-Agent": "GDI-Backend-SignDocument/1.0"
                        }
                    )

                logger.debug(f"Respuesta Legal Orchestrator: HTTP {response.status_code}")

                if response.status_code == 200:
                    result = response.json()

                    if result.get("status") == "success":
                        logger.info(f"Documento firmado exitosamente")

                        return {
                            "success": True,
                            "message": "Documento firmado exitosamente por Legal Orchestrator",
                            "document_generate_id": signing_data["document_generate_id"],
                            "api_response": result,
                            "api_mode": "real"
                        }
                    else:
                        error_msg = result.get("message", "Error desconocido en Legal Orchestrator")
                        logger.error(f"Error en Legal Orchestrator: {error_msg}")
                        raise ExternalServiceError(f"Legal Orchestrator error: {error_msg}")

                elif response.status_code == 404:
                    # Error 404 específico - might be timing issue, retry
                    error_text = response.text
                    logger.warning(f"HTTP 404 en intento {attempt + 1}/{max_retries}, archivo: {request_payload.get('document_filename', 'NO_FILENAME')}")

                    if attempt < max_retries - 1:  # Si no es el último intento
                        last_error = ExternalServiceError(f"Legal Orchestrator HTTP 404: {error_text}")
                        continue  # Reintentar
                    else:
                        # Último intento fallido
                        logger.error(f"HTTP 404 después de {max_retries} intentos: {error_text}")
                        raise ExternalServiceError(f"Legal Orchestrator HTTP 404 (después de {max_retries} intentos): {error_text}")

                else:
                    # Otros códigos de error HTTP
                    logger.error(f"HTTP {response.status_code} en Legal Orchestrator")
                    raise ExternalServiceError(f"Legal Orchestrator HTTP {response.status_code}: {response.text}")

            except httpx.TimeoutException:
                logger.warning(f"Timeout en intento {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    last_error = ExternalServiceError("Timeout en Legal Orchestrator API")
                    continue
                else:
                    raise ExternalServiceError("Timeout en Legal Orchestrator API")
            except httpx.RequestError as e:
                logger.warning(f"Error de conexión en intento {attempt + 1}/{max_retries}: {str(e)}")
                if attempt < max_retries - 1:
                    last_error = ExternalServiceError(f"Error de conexión con Legal Orchestrator: {str(e)}")
                    continue
                else:
                    raise ExternalServiceError(f"Error de conexión con Legal Orchestrator: {str(e)}")
            except ExternalServiceError:
                # Re-raise ExternalServiceError sin wrapping
                raise
            except Exception as e:
                logger.warning(f"Error inesperado en intento {attempt + 1}/{max_retries}: {str(e)}")
                if attempt < max_retries - 1:
                    last_error = ExternalServiceError(f"Error inesperado en firma: {str(e)}")
                    continue
                else:
                    raise ExternalServiceError(f"Error inesperado en firma: {str(e)}")

        # Si llegamos aquí, todos los intentos fallaron
        if last_error:
            raise last_error
        else:
            raise ExternalServiceError("Todos los intentos de firma fallaron")

    except Exception as e:
        if isinstance(e, (ExternalServiceError, ValidationError)):
            raise
        logger.error(f"Error inesperado en firma: {str(e)}")
        raise ExternalServiceError(f"Error inesperado en firma: {str(e)}")


async def _fetch_signing_data(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Recupera todos los datos necesarios para firma desde BD.

    Hace JOIN entre document_draft, users, city_seals y departments
    para obtener toda la información que necesita Legal Orchestrator.
    """
    from database import get_db_connection
    from services.shared.settings_utils import get_city_from_settings

    logger.debug(f"Recuperando datos de BD para firma...")

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Query completo con todos los JOINs necesarios
            query = """
                SELECT
                    d.id as document_id,
                    d.id as document_generate_id,
                    d.reference,
                    d.status,
                    u.id as user_id,
                    u.full_name as signer_full_name,
                    cs.name as signer_seal,
                    dep.name as signer_department
                FROM document_draft d
                INNER JOIN document_signers ds ON d.id = ds.document_id
                INNER JOIN users u ON ds.user_id = u.id
                LEFT JOIN user_seals us ON u.id = us.user_id
                LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
                LEFT JOIN sectors s ON u.sector_id = s.id
                LEFT JOIN departments dep ON s.department_id = dep.id
                WHERE d.id = %s AND u.id = %s
            """

            cursor.execute(query, (document_id, user_id))
            result = cursor.fetchone()

            if not result:
                raise ValidationError(f"No se encontró documento {document_id} con firmante {user_id}")

            # Obtener city desde settings del tenant
            city = get_city_from_settings(cursor=cursor)

            signing_data = {
                "document_id": result["document_id"],
                "document_generate_id": result["document_generate_id"],
                "reference": result["reference"],
                "status": result["status"],
                "signer_full_name": result["signer_full_name"],
                "signer_seal": result["signer_seal"] or "Sin sello asignado",
                "signer_department": result["signer_department"] or "Sin departamento asignado",
                "city": city
            }

            logger.debug(f"Datos de BD recuperados exitosamente")
            return signing_data


def _transform_to_sign_document_request(signing_data: Dict[str, Any], is_numerator: bool = False, official_number: str = None) -> Dict[str, Any]:
    """
    Transforma datos de BD al formato requerido por Legal Orchestrator /sign-document.

    Args:
        signing_data: Datos desde BD
        is_numerator: Si es True, incluye official_document_number, si es False lo pone null
        official_number: Número oficial a usar (solo para numeradores)
    """
    # Limpiar document_generate_id para usar como filename
    document_filename = signing_data["document_generate_id"]

    # Si document_generate_id es null, usar document_id como fallback
    if not document_filename:
        document_filename = signing_data["document_id"]
        logger.debug(f"document_generate_id es null, usando document_id como fallback")

    # Quitar guiones del UUID para Legal Orchestrator
    if document_filename and "-" in document_filename:
        document_filename = document_filename.replace("-", "")

    # Validar que document_filename no sea null (último recurso)
    if not document_filename:
        document_filename = f"document_{signing_data['document_id'][:8]}"
        logger.debug(f"Usando filename fallback: {document_filename}")

    # Obtener city desde signing_data o usar default
    city = signing_data.get("city", "LATAM")

    payload = {
        "document_filename": document_filename,
        "signer_full_name": signing_data["signer_full_name"],
        "signer_seal": signing_data["signer_seal"],
        "signer_department": signing_data["signer_department"],
        "signer_municipality": "Municipalidad Del Futuro",  # Hardcodeado
        "official_document_number": official_number if is_numerator else None,
        "city_name": city
    }

    logger.debug(f"Payload transformado para firma: filename={document_filename}, is_numerator={is_numerator}")

    return payload


async def _mock_sign_document_response(document_id: str, user_id: str) -> Dict[str, Any]:
    """
    Respuesta mock cuando Legal Orchestrator no está configurado.
    """
    logger.warning(f"MOCK: Simulando firma de documento {document_id}")

    # Simular tiempo de procesamiento
    await asyncio.sleep(2.0)

    # En modo mock, obtener el document_generate_id actual de la BD
    try:
        # NOTA: schema_name no está disponible aquí - esto es un problema potencial
        # Por ahora usamos document_id como fallback
        current_generate_id = document_id
    except Exception:
        # Si falla, usar el document_id como fallback
        current_generate_id = document_id
    
    return {
        "success": True,
        "message": "Documento firmado exitosamente (MOCK)",
        "document_generate_id": current_generate_id,  # Mantener el mismo ID
        "api_response": {
            "status": "success",
            "message": "Mock signature applied",
            "data": {"filename": f"{current_generate_id}.pdf"}
        },
        "api_mode": "mock"
    }


async def call_legal_orchestrator_sign_document_numerator(document_id: str, user_id: str, official_number: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Llama a Legal Orchestrator para firmar un documento como numerador.

    Recupera datos del documento y usuario desde BD y llama a Legal Orchestrator /sign-document
    para estampar la firma CON official_document_number (numerador).

    Args:
        document_id: UUID del documento a firmar
        user_id: UUID del usuario numerador
        official_number: Número oficial generado (ej: IF-2025-000000157-MT-DGOBR)
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con el resultado de la operación:
        - success: bool
        - message: str
        - document_generate_id: str (mismo UUID del PDF)
        - api_response: dict (respuesta completa de Legal Orchestrator)

    Raises:
        ExternalServiceError: Si falla la API de Legal Orchestrator
        ValidationError: Si faltan datos o hay errores de validación
    """
    logger.info(f"Iniciando firma de numerador, doc={document_id[:8]}, user={user_id[:8]}, official_number={official_number}")
    
    # Obtener configuración de API
    api_config = get_external_api_config()
    if not api_config or not api_config.base_url:
        return await _mock_sign_document_numerator_response(document_id, user_id, official_number)
    
    base_url = api_config.base_url
    api_key = os.getenv('LEGAL_ORCHESTRATOR_API_KEY')
    
    try:
        # Recuperar datos necesarios desde BD
        signing_data = await _fetch_signing_data(document_id, user_id, schema_name=schema_name)
        logger.debug(f"Datos de BD recuperados para firma de numerador")

        # Transformar datos a formato Legal Orchestrator (CON número oficial)
        request_payload = _transform_to_sign_document_request(signing_data, is_numerator=True, official_number=official_number)

        logger.debug(f"Llamando a Legal Orchestrator: {base_url}/sign-document (numerador)")

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{base_url}/sign-document",
                json=request_payload,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": api_key or "",
                    "X-Correlation-ID": get_correlation_id(),
                    "User-Agent": "GDI-Backend-SignDocument-Numerator/1.0"
                }
            )

            logger.debug(f"Respuesta Legal Orchestrator: HTTP {response.status_code}")

            if response.status_code == 200:
                result = response.json()

                if result.get("status") == "success":
                    logger.info(f"Documento firmado exitosamente por numerador con número {official_number}")

                    return {
                        "success": True,
                        "message": f"Documento firmado exitosamente por numerador con número {official_number}",
                        "document_generate_id": signing_data["document_generate_id"],
                        "official_number": official_number,
                        "api_response": result,
                        "api_mode": "real"
                    }
                else:
                    error_msg = result.get("message", "Error desconocido en Legal Orchestrator")
                    logger.error(f"Error en Legal Orchestrator: {error_msg}")
                    raise ExternalServiceError(f"Legal Orchestrator error: {error_msg}")
            else:
                logger.error(f"HTTP {response.status_code} en Legal Orchestrator (numerador)")
                raise ExternalServiceError(
                    f"Legal Orchestrator HTTP {response.status_code}: {response.text}"
                )

    except httpx.TimeoutException:
        logger.error(f"Timeout en Legal Orchestrator")
        raise ExternalServiceError("Timeout en Legal Orchestrator API")
    except httpx.RequestError as e:
        logger.error(f"Error de conexión: {str(e)}")
        raise ExternalServiceError(f"Error de conexión con Legal Orchestrator: {str(e)}")
    except Exception as e:
        if isinstance(e, (ExternalServiceError, ValidationError)):
            raise
        logger.error(f"Error inesperado en firma de numerador: {str(e)}")
        raise ExternalServiceError(f"Error inesperado en firma de numerador: {str(e)}")


async def _mock_sign_document_numerator_response(document_id: str, user_id: str, official_number: str) -> Dict[str, Any]:
    """
    Respuesta mock cuando Legal Orchestrator no está configurado (numerador).
    """
    logger.warning(f"MOCK: Simulando firma de numerador para documento {document_id} con número {official_number}")

    # Simular tiempo de procesamiento
    await asyncio.sleep(2.0)

    # En modo mock, usar el document_id como fallback
    current_generate_id = document_id

    return {
        "success": True,
        "message": f"Documento firmado exitosamente por numerador con número {official_number} (MOCK)",
        "document_generate_id": current_generate_id,  # Mantener el mismo ID
        "official_number": official_number,
        "api_response": {
            "status": "success",
            "message": "Mock numerator signature applied",
            "data": {"filename": f"{current_generate_id}.pdf", "official_number": official_number}
        },
        "api_mode": "mock"
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

    r2_client = get_tenant_r2_client(schema_name=schema_name)

    if document_status == "sent_to_sign":
        # Documentos en proceso de firma: bucket tosign
        # El filename debe ser el UUID sin guiones + .pdf
        filename = document_generate_id.replace('-', '') + '.pdf'
        logger.debug(f"Obteniendo URL de tosign: {filename}")
        return await run_in_threadpool(r2_client.get_tosign_url, filename)

    elif document_status == "signed":
        # Documentos firmados: bucket oficial
        # Necesitamos buscar el official_number en la BD
        from database import execute_query

        logger.debug(f"Buscando official_number en BD...")

        query = """
            SELECT official_number
            FROM official_documents
            WHERE id = %s
        """

        result = execute_query(query, (document_id,), fetch_one=True, schema_name=schema_name)

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
    signers: List[Dict[str, Any]]
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