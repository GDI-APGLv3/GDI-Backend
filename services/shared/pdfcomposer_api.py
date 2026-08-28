
from typing import Dict, Any, Optional
import asyncio
import hashlib
import os
import httpx
import json
from shared.logging import get_logger
from shared.exceptions import ExternalServiceError, ValidationError
from config.constants import DEFAULT_LOGO_URL, MAX_SIGNABLE_PDF_SIZE

logger = get_logger(__name__)


async def _post_pdfcomposer_with_retry(
    endpoint_path: str,
    *,
    pdfcomposer_url: str,
    pdfcomposer_api_key: str,
    request_kwargs: Dict[str, Any],
    success_log: str,
    op_label: str = "",
    max_size: int = MAX_SIGNABLE_PDF_SIZE,
    check_sha256: bool = True,
    log_http_error_details: bool = True,
) -> bytes:
    timeout = httpx.Timeout(90.0)
    headers = {"X-API-Key": pdfcomposer_api_key}
    url = f"{pdfcomposer_url}{endpoint_path}"
    label = f" {op_label}" if op_label else ""

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                logger.info(f"Intento {attempt + 1}/2")

                response = await client.post(
                    url,
                    headers=headers,
                    follow_redirects=True,
                    **request_kwargs
                )

                response.raise_for_status()

                pdf_bytes = await response.aread()
                pdf_size = len(pdf_bytes)

                logger.info(success_log)
                logger.info(f"Size: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

                if pdf_size == 0:
                    raise ExternalServiceError("PDFComposer retorno PDF vacio")

                if not pdf_bytes.startswith(b'%PDF'):
                    logger.warning("Respuesta no parece ser PDF valido")

                if pdf_size > max_size:
                    raise ExternalServiceError(f"PDF excede tamano maximo ({pdf_size/1024/1024:.2f}MB > {max_size/1024/1024:.0f}MB)")

                if check_sha256:
                    local_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
                    remote_sha256 = response.headers.get("X-PDF-SHA256")
                    if remote_sha256:
                        if remote_sha256 != local_sha256:
                            logger.warning(
                                f"[SU-009] SHA-256 mismatch en {endpoint_path}: "
                                f"PDFComposer={remote_sha256} local={local_sha256} "
                                f"— posible corrupcion en transito. Usando hash local."
                            )
                        else:
                            logger.debug(f"[SU-009] SHA-256 OK {endpoint_path}: {local_sha256}")
                    else:
                        logger.debug(f"[SU-009] X-PDF-SHA256 no presente en respuesta {endpoint_path}")

                return pdf_bytes

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP Error: {e.response.status_code}")
                if log_http_error_details:
                    logger.error(f"Response: {e.response.text[:200]}")

                if attempt == 1:
                    if log_http_error_details:
                        raise ExternalServiceError(
                            f"PDFComposer{label} error: HTTP {e.response.status_code}",
                            details={"response": e.response.text[:500]}
                        )
                    raise ExternalServiceError(f"PDFComposer{label} error: HTTP {e.response.status_code}")

            except httpx.TimeoutException:
                logger.error("Timeout (90s excedido)")
                if attempt == 1:
                    raise ExternalServiceError(f"PDFComposer{label} timeout despues de 90 segundos")

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
                    raise ExternalServiceError(f"Error inesperado en PDFComposer{label}: {str(e)}")

            if attempt == 0:
                logger.info("Esperando 1s antes de reintentar...")
                await asyncio.sleep(1)

    raise ExternalServiceError(f"PDFComposer{label}: error desconocido despues de reintentos")


async def call_pdfcomposer_create_case(cover_data: Dict[str, Any], *, schema_name: str) -> bytes:
    logger.info("Llamando a PDFComposer /create-case/...")

    required_fields = [
        "document_type_acronym", "document_type_name", "document_reference",
        "case_number", "case_type_acronym", "case_type_name", "case_motive",
        "initiating_department", "case_creator", "signer_full_name",
        "signer_municipality", "city_name"
    ]

    missing_fields = [field for field in required_fields if not cover_data.get(field)]
    if missing_fields:
        raise ValidationError(f"Campos requeridos faltantes para PDFComposer: {', '.join(missing_fields)}")

    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = cover_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    logger.debug("Valor de case_motive en cover_data:")
    logger.debug(f"case_motive: '{cover_data.get('case_motive')}'")
    logger.debug(f"case_motive type: {type(cover_data.get('case_motive'))}")
    logger.debug(f"case_motive is None: {cover_data.get('case_motive') is None}")
    logger.debug(f"case_motive is empty string: {cover_data.get('case_motive') == ''}")

    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": cover_data["document_type_acronym"],
        "document_type": cover_data["document_type_name"],
        "reference": cover_data["document_reference"],
        "case_number": cover_data["case_number"],
        "acrony_case_type": cover_data["case_type_acronym"],
        "case_type": cover_data["case_type_name"],
        "case_motive": cover_data["case_motive"],
        "initiating_division": cover_data["initiating_department"],
        "creator": cover_data["case_creator"]
    }

    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = (await get_tenant_settings(schema_name)).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/create-case/")
    logger.info(f"Case: {cover_data['case_number']}")
    logger.info(f"Signer: {cover_data['signer_full_name']}")

    logger.debug("Payload completo a enviar:")
    for key, value in pdfcomposer_data.items():
        logger.debug(f"{key}: '{value}' (type: {type(value).__name__})")

    return await _post_pdfcomposer_with_retry(
        "/create-case/",
        pdfcomposer_url=pdfcomposer_url,
        pdfcomposer_api_key=pdfcomposer_api_key,
        request_kwargs={"data": pdfcomposer_data},
        success_log="PDF generado exitosamente",
    )


