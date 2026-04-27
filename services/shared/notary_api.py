"""
Cliente para llamar directamente a Notary API.
Reemplaza llamadas a Legal Orchestrator para firma de documentos.

Incluye manejo automático de FULLPAGE:
- Primer intento: Intentar firmar PDF
- Si responde FULLPAGE: Agregar página de firma (con marcador "end-text") y reintentar
- Segundo intento: Firmar PDF aumentado

Si hay certificado en R2 (resolve_certificate), envía cert_file + cert_password
en el multipart para que Notary firme con PAdES sin necesitar el .p12 local.
"""

import os
import httpx
from typing import Dict, Any
from shared.logging import get_logger
from services.shared.pdf_utils import add_blank_page_to_pdf

logger = get_logger(__name__)


# Configuración de Notary desde variables de entorno
NOTARY_URL = os.getenv('NOTARY_URL')
if not NOTARY_URL:
    raise RuntimeError("NOTARY_URL no configurado en variables de entorno")

NOTARY_API_KEY = os.getenv('NOTARY_API_KEY')
if not NOTARY_API_KEY:
    raise RuntimeError("NOTARY_API_KEY no configurado en variables de entorno")

NOTARY_TIMEOUT = 20.0  # 20s: tolerancia a TSA externo lento en p99 (timestamp.digicert.com)


