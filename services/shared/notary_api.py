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

import hashlib
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
    schema_name: str = None,
    expected_sha256: str | None = None
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
        expected_sha256: Hash SHA-256 esperado del PDF (SU-009). Si es None
            se calcula localmente sobre pdf_bytes. Siempre se envía a Notary
            como campo expected_sha256 en el multipart.

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

    # SU-009: Calcular SHA-256 local sobre los bytes que efectivamente se envían a Notary.
    # Esto cubre el tramo Backend→Notary con independencia de la fuente del PDF
    # (PDFComposer, R2, etc.). Si el caller ya calculó y pasó expected_sha256, se usa
    # ese valor directamente; de lo contrario se calcula aquí.
    _pdf_sha256 = expected_sha256 if expected_sha256 else hashlib.sha256(pdf_bytes).hexdigest()
    logger.info(f"[SU-009] SHA-256 para Notary: {_pdf_sha256}")

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
        "city": (None, city),
        # SU-009: hash SHA-256 para que Notary verifique integridad antes de firmar
        "expected_sha256": (None, _pdf_sha256)
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

    # Agregar HMAC inter-servicio si está configurado (Fase 1 NuevaFIRMAfull).
    # Se firma el PDF original como body representativo de la request.
    # Si el secreto no está configurado, build_internal_hmac_header devuelve ""
    # y no se agrega el header (backward compatible).
    try:
        from services.notary_internal_hmac import build_internal_hmac_header
        hmac_value = build_internal_hmac_header(
            method="POST",
            path="/sign-pdf",
            body_bytes=pdf_bytes,
        )
        if hmac_value:
            headers["X-Internal-Sign"] = hmac_value
    except Exception as _hmac_err:
        # Soft-fail: no interrumpir la firma si el HMAC falla
        logger.warning(f"No se pudo generar X-Internal-Sign (soft-fail): {_hmac_err}")

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

                # SU-009: recalcular SHA-256 sobre el PDF aumentado (diferente al original)
                _augmented_sha256 = hashlib.sha256(augmented_pdf).hexdigest()
                logger.info(f"[SU-009] SHA-256 PDF aumentado (FULLPAGE retry): {_augmented_sha256}")

                files_retry = {
                    "pdf_file": ("document.pdf", augmented_pdf, "application/pdf"),
                    "name": (None, signer_name),
                    "seal": (None, signer_seal),
                    "department": (None, signer_department),
                    "entity": (None, signer_municipality),
                    "document_number": (None, official_number),
                    "city": (None, city),
                    # SU-009: hash del PDF aumentado
                    "expected_sha256": (None, _augmented_sha256)
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


async def call_notary_verify(pdf_bytes: bytes) -> dict:
    """
    Llama a Notary /sign-pdf/verify para validar integridad del PDF firmado por AutoFirma.

    Returns:
        {ok, failure_reason, signature_count, signature_visible, modification_level}

    Nunca lanza excepción — en caso de error de red devuelve ok=False con failure_reason.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{NOTARY_URL}/sign-pdf/verify",
                files={"pdf_file": ("signed.pdf", pdf_bytes, "application/pdf")},
                headers={"x-api-key": NOTARY_API_KEY},
            )
        if response.status_code == 200:
            return response.json()
        return {
            "ok": False,
            "failure_reason": f"notary_verify_http_{response.status_code}",
            "signature_count": 0,
            "signature_visible": False,
            "modification_level": None,
        }
    except Exception as e:
        logger.warning(f"call_notary_verify error (soft): {e}")
        return {
            "ok": False,
            "failure_reason": f"notary_verify_unreachable",
            "signature_count": 0,
            "signature_visible": False,
            "modification_level": None,
        }


async def call_notary_stamp_only(
    pdf_bytes: bytes,
    official_number: str,
    city: str = "LATAM",
    stamp_position: str = "first",
    existing_count: int | None = None,
) -> tuple[bytes, float, float, float, float]:
    """
    Llama a Notary /stamp-number: estampa número/fecha y devuelve posición de firma.

    Usado en el flujo digital (AutoFirma) para:
    1. Aplicar el sello de número/fecha (igual que en flujo electrónico).
    2. Calcular la posición donde AutoFirma debe insertar la firma PAdES.

    Returns:
        (stamped_pdf_bytes, sig_llx, sig_lly, sig_urx, sig_ury)
        Las coordenadas son en espacio PDF (origin bottom-left), listas para
        usar directamente en las propiedades de AutoFirma.

    Raises:
        Exception: Si Notary falla.
    """
    import base64

    files = {
        "pdf_file": ("document.pdf", pdf_bytes, "application/pdf"),
        "document_number": (None, official_number),
        "city": (None, city),
        "stamp_position": (None, stamp_position),
    }
    if existing_count is not None:
        files["existing_count"] = (None, str(existing_count))

    headers = {"x-api-key": NOTARY_API_KEY}

    async with httpx.AsyncClient(timeout=NOTARY_TIMEOUT) as client:
        response = await client.post(
            f"{NOTARY_URL}/stamp-number",
            files=files,
            headers=headers,
        )

    if response.status_code != 200:
        raise Exception(f"Notary /stamp-number falló {response.status_code}: {response.text[:300]}")

    data = response.json()
    stamped_pdf = base64.b64decode(data["stamped_pdf_b64"])
    return (
        stamped_pdf,
        float(data["sig_llx"]),
        float(data["sig_lly"]),
        float(data["sig_urx"]),
        float(data["sig_ury"]),
    )
