
from typing import Dict, Any, List, Optional
from shared.exceptions import ExternalServiceError, ValidationError
from shared.context import get_correlation_id
from fastapi.concurrency import run_in_threadpool
import uuid
import os
import asyncio
import httpx

from config.constants import DEFAULT_LOGO_URL, MAX_SIGNABLE_PDF_SIZE
from shared.logging import get_logger
from services.shared.micro_retry import post_micro_with_coldstart_retry

logger = get_logger(__name__)

async def generate_final_document_pdf(
    document_id: str,
    document_data: Dict[str, Any],
    signers: List[Dict[str, Any]],
    *,
    schema_name: str,
    embedded_files: Optional[List[tuple]] = None,
) -> Dict[str, Any]:
    embedded_files = embedded_files or []

    if 'document_id' not in document_data:
        document_data['document_id'] = document_id

    logger.info(f"Generando PDF para documento {document_id[:8]}, tipo={document_data.get('type_name', 'N/A')}, firmantes={len(signers)}")

    content = document_data.get('content')
    is_imported = content is None or content == '' or content == {}

    from services.storage.cloudflare import get_tenant_r2_client
    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    filename = document_id.replace('-', '') + '.pdf'

    try:
        if is_imported:
            exists = await run_in_threadpool(r2_client.exists_tosign, filename)

            if not exists:
                raise ExternalServiceError(
                    f"PDF de documento importado no encontrado en R2: {filename}. "
                    "El documento debe tener un PDF subido antes de iniciar firma."
                )

            logger.debug(f"PDF importado verificado en R2: {filename}")

            document_url = await run_in_threadpool(r2_client.get_tosign_url, filename)

            if not document_url:
                raise ExternalServiceError("No se pudo generar URL firmada para el PDF importado")

            logger.debug(f"URL firmada generada para PDF importado (expira en 600s)")

            return {
                "status": "success",
                "document_generate_id": document_id,
                "document_url": document_url,
                "api_mode": "imported_r2",
                "upload_info": {"filename": filename, "already_existed": True},
                "file_size": 0
            }

        else:
            _content_raw = document_data.get('content')
            if isinstance(_content_raw, dict) and 'html' in _content_raw:
                from services.documents.lifecycle.images import inline_document_images_as_base64
                _content_raw['html'] = await inline_document_images_as_base64(
                    _content_raw['html'], document_id, schema_name=schema_name
                )
            elif isinstance(_content_raw, str) and _content_raw:
                from services.documents.lifecycle.images import inline_document_images_as_base64
                document_data['content'] = await inline_document_images_as_base64(
                    _content_raw, document_id, schema_name=schema_name
                )

            _base_type = (
                document_data.get('base_type')
                or document_data.get('type_acronym')
                or document_data.get('document_type_acronym')
                or ''
            ).upper()

            if _base_type in ('NOTA', 'MEMO'):
                logger.debug(f"Generando documento base_type={_base_type} con recipients")
                from services.shared.pdfcomposer_api import call_pdfcomposer_note_final

                if _base_type == 'NOTA':
                    from services.notes.recipients import format_recipients_for_pdf
                    recipients = await format_recipients_for_pdf(document_id, schema_name=schema_name)
                else:
                    from services.memos.recipients import format_memo_recipients_for_pdf
                    recipients = await format_memo_recipients_for_pdf(document_id, schema_name=schema_name)

                if embedded_files:
                    raise ValidationError(
                        f"El documento es de tipo {_base_type} y tiene {len(embedded_files)} "
                        "adjunto(s) embebido(s) cargado(s), pero los documentos NOTA/MEMO no "
                        "soportan adjuntos embebidos en esta versión. Quite los adjuntos antes "
                        "de firmar, o contacte a soporte si el tipo de documento no debería "
                        "tener habilitado 'Permite adjuntar archivos embebidos'."
                    )
                pdf_bytes = await call_pdfcomposer_note_final(
                    document_data,
                    para=recipients['para'],
                    cc=recipients.get('cc'),
                    schema_name=schema_name,
                )
            else:
                pdf_bytes = await call_pdfcomposer_generate_pdf(
                    document_data, signers, schema_name=schema_name, embedded_files=embedded_files
                )

            logger.debug(f"PDF generado: {len(pdf_bytes)} bytes")

            upload_result = await run_in_threadpool(r2_client.upload_tosign, pdf_bytes, filename)
            logger.debug(f"PDF subido a R2: {filename}")

            document_url = await run_in_threadpool(r2_client.get_tosign_url, filename)

            if not document_url:
                raise ExternalServiceError("No se pudo generar URL firmada para el PDF")

            logger.debug(f"URL firmada generada (expira en 600s)")

            return {
                "status": "success",
                "document_generate_id": document_id,
                "document_url": document_url,
                "api_mode": "direct_r2",
                "upload_info": upload_result,
                "file_size": len(pdf_bytes)
            }

    except ValidationError:
        raise
    except ExternalServiceError:
        raise
    except Exception as e:
        logger.error(f"Error generando PDF: {type(e).__name__} - {str(e)}")
        raise ExternalServiceError(f"Error generando PDF: {str(e)}")