async def call_pdfcomposer_create_transfer(transfer_data: Dict[str, Any], *, schema_name: str) -> bytes:
    logger.info("Llamando a PDFComposer /move/...")

    required_fields = [
        "document_type_acronym", "document_type_name", "document_reference",
        "movement_type", "requesting_area", "receiving_area", "movement_reason"
    ]

    missing_fields = [field for field in required_fields if not transfer_data.get(field)]
    if missing_fields:
        raise ValidationError(f"Campos requeridos faltantes para PDFComposer: {', '.join(missing_fields)}")

    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = transfer_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": transfer_data["document_type_acronym"],
        "document_type": transfer_data["document_type_name"],
        "reference": transfer_data["document_reference"],
        "tipo_movimiento": transfer_data["movement_type"],
        "area_requiriente": transfer_data["requesting_area"],
        "area_receptora": transfer_data["receiving_area"],
        "motivo": transfer_data["movement_reason"]
    }

    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = (await get_tenant_settings(schema_name)).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/move/")
    logger.info(f"Movement: {transfer_data['movement_type']}")

    return await _post_pdfcomposer_with_retry(
        "/move/",
        pdfcomposer_url=pdfcomposer_url,
        pdfcomposer_api_key=pdfcomposer_api_key,
        request_kwargs={"data": pdfcomposer_data},
        success_log="PDF generado exitosamente",
        log_http_error_details=False,
    )


