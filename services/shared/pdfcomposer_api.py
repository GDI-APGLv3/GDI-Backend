"""
Cliente para integración directa con PDFComposer API.

Phase 5: Eliminación de Legal Orchestrator para generación de carátulas y pases.
Phase 6: Nuevos endpoints /note-preview/ y /note/ para documentos tipo NOTA.

Llama directamente a PDFComposer para generar PDFs con templates especiales.
"""

from typing import Dict, Any, Optional
import os
import httpx
import json
from shared.logging import get_logger
from shared.exceptions import ExternalServiceError, ValidationError
from config.constants import DEFAULT_LOGO_URL

logger = get_logger(__name__)


async def call_pdfcomposer_create_case(cover_data: Dict[str, Any], *, schema_name: str) -> bytes:
    """
    Llama a PDFComposer /create-case/ para generar PDF de carátula de expediente.

    Usa template especial caratula.html con campos de expediente.

    Args:
        cover_data: Dict con datos del expediente:
            - municipality_logo_url: URL del logo
            - document_type_acronym: CAEX
            - document_type_name: Nombre del tipo de documento
            - document_reference: Referencia del documento
            - case_number: Número del expediente
            - case_type_acronym: Acrónimo del tipo de expediente
            - case_type_name: Nombre del tipo de expediente
            - case_motive: Motivo del expediente
            - initiating_department: Departamento iniciador
            - case_creator: Nombre del creador del caso
            - signer_full_name: Nombre del firmante
            - signer_municipality: Municipalidad
            - official_document_number: Número oficial
            - city_name: Ciudad

    Returns:
        bytes: PDF generado por PDFComposer

    Raises:
        ValidationError: Si faltan campos requeridos
        ExternalServiceError: Si PDFComposer falla o no responde
    """
    logger.info("Llamando a PDFComposer /create-case/...")

    # Validar campos requeridos
    required_fields = [
        "document_type_acronym", "document_type_name", "document_reference",
        "case_number", "case_type_acronym", "case_type_name", "case_motive",
        "initiating_department", "case_creator", "signer_full_name",
        "signer_municipality", "city_name"
    ]

    missing_fields = [field for field in required_fields if not cover_data.get(field)]
    if missing_fields:
        raise ValidationError(f"Campos requeridos faltantes para PDFComposer: {', '.join(missing_fields)}")

    # Obtener credenciales desde .env
    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = cover_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    # DEBUG: Verificar valor de case_motive ANTES de crear payload
    logger.debug("Valor de case_motive en cover_data:")
    logger.debug(f"case_motive: '{cover_data.get('case_motive')}'")
    logger.debug(f"case_motive type: {type(cover_data.get('case_motive'))}")
    logger.debug(f"case_motive is None: {cover_data.get('case_motive') is None}")
    logger.debug(f"case_motive is empty string: {cover_data.get('case_motive') == ''}")

    # Preparar payload para PDFComposer (multipart/form-data)
    # PDFComposer /create-case/ SOLO acepta estos 10 campos (segun spec del usuario)
    # Firmante, document_number y city NO son parte de este endpoint
    # Esos se agregan en el paso de firma con Notary
    pdfcomposer_data = {
        # Logo
        "urlLogo": logo_url,
        # Documento
        "NameAcronyType": cover_data["document_type_acronym"],
        "document_type": cover_data["document_type_name"],
        "reference": cover_data["document_reference"],
        # Expediente
        "case_number": cover_data["case_number"],
        "acrony_case_type": cover_data["case_type_acronym"],
        "case_type": cover_data["case_type_name"],
        "case_motive": cover_data["case_motive"],
        # Departamento y creador
        "initiating_division": cover_data["initiating_department"],
        "creator": cover_data["case_creator"]
    }

    # Inyectar frase_anual desde settings del tenant
    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = get_tenant_settings(schema_name).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/create-case/")
    logger.info(f"Case: {cover_data['case_number']}")
    logger.info(f"Signer: {cover_data['signer_full_name']}")

    # DEBUG: Mostrar TODO el payload que se enviará
    logger.debug("Payload completo a enviar:")
    for key, value in pdfcomposer_data.items():
        logger.debug(f"{key}: '{value}' (type: {type(value).__name__})")

    # Llamar a PDFComposer con retry
    timeout = httpx.Timeout(90.0)  # Timeout largo para generación de PDF
    headers = {"X-API-Key": pdfcomposer_api_key}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):  # 1 reintento
            try:
                logger.info(f"Intento {attempt + 1}/2")

                response = await client.post(
                    f"{pdfcomposer_url}/create-case/",
                    data=pdfcomposer_data,
                    headers=headers,
                    follow_redirects=True
                )

                response.raise_for_status()

                # Leer contenido directo en memoria
                pdf_bytes = await response.aread()
                pdf_size = len(pdf_bytes)

                logger.info("PDF generado exitosamente")
                logger.info(f"Size: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

                # Validar que recibimos un PDF válido
                if pdf_size == 0:
                    raise ExternalServiceError("PDFComposer retorno PDF vacio")

                # Validar magic bytes
                if not pdf_bytes.startswith(b'%PDF'):
                    logger.warning("Respuesta no parece ser PDF valido")

                # Validar tamaño máximo (10MB)
                MAX_PDF_SIZE = 10 * 1024 * 1024  # 10MB
                if pdf_size > MAX_PDF_SIZE:
                    raise ExternalServiceError(f"PDF excede tamano maximo ({pdf_size/1024/1024:.2f}MB > 10MB)")

                return pdf_bytes

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP Error: {e.response.status_code}")
                logger.error(f"Response: {e.response.text[:200]}")

                if attempt == 1:  # Último intento
                    raise ExternalServiceError(
                        f"PDFComposer error: HTTP {e.response.status_code}",
                        details={"response": e.response.text[:500]}
                    )

            except httpx.TimeoutException:
                logger.error("Timeout (90s excedido)")
                if attempt == 1:
                    raise ExternalServiceError("PDFComposer timeout despues de 90 segundos")

            except httpx.RequestError as e:
                logger.error(f"Request Error: {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error de conexion con PDFComposer: {str(e)}")

            except ExternalServiceError:
                # Re-raise ExternalServiceError sin modificar
                if attempt == 1:
                    raise

            except Exception as e:
                logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error inesperado en PDFComposer: {str(e)}")

            # Si no es el último intento, esperar antes de retry
            if attempt == 0:
                import asyncio
                logger.info("Esperando 1s antes de reintentar...")
                await asyncio.sleep(1)

    # No debería llegar aquí
    raise ExternalServiceError("PDFComposer: error desconocido despues de reintentos")


async def call_pdfcomposer_create_transfer(transfer_data: Dict[str, Any], *, schema_name: str) -> bytes:
    """
    Llama a PDFComposer /move/ para generar PDF de pase/transferencia.

    Usa template especial movimiento.html con campos de transferencia.

    Phase 6: Para implementar en el futuro.

    Args:
        transfer_data: Dict con datos del pase:
            - municipality_logo_url: URL del logo
            - document_type_acronym: PV
            - document_type_name: Nombre del tipo de documento
            - document_reference: Referencia del documento
            - movement_type: Tipo de movimiento
            - requesting_area: Area requiriente
            - receiving_area: Area receptora
            - movement_reason: Motivo del movimiento
            - signer_full_name: Nombre del firmante
            - signer_municipality: Municipalidad
            - official_document_number: Número oficial
            - city_name: Ciudad

    Returns:
        bytes: PDF generado por PDFComposer

    Raises:
        ValidationError: Si faltan campos requeridos
        ExternalServiceError: Si PDFComposer falla o no responde
    """
    logger.info("Llamando a PDFComposer /move/...")

    # Validar campos requeridos SOLO para PDFComposer /move/
    # Nota: signer_*, official_document_number, city_name son para Notary, no para PDFComposer
    required_fields = [
        "document_type_acronym", "document_type_name", "document_reference",
        "movement_type", "requesting_area", "receiving_area", "movement_reason"
    ]

    missing_fields = [field for field in required_fields if not transfer_data.get(field)]
    if missing_fields:
        raise ValidationError(f"Campos requeridos faltantes para PDFComposer: {', '.join(missing_fields)}")

    # Obtener credenciales desde .env
    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = transfer_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    # Preparar payload para PDFComposer (multipart/form-data)
    # PDFComposer /move/ SOLO acepta estos 8 campos (según spec del usuario)
    # Firmante, document_number y city NO son parte de este endpoint
    # Esos se agregan en el paso de firma con Notary
    pdfcomposer_data = {
        # Logo
        "urlLogo": logo_url,
        # Documento
        "NameAcronyType": transfer_data["document_type_acronym"],
        "document_type": transfer_data["document_type_name"],
        "reference": transfer_data["document_reference"],
        # Pase
        "tipo_movimiento": transfer_data["movement_type"],
        "area_requiriente": transfer_data["requesting_area"],
        "area_receptora": transfer_data["receiving_area"],
        "motivo": transfer_data["movement_reason"]
    }

    # Inyectar frase_anual desde settings del tenant
    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = get_tenant_settings(schema_name).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/move/")
    logger.info(f"Movement: {transfer_data['movement_type']}")

    # Llamar a PDFComposer con retry (mismo patrón que create_case)
    timeout = httpx.Timeout(90.0)
    headers = {"X-API-Key": pdfcomposer_api_key}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                logger.info(f"Intento {attempt + 1}/2")

                response = await client.post(
                    f"{pdfcomposer_url}/move/",
                    data=pdfcomposer_data,
                    headers=headers,
                    follow_redirects=True
                )

                response.raise_for_status()

                pdf_bytes = await response.aread()
                pdf_size = len(pdf_bytes)

                logger.info("PDF generado exitosamente")
                logger.info(f"Size: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

                # Validaciones
                if pdf_size == 0:
                    raise ExternalServiceError("PDFComposer retorno PDF vacio")

                if not pdf_bytes.startswith(b'%PDF'):
                    logger.warning("Respuesta no parece ser PDF valido")

                MAX_PDF_SIZE = 10 * 1024 * 1024
                if pdf_size > MAX_PDF_SIZE:
                    raise ExternalServiceError(f"PDF excede tamano maximo ({pdf_size/1024/1024:.2f}MB > 10MB)")

                return pdf_bytes

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP Error: {e.response.status_code}")
                if attempt == 1:
                    raise ExternalServiceError(f"PDFComposer error: HTTP {e.response.status_code}")

            except httpx.TimeoutException:
                logger.error("Timeout (90s excedido)")
                if attempt == 1:
                    raise ExternalServiceError("PDFComposer timeout despues de 90 segundos")

            except httpx.RequestError as e:
                logger.error(f"Request Error: {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error de conexion con PDFComposer: {str(e)}")

            except ExternalServiceError:
                if attempt == 1:
                    raise

            except Exception as e:
                logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error inesperado en PDFComposer: {str(e)}")

            if attempt == 0:
                import asyncio
                logger.info("Esperando 1s antes de reintentar...")
                await asyncio.sleep(1)

    raise ExternalServiceError("PDFComposer: error desconocido despues de reintentos")


async def call_pdfcomposer_preview_pdf(document_data: Dict[str, Any], *, schema_name: str) -> bytes:
    """
    Llama a PDFComposer /preview-pdf/ para generar PDF de previsualización con marca de agua.

    Elimina la dependencia de Legal Orchestrator llamando directamente a PDFComposer.

    Args:
        document_data: Dict con datos del documento:
            - document_type_acronym: Acrónimo del tipo de documento (ej. "OF", "RES")
            - document_type_name: Nombre del tipo de documento (ej. "Oficio", "Resolución")
            - reference: Referencia del documento
            - content: Contenido HTML del documento
            - municipality_logo_url: (opcional) URL del logo

    Returns:
        bytes: PDF generado por PDFComposer con marca de agua de previsualización

    Raises:
        ValidationError: Si faltan campos requeridos
        ExternalServiceError: Si PDFComposer falla o no responde
    """
    logger.info("Llamando a PDFComposer /preview-pdf/...")

    # Validar campos requeridos
    required_fields = ["document_type_acronym", "document_type_name", "reference", "content"]
    missing_fields = [field for field in required_fields if not document_data.get(field)]
    if missing_fields:
        raise ValidationError(f"Campos requeridos faltantes para PDFComposer preview: {', '.join(missing_fields)}")

    # Obtener credenciales desde .env
    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = document_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    # Extraer contenido HTML del campo content
    content_raw = document_data.get("content", "")
    if isinstance(content_raw, dict) and 'html' in content_raw:
        html_content = content_raw['html']
    elif isinstance(content_raw, str):
        html_content = content_raw
    else:
        html_content = str(content_raw) if content_raw else ""

    # Preparar payload para PDFComposer /preview-pdf/
    # PDFComposer espera Text como JSON string con formato: {"html": "content"}
    # Esto se parsea en main.py línea 125 con json.loads(Text)
    import json

    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": document_data["document_type_acronym"],
        "TypeDocument": document_data["document_type_name"],
        "Reference": document_data["reference"],
        "Text": json.dumps({"html": html_content})  # CRITICAL: Must be JSON string with html key
    }

    # Inyectar frase_anual desde settings del tenant
    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = get_tenant_settings(schema_name).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/preview-pdf/")
    logger.info(f"Document: {document_data.get('reference', 'N/A')}")
    logger.info(f"Type: {document_data.get('document_type_name', 'N/A')} ({document_data.get('document_type_acronym', 'N/A')})")
    logger.info(f"Content length: {len(html_content)} chars")
    logger.debug(f"Payload keys: {list(pdfcomposer_data.keys())}")
    logger.debug(f"NameAcronyType: {pdfcomposer_data.get('NameAcronyType')}")
    logger.debug(f"TypeDocument: {pdfcomposer_data.get('TypeDocument')}")
    logger.debug(f"Reference: {pdfcomposer_data.get('Reference')}")

    # Llamar a PDFComposer con retry (mismo patrón que create_case)
    timeout = httpx.Timeout(90.0)
    headers = {"X-API-Key": pdfcomposer_api_key}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):  # 1 reintento
            try:
                logger.info(f"Intento {attempt + 1}/2")

                response = await client.post(
                    f"{pdfcomposer_url}/preview-pdf/",
                    data=pdfcomposer_data,
                    headers=headers,
                    follow_redirects=True
                )

                response.raise_for_status()

                # Leer contenido directo en memoria
                pdf_bytes = await response.aread()
                pdf_size = len(pdf_bytes)

                logger.info("PDF preview generado exitosamente")
                logger.info(f"Size: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

                # Validar que recibimos un PDF válido
                if pdf_size == 0:
                    raise ExternalServiceError("PDFComposer retorno PDF vacio")

                # Validar magic bytes
                if not pdf_bytes.startswith(b'%PDF'):
                    logger.warning("Respuesta no parece ser PDF valido")

                # Validar tamaño máximo (10MB)
                MAX_PDF_SIZE = 10 * 1024 * 1024  # 10MB
                if pdf_size > MAX_PDF_SIZE:
                    raise ExternalServiceError(f"PDF excede tamano maximo ({pdf_size/1024/1024:.2f}MB > 10MB)")

                return pdf_bytes

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP Error: {e.response.status_code}")
                logger.error(f"Response: {e.response.text[:200]}")

                if attempt == 1:  # Último intento
                    raise ExternalServiceError(
                        f"PDFComposer preview error: HTTP {e.response.status_code}",
                        details={"response": e.response.text[:500]}
                    )

            except httpx.TimeoutException:
                logger.error("Timeout (90s excedido)")
                if attempt == 1:
                    raise ExternalServiceError("PDFComposer preview timeout despues de 90 segundos")

            except httpx.RequestError as e:
                logger.error(f"Request Error: {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error de conexion con PDFComposer: {str(e)}")

            except ExternalServiceError:
                # Re-raise ExternalServiceError sin modificar
                if attempt == 1:
                    raise

            except Exception as e:
                logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error inesperado en PDFComposer preview: {str(e)}")

            # Si no es el último intento, esperar antes de retry
            if attempt == 0:
                import asyncio
                logger.info("Esperando 1s antes de reintentar...")
                await asyncio.sleep(1)

    # No debería llegar aquí
    raise ExternalServiceError("PDFComposer preview: error desconocido despues de reintentos")


async def call_pdfcomposer_import(
    pdf_file: bytes,
    filename: str,
    url_logo: str,
    name_acrony_type: str,
    document_type: str,
    reference: str,
    *,
    schema_name: str
) -> bytes:
    """
    Llama a PDFComposer /import/ para procesar PDF importado.

    Agrega una hoja final con información del documento y espacio para firmas.

    Args:
        pdf_file: Contenido binario del PDF a importar
        filename: Nombre del archivo PDF
        url_logo: URL del logo del municipio
        name_acrony_type: Acrónimo del tipo de documento (ej: "ANEXO")
        document_type: Nombre del tipo de documento (ej: "Anexo")
        reference: Referencia del documento

    Returns:
        bytes: PDF procesado con hoja final para firmas

    Raises:
        ValidationError: Si faltan campos requeridos o PDF inválido
        ExternalServiceError: Si PDFComposer falla o no responde
    """
    logger.info("Llamando a PDFComposer /import/...")

    # Validar que tenemos un PDF válido
    if not pdf_file or len(pdf_file) == 0:
        raise ValidationError("PDF file no puede estar vacío")

    if not pdf_file.startswith(b'%PDF'):
        raise ValidationError("El archivo no es un PDF válido")

    # Validar tamaño máximo (25MB)
    MAX_IMPORT_SIZE = 25 * 1024 * 1024  # 25MB
    if len(pdf_file) > MAX_IMPORT_SIZE:
        raise ValidationError(f"PDF excede tamaño máximo permitido ({len(pdf_file)/1024/1024:.2f}MB > 25MB)")

    # Validar campos requeridos
    if not all([url_logo, name_acrony_type, document_type, reference]):
        raise ValidationError("Faltan campos requeridos para PDFComposer /import/")

    # Obtener credenciales desde .env
    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logger.info(f"URL: {pdfcomposer_url}/import/")
    logger.info(f"Filename: {filename}")
    logger.info(f"Type: {document_type} ({name_acrony_type})")
    logger.info(f"Reference: {reference}")
    logger.info(f"Size: {len(pdf_file)} bytes ({len(pdf_file)/1024:.2f} KB)")

    # Preparar payload multipart/form-data
    files = {
        'pdf_file': (filename, pdf_file, 'application/pdf')
    }

    data = {
        'urlLogo': url_logo,
        'NameAcronyType': name_acrony_type,
        'document_type': document_type,
        'reference': reference
    }

    # Inyectar frase_anual desde settings del tenant
    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = get_tenant_settings(schema_name).get("annual_slogan", "")
    if annual_slogan:
        data["frase_anual"] = annual_slogan

    # Llamar a PDFComposer con retry
    timeout = httpx.Timeout(90.0)  # Timeout largo para procesamiento de PDF
    headers = {"X-API-Key": pdfcomposer_api_key}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):  # 1 reintento
            try:
                logger.info(f"Intento {attempt + 1}/2")

                response = await client.post(
                    f"{pdfcomposer_url}/import/",
                    files=files,
                    data=data,
                    headers=headers,
                    follow_redirects=True
                )

                response.raise_for_status()

                # Leer contenido directo en memoria
                pdf_bytes = await response.aread()
                pdf_size = len(pdf_bytes)

                logger.info("PDF importado procesado exitosamente")
                logger.info(f"Size: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

                # Validar que recibimos un PDF válido
                if pdf_size == 0:
                    raise ExternalServiceError("PDFComposer retorno PDF vacio")

                # Validar magic bytes
                if not pdf_bytes.startswith(b'%PDF'):
                    logger.warning("Respuesta no parece ser PDF valido")

                # Validar tamaño máximo (30MB para el procesado)
                MAX_OUTPUT_SIZE = 30 * 1024 * 1024
                if pdf_size > MAX_OUTPUT_SIZE:
                    raise ExternalServiceError(f"PDF procesado excede tamano maximo ({pdf_size/1024/1024:.2f}MB > 30MB)")

                return pdf_bytes

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP Error: {e.response.status_code}")
                logger.error(f"Response: {e.response.text[:200]}")

                if attempt == 1:  # Último intento
                    raise ExternalServiceError(
                        f"PDFComposer import error: HTTP {e.response.status_code}",
                        details={"response": e.response.text[:500]}
                    )

            except httpx.TimeoutException:
                logger.error("Timeout (90s excedido)")
                if attempt == 1:
                    raise ExternalServiceError("PDFComposer import timeout despues de 90 segundos")

            except httpx.RequestError as e:
                logger.error(f"Request Error: {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error de conexion con PDFComposer: {str(e)}")

            except ExternalServiceError:
                # Re-raise ExternalServiceError sin modificar
                if attempt == 1:
                    raise

            except Exception as e:
                logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error inesperado en PDFComposer import: {str(e)}")

            # Si no es el último intento, esperar antes de retry
            if attempt == 0:
                import asyncio
                logger.info("Esperando 1s antes de reintentar...")
                await asyncio.sleep(1)

    # No debería llegar aquí
    raise ExternalServiceError("PDFComposer import: error desconocido despues de reintentos")


async def call_pdfcomposer_note_preview(
    document_data: Dict[str, Any],
    para: str,
    cc: Optional[str] = None,
    *,
    schema_name: str
) -> bytes:
    """
    Llama a PDFComposer /note-preview/ para generar preview de NOTA con marca de agua.

    Usa template especial nota_preview.html que incluye header de recipients.

    Args:
        document_data: Dict con datos del documento:
            - document_type_acronym: Acrónimo (NOTA)
            - document_type_name: Nombre del tipo
            - reference: Referencia del documento
            - content: Contenido HTML del documento
            - municipality_logo_url: (opcional) URL del logo
        para: String con destinatarios TO (ej: "Finanzas#Tesorería, Legales#Asesoría")
        cc: String con destinatarios CC o None (ej: "RRHH#Personal")

    Returns:
        bytes: PDF generado por PDFComposer con marca de agua

    Raises:
        ValidationError: Si faltan campos requeridos
        ExternalServiceError: Si PDFComposer falla o no responde
    """
    import asyncio

    logger.info("Llamando a PDFComposer /note-preview/...")

    # Validar campos requeridos
    required_fields = ["document_type_acronym", "document_type_name", "reference", "content"]
    missing_fields = [field for field in required_fields if not document_data.get(field)]
    if missing_fields:
        raise ValidationError(f"Campos requeridos faltantes para PDFComposer note-preview: {', '.join(missing_fields)}")

    # Obtener credenciales desde .env
    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = document_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    # Extraer contenido HTML del campo content
    content_raw = document_data.get("content", "")
    if isinstance(content_raw, dict) and 'html' in content_raw:
        html_content = content_raw['html']
    elif isinstance(content_raw, str):
        html_content = content_raw
    else:
        html_content = str(content_raw) if content_raw else ""

    # Preparar payload para PDFComposer /note-preview/
    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": document_data["document_type_acronym"],
        "document_type": document_data["document_type_name"],
        "reference": document_data["reference"],
        "para": para,
        "Text": json.dumps({"html": html_content})
    }

    # Agregar CC solo si existe
    if cc:
        pdfcomposer_data["cc"] = cc

    # Inyectar frase_anual desde settings del tenant
    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = get_tenant_settings(schema_name).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/note-preview/")
    logger.info(f"Document: {document_data.get('reference', 'N/A')}")
    logger.info(f"Type: {document_data.get('document_type_name', 'N/A')} ({document_data.get('document_type_acronym', 'N/A')})")
    logger.info(f"para: {para}")
    logger.info(f"cc: {cc}")
    logger.info(f"Content length: {len(html_content)} chars")

    # Llamar a PDFComposer con retry
    timeout = httpx.Timeout(90.0)
    headers = {"X-API-Key": pdfcomposer_api_key}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                logger.info(f"Intento {attempt + 1}/2")

                response = await client.post(
                    f"{pdfcomposer_url}/note-preview/",
                    data=pdfcomposer_data,
                    headers=headers,
                    follow_redirects=True
                )

                response.raise_for_status()

                pdf_bytes = await response.aread()
                pdf_size = len(pdf_bytes)

                logger.info("PDF note-preview generado exitosamente")
                logger.info(f"Size: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

                if pdf_size == 0:
                    raise ExternalServiceError("PDFComposer retorno PDF vacio")

                if not pdf_bytes.startswith(b'%PDF'):
                    logger.warning("Respuesta no parece ser PDF valido")

                MAX_PDF_SIZE = 10 * 1024 * 1024
                if pdf_size > MAX_PDF_SIZE:
                    raise ExternalServiceError(f"PDF excede tamano maximo ({pdf_size/1024/1024:.2f}MB > 10MB)")

                return pdf_bytes

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP Error: {e.response.status_code}")
                logger.error(f"Response: {e.response.text[:200]}")

                if attempt == 1:
                    raise ExternalServiceError(
                        f"PDFComposer note-preview error: HTTP {e.response.status_code}",
                        details={"response": e.response.text[:500]}
                    )

            except httpx.TimeoutException:
                logger.error("Timeout (90s excedido)")
                if attempt == 1:
                    raise ExternalServiceError("PDFComposer note-preview timeout despues de 90 segundos")

            except httpx.RequestError as e:
                logger.error(f"Request Error: {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error de conexion con PDFComposer: {str(e)}")

            except ExternalServiceError:
                if attempt == 1:
                    raise

            except Exception as e:
                logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error inesperado en PDFComposer note-preview: {str(e)}")

            if attempt == 0:
                logger.info("Esperando 1s antes de reintentar...")
                await asyncio.sleep(1)

    raise ExternalServiceError("PDFComposer note-preview: error desconocido despues de reintentos")


async def call_pdfcomposer_note_final(
    document_data: Dict[str, Any],
    para: str,
    cc: Optional[str] = None,
    *,
    schema_name: str
) -> bytes:
    """
    Llama a PDFComposer /note/ para generar PDF final de NOTA sin marca de agua.

    Usa template especial nota.html que incluye header de recipients.
    Este PDF se usa para el proceso de firma.

    Args:
        document_data: Dict con datos del documento:
            - document_type_acronym: Acrónimo (NOTA)
            - document_type_name: Nombre del tipo
            - reference: Referencia del documento
            - content: Contenido HTML del documento
            - municipality_logo_url: (opcional) URL del logo
        para: String con destinatarios TO (ej: "Finanzas#Tesorería, Legales#Asesoría")
        cc: String con destinatarios CC o None (ej: "RRHH#Personal")

    Returns:
        bytes: PDF generado por PDFComposer sin marca de agua

    Raises:
        ValidationError: Si faltan campos requeridos
        ExternalServiceError: Si PDFComposer falla o no responde
    """
    import asyncio

    logger.info("Llamando a PDFComposer /note/...")

    # Extraer campos (aceptar ambos nombres: type_acronym o document_type_acronym)
    type_acronym = document_data.get('type_acronym') or document_data.get('document_type_acronym')
    type_name = document_data.get('type_name') or document_data.get('document_type_name')
    reference = document_data.get('reference')
    content_raw = document_data.get('content', '')

    # Validar campos requeridos
    if not type_acronym:
        raise ValidationError("type_acronym es requerido para PDFComposer note")
    if not type_name:
        raise ValidationError("type_name es requerido para PDFComposer note")
    if not reference:
        raise ValidationError("reference es requerido para PDFComposer note")

    # Obtener credenciales desde .env
    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = document_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    # Extraer contenido HTML del campo content
    if isinstance(content_raw, dict) and 'html' in content_raw:
        html_content = content_raw['html']
    elif isinstance(content_raw, str):
        html_content = content_raw
    else:
        html_content = str(content_raw) if content_raw else ""

    # Preparar payload para PDFComposer /note/
    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": type_acronym,
        "document_type": type_name,
        "reference": reference,
        "para": para,
        "Text": json.dumps({"html": html_content})
    }

    # Agregar CC solo si existe
    if cc:
        pdfcomposer_data["cc"] = cc

    # Inyectar frase_anual desde settings del tenant
    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = get_tenant_settings(schema_name).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/note/")
    logger.info(f"Document: {reference}")
    logger.info(f"Type: {type_name} ({type_acronym})")
    logger.info(f"para: {para}")
    logger.info(f"cc: {cc}")
    logger.info(f"Content length: {len(html_content)} chars")

    # Llamar a PDFComposer con retry
    timeout = httpx.Timeout(90.0)
    headers = {"X-API-Key": pdfcomposer_api_key}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                logger.info(f"Intento {attempt + 1}/2")

                response = await client.post(
                    f"{pdfcomposer_url}/note/",
                    data=pdfcomposer_data,
                    headers=headers,
                    follow_redirects=True
                )

                response.raise_for_status()

                pdf_bytes = await response.aread()
                pdf_size = len(pdf_bytes)

                logger.info("PDF note generado exitosamente")
                logger.info(f"Size: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

                if pdf_size == 0:
                    raise ExternalServiceError("PDFComposer retorno PDF vacio")

                if not pdf_bytes.startswith(b'%PDF'):
                    logger.warning("Respuesta no parece ser PDF valido")

                MAX_PDF_SIZE = 10 * 1024 * 1024
                if pdf_size > MAX_PDF_SIZE:
                    raise ExternalServiceError(f"PDF excede tamano maximo ({pdf_size/1024/1024:.2f}MB > 10MB)")

                return pdf_bytes

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP Error: {e.response.status_code}")
                logger.error(f"Response: {e.response.text[:200]}")

                if attempt == 1:
                    raise ExternalServiceError(
                        f"PDFComposer note error: HTTP {e.response.status_code}",
                        details={"response": e.response.text[:500]}
                    )

            except httpx.TimeoutException:
                logger.error("Timeout (90s excedido)")
                if attempt == 1:
                    raise ExternalServiceError("PDFComposer note timeout despues de 90 segundos")

            except httpx.RequestError as e:
                logger.error(f"Request Error: {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error de conexion con PDFComposer: {str(e)}")

            except ExternalServiceError:
                if attempt == 1:
                    raise

            except Exception as e:
                logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error inesperado en PDFComposer note: {str(e)}")

            if attempt == 0:
                logger.info("Esperando 1s antes de reintentar...")
                await asyncio.sleep(1)

    raise ExternalServiceError("PDFComposer note: error desconocido despues de reintentos")


async def call_pdfcomposer_create_ifrlm(ifrlm_data: Dict[str, Any], *, schema_name: str) -> bytes:
    """
    Llama a PDFComposer /ifrlm/ para generar PDF de informe de legajo.

    Usa template especial ifrlm.html con campos del legajo y snapshot HTML.

    Args:
        ifrlm_data: Dict con datos del informe:
            - municipality_logo_url: URL del logo (opcional, usa DEFAULT_LOGO_URL)
            - document_type_acronym: IFRLM
            - document_type_name: Nombre del tipo de documento
            - document_reference: Referencia del documento
            - record_number: Numero del legajo
            - registry_name: Nombre de la familia de registro
            - state: Estado actual del legajo
            - snapshot_html: HTML del snapshot del legajo
            - signer_full_name: Nombre del firmante
            - signer_municipality: Municipalidad
            - official_document_number: Numero oficial
            - city_name: Ciudad

    Returns:
        bytes: PDF generado por PDFComposer

    Raises:
        ValidationError: Si faltan campos requeridos
        ExternalServiceError: Si PDFComposer falla o no responde
    """
    logger.info("Llamando a PDFComposer /ifrlm/...")

    # Validar campos requeridos
    required_fields = [
        "document_type_acronym", "document_type_name", "document_reference",
        "record_number", "registry_name", "snapshot_html",
        "signer_full_name", "signer_municipality",
        "official_document_number", "city_name"
    ]

    missing_fields = [field for field in required_fields if not ifrlm_data.get(field)]
    if missing_fields:
        raise ValidationError(f"Campos requeridos faltantes para PDFComposer IFRLM: {', '.join(missing_fields)}")

    # Obtener credenciales desde .env
    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = ifrlm_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    # Preparar payload para PDFComposer (multipart/form-data)
    # PDFComposer /ifrlm/ acepta estos campos
    # Firmante, document_number y city NO son parte de este endpoint
    # Esos se agregan en el paso de firma con Notary
    pdfcomposer_data = {
        # Logo
        "urlLogo": logo_url,
        # Documento
        "NameAcronyType": ifrlm_data["document_type_acronym"],
        "document_type": ifrlm_data["document_type_name"],
        "reference": ifrlm_data["document_reference"],
        # Legajo
        "record_number": ifrlm_data["record_number"],
        "registry_name": ifrlm_data["registry_name"],
        "state": ifrlm_data["state"],
        # Snapshot HTML
        "snapshot_html": ifrlm_data["snapshot_html"],
    }

    # Inyectar frase_anual desde settings del tenant
    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = get_tenant_settings(schema_name).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/ifrlm/")
    logger.info(f"Record: {ifrlm_data['record_number']}")
    logger.info(f"Official number: {ifrlm_data['official_document_number']}")
    logger.info(f"Signer: {ifrlm_data['signer_full_name']}")

    # Llamar a PDFComposer con retry (mismo patron que create_case)
    timeout = httpx.Timeout(90.0)
    headers = {"X-API-Key": pdfcomposer_api_key}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                logger.info(f"Intento {attempt + 1}/2")

                response = await client.post(
                    f"{pdfcomposer_url}/ifrlm/",
                    data=pdfcomposer_data,
                    headers=headers,
                    follow_redirects=True
                )

                response.raise_for_status()

                pdf_bytes = await response.aread()
                pdf_size = len(pdf_bytes)

                logger.info("PDF IFRLM generado exitosamente")
                logger.info(f"Size: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

                # Validaciones
                if pdf_size == 0:
                    raise ExternalServiceError("PDFComposer retorno PDF vacio")

                if not pdf_bytes.startswith(b'%PDF'):
                    logger.warning("Respuesta no parece ser PDF valido")

                MAX_PDF_SIZE = 10 * 1024 * 1024
                if pdf_size > MAX_PDF_SIZE:
                    raise ExternalServiceError(f"PDF excede tamano maximo ({pdf_size/1024/1024:.2f}MB > 10MB)")

                return pdf_bytes

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP Error: {e.response.status_code}")
                logger.error(f"Response: {e.response.text[:200]}")
                if attempt == 1:
                    raise ExternalServiceError(
                        f"PDFComposer IFRLM error: HTTP {e.response.status_code}",
                        details={"response": e.response.text[:500]}
                    )

            except httpx.TimeoutException:
                logger.error("Timeout (90s excedido)")
                if attempt == 1:
                    raise ExternalServiceError("PDFComposer IFRLM timeout despues de 90 segundos")

            except httpx.RequestError as e:
                logger.error(f"Request Error: {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error de conexion con PDFComposer: {str(e)}")

            except ExternalServiceError:
                if attempt == 1:
                    raise

            except Exception as e:
                logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
                if attempt == 1:
                    raise ExternalServiceError(f"Error inesperado en PDFComposer IFRLM: {str(e)}")

            if attempt == 0:
                import asyncio
                logger.info("Esperando 1s antes de reintentar...")
                await asyncio.sleep(1)

    raise ExternalServiceError("PDFComposer IFRLM: error desconocido despues de reintentos")