async def call_signature_stamping_api(document_id: str, user_id: str, current_pdf_id: str = None) -> Dict[str, Any]:

    signature_api_url = os.getenv('SIGNATURE_STAMPING_API_URL')
    
    if not signature_api_url:
        logger.warning(f"MODO MOCK: Simulando estampado de firma para documento {document_id}")

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
            
            async with httpx.AsyncClient(timeout=90.0) as client:
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

    numerator_api_url = os.getenv('NUMERATOR_SIGNING_API_URL')
    
    if not numerator_api_url:
        logger.warning(f"MODO MOCK: Simulando firma de numerador para documento {document_id} con número {official_number}")

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
            
            async with httpx.AsyncClient(timeout=120.0) as client:
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
    validation_api_url = os.getenv('DOCUMENT_VALIDATION_API_URL')
    
    if not validation_api_url:
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
    from services.storage.cloudflare import get_tenant_r2_client

    logger.debug(f"Obteniendo URL de PDF: doc={document_id}, status={document_status}")

    r2_client = await get_tenant_r2_client(schema_name=schema_name)

    if document_status == "sent_to_sign":
        filename = document_generate_id.replace('-', '') + '.pdf'
        logger.debug(f"Obteniendo URL de tosign: {filename}")
        return await run_in_threadpool(r2_client.get_tosign_url, filename)

    elif document_status == "signed":
        from database import fetch_one

        logger.debug(f"Buscando official_number en BD...")

        query = """
            SELECT official_number, pdf_location
            FROM official_documents
            WHERE id = $1
              AND signed_at IS NOT NULL
        """

        result = await fetch_one(query, document_id, schema_name=schema_name)

        if result and result.get("official_number"):
            official_number = result["official_number"]
            logger.debug(f"Official number encontrado, obteniendo URL oficial")
            return await run_in_threadpool(
                r2_client.get_oficial_url, official_number,
                result.get("pdf_location") or "oficial",
            )
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
    schema_name: str,
    embedded_files: Optional[List[tuple]] = None,
) -> bytes:
    import json

    embedded_files = embedded_files or []

    document_id = document_data.get('document_id')
    if not document_id:
        raise ValidationError("document_id es requerido para generar PDF")


    type_acronym = document_data.get('type_acronym') or document_data.get('document_type_acronym')
    type_name = document_data.get('type_name') or document_data.get('document_type_name')
    reference = document_data.get('reference')
    content_raw = document_data.get('content', '')

    if not type_acronym:
        raise ValidationError("type_acronym es requerido para generar PDF")
    if not type_name:
        raise ValidationError("type_name es requerido para generar PDF")
    if not reference:
        raise ValidationError("reference es requerido para generar PDF")

    if isinstance(content_raw, dict) and 'html' in content_raw:
        content_html = content_raw['html']
    elif isinstance(content_raw, str):
        content_html = content_raw
    else:
        content_html = str(content_raw) if content_raw else ""

    text_json = json.dumps({"html": content_html})

    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise RuntimeError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise RuntimeError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = document_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": type_acronym,
        "TypeDocument": type_name,
        "Reference": reference,
        "Text": text_json
    }

    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = (await get_tenant_settings(schema_name)).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"Generando PDF con PDFComposer: {reference}, tipo={type_name}")

    timeout = httpx.Timeout(90.0)
    headers = {
        "X-API-Key": pdfcomposer_api_key,
        "X-Correlation-ID": get_correlation_id()
    }

    files_payload = [
        ("embedded_files", (file_name, file_bytes, "application/octet-stream"))
        for file_name, file_bytes in embedded_files
    ] or None

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await post_micro_with_coldstart_retry(
                client,
                f"{pdfcomposer_url}/generate-pdf/",
                data=pdfcomposer_data,
                files=files_payload,
                headers=headers,
                log_label="PDFComposer",
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
                httpx.RemoteProtocolError, httpx.PoolTimeout) as e:
            logger.error(f"PDFComposer inalcanzable tras reintentos de cold-start: {type(e).__name__}: {e}")
            raise ExternalServiceError(f"PDFComposer inalcanzable (cold-start): {str(e)}")
        except httpx.TimeoutException:
            logger.warning("Timeout en PDFComposer (90s excedido)")
            raise ExternalServiceError("PDFComposer timeout después de 90 segundos")
        except httpx.RequestError as e:
            logger.warning(f"Error de conexión con PDFComposer: {str(e)}")
            raise ExternalServiceError(f"Error de conexión con PDFComposer: {str(e)}")

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP Error {e.response.status_code} en PDFComposer")
            raise ExternalServiceError(
                f"PDFComposer error: HTTP {e.response.status_code}",
                details={"response": e.response.text[:500]}
            )

        try:
            pdf_bytes = await response.aread()
            pdf_size = len(pdf_bytes)

            logger.info(f"PDF generado exitosamente: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

            if pdf_size == 0:
                raise ExternalServiceError("PDFComposer retornó PDF vacío")

            if not pdf_bytes.startswith(b'%PDF'):
                logger.warning(f"Respuesta de PDFComposer no parece ser PDF válido")

            if pdf_size > MAX_SIGNABLE_PDF_SIZE:
                raise ExternalServiceError(
                    f"PDF excede tamaño máximo ({pdf_size/1024/1024:.2f}MB > {MAX_SIGNABLE_PDF_SIZE/1024/1024:.0f}MB)"
                )
        except ExternalServiceError:
            raise
        except httpx.HTTPError as e:
            logger.error(f"Error leyendo respuesta de PDFComposer post-200: {type(e).__name__}: {e}")
            raise ExternalServiceError(f"Error leyendo respuesta de PDFComposer: {str(e)}")

        return pdf_bytes