async def call_pdfcomposer_preview_pdf(document_data: Dict[str, Any], *, schema_name: str) -> bytes:
    logger.info("Llamando a PDFComposer /preview-pdf/...")

    required_fields = ["document_type_acronym", "document_type_name", "reference", "content"]
    missing_fields = [field for field in required_fields if not document_data.get(field)]
    if missing_fields:
        raise ValidationError(f"Campos requeridos faltantes para PDFComposer preview: {', '.join(missing_fields)}")

    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = document_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    content_raw = document_data.get("content", "")
    if isinstance(content_raw, dict) and 'html' in content_raw:
        html_content = content_raw['html']
    elif isinstance(content_raw, str):
        html_content = content_raw
    else:
        html_content = str(content_raw) if content_raw else ""

    import json

    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": document_data["document_type_acronym"],
        "TypeDocument": document_data["document_type_name"],
        "Reference": document_data["reference"],
        "Text": json.dumps({"html": html_content})
    }

    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = (await get_tenant_settings(schema_name)).get("annual_slogan", "")
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

    return await _post_pdfcomposer_with_retry(
        "/preview-pdf/",
        pdfcomposer_url=pdfcomposer_url,
        pdfcomposer_api_key=pdfcomposer_api_key,
        request_kwargs={"data": pdfcomposer_data},
        success_log="PDF preview generado exitosamente",
        op_label="preview",
        check_sha256=False,
    )


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
    logger.info("Llamando a PDFComposer /import/...")

    if not pdf_file or len(pdf_file) == 0:
        raise ValidationError("PDF file no puede estar vacío")

    if not pdf_file.startswith(b'%PDF'):
        raise ValidationError("El archivo no es un PDF válido")

    MAX_IMPORT_SIZE = 25 * 1024 * 1024
    if len(pdf_file) > MAX_IMPORT_SIZE:
        raise ValidationError(f"PDF excede tamaño máximo permitido ({len(pdf_file)/1024/1024:.2f}MB > 25MB)")

    if not all([url_logo, name_acrony_type, document_type, reference]):
        raise ValidationError("Faltan campos requeridos para PDFComposer /import/")

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

    files = {
        'pdf_file': (filename, pdf_file, 'application/pdf')
    }

    data = {
        'urlLogo': url_logo,
        'NameAcronyType': name_acrony_type,
        'document_type': document_type,
        'reference': reference
    }

    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = (await get_tenant_settings(schema_name)).get("annual_slogan", "")
    if annual_slogan:
        data["frase_anual"] = annual_slogan

    MAX_OUTPUT_SIZE = 30 * 1024 * 1024

    return await _post_pdfcomposer_with_retry(
        "/import/",
        pdfcomposer_url=pdfcomposer_url,
        pdfcomposer_api_key=pdfcomposer_api_key,
        request_kwargs={"files": files, "data": data},
        success_log="PDF importado procesado exitosamente",
        op_label="import",
        max_size=MAX_OUTPUT_SIZE,
    )


async def call_pdfcomposer_note_preview(
    document_data: Dict[str, Any],
    para: str,
    cc: Optional[str] = None,
    *,
    schema_name: str
) -> bytes:
    logger.info("Llamando a PDFComposer /note-preview/...")

    required_fields = ["document_type_acronym", "document_type_name", "reference", "content"]
    missing_fields = [field for field in required_fields if not document_data.get(field)]
    if missing_fields:
        raise ValidationError(f"Campos requeridos faltantes para PDFComposer note-preview: {', '.join(missing_fields)}")

    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = document_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    content_raw = document_data.get("content", "")
    if isinstance(content_raw, dict) and 'html' in content_raw:
        html_content = content_raw['html']
    elif isinstance(content_raw, str):
        html_content = content_raw
    else:
        html_content = str(content_raw) if content_raw else ""

    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": document_data["document_type_acronym"],
        "document_type": document_data["document_type_name"],
        "reference": document_data["reference"],
        "para": para,
        "Text": json.dumps({"html": html_content})
    }

    if cc:
        pdfcomposer_data["cc"] = cc

    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = (await get_tenant_settings(schema_name)).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/note-preview/")
    logger.info(f"Document: {document_data.get('reference', 'N/A')}")
    logger.info(f"Type: {document_data.get('document_type_name', 'N/A')} ({document_data.get('document_type_acronym', 'N/A')})")
    logger.info(f"para: {para}")
    logger.info(f"cc: {cc}")
    logger.info(f"Content length: {len(html_content)} chars")

    return await _post_pdfcomposer_with_retry(
        "/note-preview/",
        pdfcomposer_url=pdfcomposer_url,
        pdfcomposer_api_key=pdfcomposer_api_key,
        request_kwargs={"data": pdfcomposer_data},
        success_log="PDF note-preview generado exitosamente",
        op_label="note-preview",
        check_sha256=False,
    )