async def call_notary_sign_pdf(
    pdf_bytes: bytes,
    signer_name: str,
    signer_seal: str,
    signer_department: str,
    signer_municipality: str,
    official_number: str,
    city: str = "LATAM",
    stamp_position: str = "",
    *,
    tenant_id: str = None,
    schema_name: str = None
) -> bytes:
    """
    Llama a Notary /sign-pdf con manejo automático de FULLPAGE.

    Flujo:
    1. Si tenant_id y schema_name: intenta resolver certificado de R2
    2. Primer intento: Enviar PDF a Notary (con cert_file si disponible)
    3. Si responde FULLPAGE (400):
       - Agregar página de firma (con marcador "end-text")
       - Reintentar con PDF aumentado
    4. Retornar PDF firmado

    Args:
        pdf_bytes: Contenido binario del PDF a firmar
        signer_name: Nombre completo del firmante
        signer_seal: Sello del firmante
        signer_department: Departamento
        signer_municipality: Municipalidad
        official_number: Número oficial del documento
        city: Ciudad para el sello (default: "LATAM")
        stamp_position: Posición del sello
        tenant_id: ID del tenant para certificado PAdES
        schema_name: Schema del tenant (para resolver certificado de R2)

    Returns:
        bytes: PDF firmado por Notary

    Raises:
        Exception: Si Notary falla en ambos intentos
    """
    logger.info("Preparando llamada a Notary")
    logger.info(f"URL: {NOTARY_URL}/sign-pdf")
    logger.info(f"Firmante: {signer_name}")
    logger.info(f"Sello: {signer_seal}")
    logger.info(f"Departamento: {signer_department}")
    logger.info(f"Municipalidad: {signer_municipality}")
    logger.info(f"Número oficial: {official_number}")
    logger.info(f"Ciudad: {city}")
    logger.info(f"stamp_position: {stamp_position or '(default)'}")
    logger.info(f"tenant_id: {tenant_id or '(none)'}")
    logger.info(f"   Tamaño PDF: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

    # Intentar resolver certificado de R2 si tenemos tenant_id + schema_name
    cert_bytes = None
    cert_password = None

    if tenant_id and schema_name:
        try:
            from services.shared.certificate_resolver import resolve_certificate
            cert_bytes, cert_password = await resolve_certificate(
                tenant_id, schema_name=schema_name
            )
            logger.info(f"Certificado resuelto de R2 para {tenant_id} ({len(cert_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"No se pudo resolver certificado de R2 para {tenant_id}: {e}")
            logger.info("Continuando con tenant_id (Notary usará certificado local)")

    # Preparar multipart form-data
    files = {
        "pdf_file": ("document.pdf", pdf_bytes, "application/pdf"),
        "name": (None, signer_name),
        "seal": (None, signer_seal),
        "department": (None, signer_department),
        "entity": (None, signer_municipality),
        "document_number": (None, official_number),
        "city": (None, city)
    }

    # Agregar stamp_position si está especificado (para documentos importados)
    if stamp_position:
        files["stamp_position"] = (None, stamp_position)

    # Agregar tenant_id para firma PAdES (criptográfica)
    if tenant_id:
        files["tenant_id"] = (None, tenant_id)

    # Agregar certificado si se resolvió de R2
    if cert_bytes and cert_password:
        files["cert_file"] = ("cert.p12", cert_bytes, "application/x-pkcs12")
        files["cert_password"] = (None, cert_password)

    headers = {
        "x-api-key": NOTARY_API_KEY
    }

    try:
        # PRIMER INTENTO: Firmar PDF original
        logger.info("Intento 1/2: Enviando PDF original a Notary...")

        async with httpx.AsyncClient(timeout=NOTARY_TIMEOUT) as client:
            response = await client.post(
                f"{NOTARY_URL}/sign-pdf",
                files=files,
                headers=headers
            )

        # Si es exitoso (200), retornar PDF firmado
        if response.status_code == 200:
            signed_pdf = response.content
            signature_type = response.headers.get("X-Signature-Type", "unknown")
            logger.info("PDF firmado exitosamente en intento 1")
            logger.info(f"Tipo de firma: {signature_type}")
            logger.info(f"Tamaño firmado: {len(signed_pdf)} bytes ({len(signed_pdf)/1024:.2f} KB)")
            return signed_pdf

        # Detectar FULLPAGE
        if response.status_code == 400:
            response_text = response.text.upper()
            response_json = None

            # Intentar parsear JSON si es posible
            try:
                response_json = response.json()
            except Exception:
                pass

            # Detectar FULLPAGE en texto o JSON
            is_fullpage = (
                "FULLPAGE" in response_text or
                (response_json and "FULLPAGE" in str(response_json).upper())
            )

            if is_fullpage:
                logger.warning("Notary respondió FULLPAGE (PDF sin espacio para firma)")
                logger.info("Agregando página de firma con marcador 'end-text' y reintentando...")

                # AGREGAR SIGNPAGE.PDF CON MARCADOR "end-text"
                try:
                    augmented_pdf = add_blank_page_to_pdf(pdf_bytes)
                except Exception as pdf_error:
                    logger.error(f"Error agregando página de firma: {pdf_error}")
                    raise Exception(f"Error manipulando PDF para FULLPAGE: {str(pdf_error)}")

                # SEGUNDO INTENTO: Firmar PDF aumentado
                logger.info("Intento 2/2: Enviando PDF aumentado a Notary...")

                files_retry = {
                    "pdf_file": ("document.pdf", augmented_pdf, "application/pdf"),
                    "name": (None, signer_name),
                    "seal": (None, signer_seal),
                    "department": (None, signer_department),
                    "entity": (None, signer_municipality),
                    "document_number": (None, official_number),
                    "city": (None, city)
                }

                # Agregar stamp_position si está especificado
                if stamp_position:
                    files_retry["stamp_position"] = (None, stamp_position)

                # Agregar tenant_id para firma PAdES
                if tenant_id:
                    files_retry["tenant_id"] = (None, tenant_id)

                # Agregar certificado si se resolvió de R2
                if cert_bytes and cert_password:
                    files_retry["cert_file"] = ("cert.p12", cert_bytes, "application/x-pkcs12")
                    files_retry["cert_password"] = (None, cert_password)

                async with httpx.AsyncClient(timeout=NOTARY_TIMEOUT) as client:
                    response_retry = await client.post(
                        f"{NOTARY_URL}/sign-pdf",
                        files=files_retry,
                        headers=headers
                    )

                if response_retry.status_code == 200:
                    signed_pdf = response_retry.content
                    signature_type = response_retry.headers.get("X-Signature-Type", "unknown")
                    logger.info("PDF firmado exitosamente en intento 2 (después de FULLPAGE)")
                    logger.info(f"   Tipo de firma: {signature_type}")
                    logger.info(f"   Tamaño firmado: {len(signed_pdf)} bytes ({len(signed_pdf)/1024:.2f} KB)")
                    return signed_pdf
                else:
                    # Segundo intento también falló
                    error_msg = f"Notary falló en segundo intento (después de FULLPAGE): {response_retry.status_code}"
                    logger.info(f" [ERR] {error_msg}")
                    logger.info(f"   Response: {response_retry.text[:500]}")
                    raise Exception(error_msg)

            else:
                # Error 400 pero NO es FULLPAGE
                error_msg = f"Notary respondió 400 (no FULLPAGE): {response.text[:500]}"
                logger.info(f" [ERR] {error_msg}")
                raise Exception(error_msg)

        else:
            # Otro código de error
            error_msg = f"Notary respondió {response.status_code}: {response.text[:500]}"
            logger.info(f" [ERR] {error_msg}")
            raise Exception(error_msg)

    except httpx.TimeoutException as e:
        error_msg = f"Timeout llamando a Notary (>{NOTARY_TIMEOUT}s): {str(e)}"
        logger.info(f" [ERR] {error_msg}")
        raise Exception(error_msg)

    except httpx.RequestError as e:
        error_msg = f"Error de conexión con Notary: {str(e)}"
        logger.info(f" [ERR] {error_msg}")
        raise Exception(error_msg)

    except Exception as e:
        # Re-raise si ya es una excepción que generamos nosotros
        if "Notary" in str(e) or "FULLPAGE" in str(e):
            raise

        # Otras excepciones inesperadas
        error_msg = f"Error inesperado llamando a Notary: {type(e).__name__} - {str(e)}"
        logger.info(f" [ERR] {error_msg}")
        raise Exception(error_msg)