async def call_pdfcomposer_note_final(
    document_data: Dict[str, Any],
    para: str,
    cc: Optional[str] = None,
    *,
    schema_name: str
) -> bytes:
    logger.info("Llamando a PDFComposer /note/...")

    type_acronym = document_data.get('type_acronym') or document_data.get('document_type_acronym')
    type_name = document_data.get('type_name') or document_data.get('document_type_name')
    reference = document_data.get('reference')
    content_raw = document_data.get('content', '')

    if not type_acronym:
        raise ValidationError("type_acronym es requerido para PDFComposer note")
    if not type_name:
        raise ValidationError("type_name es requerido para PDFComposer note")
    if not reference:
        raise ValidationError("reference es requerido para PDFComposer note")

    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = document_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    if isinstance(content_raw, dict) and 'html' in content_raw:
        html_content = content_raw['html']
    elif isinstance(content_raw, str):
        html_content = content_raw
    else:
        html_content = str(content_raw) if content_raw else ""

    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": type_acronym,
        "document_type": type_name,
        "reference": reference,
        "para": para,
        "Text": json.dumps({"html": html_content})
    }

    if cc:
        pdfcomposer_data["cc"] = cc

    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = (await get_tenant_settings(schema_name)).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/note/")
    logger.info(f"Document: {reference}")
    logger.info(f"Type: {type_name} ({type_acronym})")
    logger.info(f"para: {para}")
    logger.info(f"cc: {cc}")
    logger.info(f"Content length: {len(html_content)} chars")

    return await _post_pdfcomposer_with_retry(
        "/note/",
        pdfcomposer_url=pdfcomposer_url,
        pdfcomposer_api_key=pdfcomposer_api_key,
        request_kwargs={"data": pdfcomposer_data},
        success_log="PDF note generado exitosamente",
        op_label="note",
    )


async def call_pdfcomposer_create_ifrlm(ifrlm_data: Dict[str, Any], *, schema_name: str) -> bytes:
    logger.info("Llamando a PDFComposer /ifrlm/...")

    required_fields = [
        "document_type_acronym", "document_type_name", "document_reference",
        "record_number", "registry_name", "snapshot_html",
        "signer_full_name", "signer_municipality",
        "official_document_number", "city_name"
    ]

    missing_fields = [field for field in required_fields if not ifrlm_data.get(field)]
    if missing_fields:
        raise ValidationError(f"Campos requeridos faltantes para PDFComposer IFRLM: {', '.join(missing_fields)}")

    pdfcomposer_url = os.getenv('PDFCOMPOSER_URL')
    if not pdfcomposer_url:
        raise ValidationError("PDFCOMPOSER_URL no configurado en variables de entorno")

    pdfcomposer_api_key = os.getenv('PDFCOMPOSER_API_KEY')
    if not pdfcomposer_api_key:
        raise ValidationError("PDFCOMPOSER_API_KEY no configurado en variables de entorno")

    logo_url = ifrlm_data.get('municipality_logo_url') or DEFAULT_LOGO_URL

    pdfcomposer_data = {
        "urlLogo": logo_url,
        "NameAcronyType": ifrlm_data["document_type_acronym"],
        "document_type": ifrlm_data["document_type_name"],
        "reference": ifrlm_data["document_reference"],
        "record_number": ifrlm_data["record_number"],
        "registry_name": ifrlm_data["registry_name"],
        "state": ifrlm_data["state"],
        "snapshot_html": ifrlm_data["snapshot_html"],
    }

    from services.shared.settings_utils import get_tenant_settings
    annual_slogan = (await get_tenant_settings(schema_name)).get("annual_slogan", "")
    if annual_slogan:
        pdfcomposer_data["frase_anual"] = annual_slogan

    logger.info(f"URL: {pdfcomposer_url}/ifrlm/")
    logger.info(f"Record: {ifrlm_data['record_number']}")
    logger.info(f"Official number: {ifrlm_data['official_document_number']}")
    logger.info(f"Signer: {ifrlm_data['signer_full_name']}")

    return await _post_pdfcomposer_with_retry(
        "/ifrlm/",
        pdfcomposer_url=pdfcomposer_url,
        pdfcomposer_api_key=pdfcomposer_api_key,
        request_kwargs={"data": pdfcomposer_data},
        success_log="PDF IFRLM generado exitosamente",
        op_label="IFRLM",
    )
