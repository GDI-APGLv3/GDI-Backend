"""
Notary - Servicio de Firma Visual de PDFs

Este módulo implementa un servicio web FastAPI para la firma visual de documentos PDF.
El servicio utiliza un sistema de firma visual para posicionar automáticamente
las firmas en documentos PDF con formato Letter (612x792 puntos).

Características principales:
- Posicionamiento automático de firmas basado en detección de texto "end-text"
- Layout inteligente de 2 columnas para múltiples firmas
- Estampado opcional con número de documento, ciudad y fecha
- Validación de formato PDF y parámetros de entrada
- API REST con autenticación por API key
- Nomenclatura inteligente de archivos de salida

Autor: Sistema de Firma Visual
Versión: 2.0.0
"""
from fastapi import FastAPI, File, UploadFile, Form, Depends, HTTPException
from fastapi.responses import Response
from typing import Optional
import asyncio
import hashlib
import logging
import traceback

from .auth import validate_api_key
from .validators import (
    validate_pdf_format, validate_signature_params,
    validate_stamp_params, validate_stamp_position, sanitize_filename,
    validate_tenant_id
)
from .signature_inserter import sign_pdf_document, get_signature_info, SignatureError
from .document_stamper import stamp_document, StampError
from .layout import LayoutError
from .config import SERVICE_NAME, SERVICE_VERSION, FALLBACK_TO_VISUAL
from .certificate_loader import (
    certificate_exists, load_certificate, load_certificate_from_bytes,
    get_certificate_info, list_available_certificates,
    CertificateError, CertificateNotFoundError
)
from .pades_signer import (
    sign_pdf_combined,
    get_pades_signature_info, PAdESSigningError, PAdESTsaUnavailableError
)

# Configurar logging para el servicio
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Crear la aplicación FastAPI con metadatos completos
app = FastAPI(
    title="Notary - Visual PDF Signing Service",
    description="Servicio de firma visual de documentos PDF con layout automático",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

@app.get("/health")
async def health_check():
    """
    Endpoint de verificación de salud del servicio

    Verifica el estado general del servicio y la disponibilidad del sistema
    de firma visual y PAdES.

    Returns:
        dict: Estado del servicio con información del sistema de firma:
            - status: Estado general ("healthy" o "unhealthy")
            - service: Nombre del servicio
            - version: Versión del servicio
            - signature_system: Información del sistema de firma visual
            - pades_system: Información del sistema de firma PAdES
            - available_certificates: Lista de certificados disponibles
    """
    signature_info = get_signature_info()
    pades_info = get_pades_signature_info()
    available_certs = list_available_certificates()

    return {
        "status": "healthy",
        "service": "Notary",
        "version": "2.1.0",
        "signature_system": signature_info,
        "pades_system": pades_info,
        "available_certificates": available_certs,
        "fallback_to_visual": FALLBACK_TO_VISUAL
    }

@app.post("/sign-pdf")
async def sign_pdf(
    pdf_file: UploadFile = File(..., description="PDF file to sign (Letter format only)"),
    name: str = Form(..., description="Signer name (1-100 characters)"),
    seal: str = Form(..., description="Seal (1-50 characters)"),
    department: str = Form(..., description="Department (1-100 characters)"),
    entity: str = Form(..., description="Entity (1-100 characters)"),
    document_number: Optional[str] = Form(None, description="Document number (max 40 characters, optional)"),
    city: Optional[str] = Form(None, description="City (required if document_number provided)"),
    stamp_position: Optional[str] = Form(None, description="Stamp position: 'first' (default) or 'last'"),
    tenant_id: Optional[str] = Form(None, description="Tenant ID for PAdES certificate lookup"),
    use_pades: Optional[str] = Form("true", description="Use PAdES digital signature if certificate available (true/false)"),
    cert_file: Optional[UploadFile] = File(None, description="Optional .p12 certificate bytes (from Backend)"),
    cert_password: Optional[str] = Form(None, description="Optional certificate password (from Backend)"),
    expected_sha256: Optional[str] = Form(None, description="SHA-256 hex digest esperado del PDF (opcional). Si se envía, se verifica integridad antes de firmar."),
    api_key: str = Depends(validate_api_key)
):
    """
    Firma documentos PDF con firma PAdES digital o visual, usando layout automático de 2 columnas.

    Aplica una firma en la última página del PDF, calculando la posición
    automáticamente basándose en el texto "end-text" y firmas existentes.
    Opcionalmente aplica estampado con número de documento en la página indicada.

    Modos de firma:
        - PAdES (digital): Si tenant_id presente y existe certificado .p12
        - Visual (actual): Si no hay certificado o use_pades=false

    Proceso:
        1. Valida archivo PDF (max 10MB, signatura %PDF válida)
        2. Valida parámetros de firma (no vacíos, dentro de límites)
        3. Valida parámetros de estampado si aplica (city requerido con document_number)
        4. Valida stamp_position si se proporciona (debe ser 'first' o 'last')
        5. Si tenant_id presente y use_pades=true:
           a. Busca certificado en certs/{tenant_id}.p12
           b. Si existe: firma PAdES-B-T con timestamp
           c. Si no existe y FALLBACK_TO_VISUAL=true: firma visual
           d. Si no existe y FALLBACK_TO_VISUAL=false: error 400
        6. Detecta firmas existentes buscando "Digitally Signed by"
        7. Calcula posición: 100pts debajo de "end-text", layout 2 columnas
        8. Aplica estampado en página indicada (si document_number y city presentes)
        9. Inserta firma (PAdES o visual) en última página
        10. Genera nombre archivo: {document_number}.pdf o nombre original

    Args:
        pdf_file: Archivo PDF a firmar. Max 10MB, debe contener "end-text" en última página.
        name: Nombre del firmante (1-100 caracteres, no vacío).
        seal: Cargo o sello del firmante (1-50 caracteres, no vacío).
        department: Departamento del firmante (1-100 caracteres, no vacío).
        entity: Entidad del firmante (1-100 caracteres, no vacío).
        document_number: Número para estampado (max 40 chars). Controla nombre del archivo.
        city: Ciudad para estampado (max 50 chars). Requerido si document_number presente.
        stamp_position: Posición del estampado ('first' o 'last'). Default: 'first'.
        tenant_id: ID del tenant para buscar certificado .p12 (opcional).
        use_pades: Usar firma PAdES si hay certificado. Default: true.

    Returns:
        Response: PDF firmado como descarga (application/pdf).
            Headers incluyen X-Signature-Type: "pades" o "visual"

    Raises:
        HTTPException 400:
            - FILE_TOO_LARGE: PDF excede 10MB
            - INVALID_PDF_FILE: No se puede leer el archivo
            - INVALID_PDF_FORMAT: No comienza con %PDF
            - INVALID_PARAMETERS: Parámetros de firma inválidos
            - INVALID_STAMP_PARAMETERS: document_number sin city
            - INVALID_STAMP_POSITION: stamp_position no es 'first' ni 'last'
            - CERTIFICATE_NOT_FOUND: tenant_id sin certificado y FALLBACK_TO_VISUAL=false
            - LayoutError: No se encontró "end-text" o FULLPAGE (sin espacio)
        HTTPException 401: API key inválida o faltante
        HTTPException 500: Error interno durante firma

    Requisitos del PDF:
        - DEBE contener texto "end-text" en la última página
        - Debe tener espacio suficiente para firmas (Y >= 100pts del borde inferior)
    """
    # Inicializar antes del try para que el finally no lance UnboundLocalError
    # si una excepción ocurre antes de la asignación real (ej. validate_pdf_format).
    loaded_cert = None

    try:
        # Validar tenant_id si se proporcionó (previene path traversal)
        if tenant_id:
            validate_tenant_id(tenant_id)

        # Log del inicio del proceso de firma
        logger.info(f"Iniciando proceso de firma para: {name}")
        if tenant_id:
            logger.info(f"  - Tenant ID: {tenant_id}")
            logger.info(f"  - Use PAdES: {use_pades}")

        # Validar formato del PDF (debe ser Letter) - pasar el objeto UploadFile
        pdf_content = validate_pdf_format(pdf_file)

        # Verificar integridad del PDF si el Backend envió un hash esperado (SU-009)
        if expected_sha256:
            calculated_sha256 = hashlib.sha256(pdf_content).hexdigest()
            if calculated_sha256.lower() != expected_sha256.lower():
                logger.warning(
                    f"PDF_INTEGRITY_FAILED: hash esperado={expected_sha256[:8]}..., "
                    f"calculado={calculated_sha256[:8]}... — rechazando firma"
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error_code": "PDF_INTEGRITY_FAILED",
                        "detail": "El hash del PDF recibido no coincide con el esperado"
                    }
                )
            logger.info(
                f"Integridad del PDF verificada OK (sha256={calculated_sha256[:8]}...)"
            )

        # Validar parámetros de la firma
        validate_signature_params(name, seal, department, entity)

        # Validar parámetros de estampado si se proporcionan
        if document_number or city:
            validate_stamp_params(document_number, city)

        # Validar posición del estampado si se proporciona
        if stamp_position:
            validate_stamp_position(stamp_position)

        # Determinar modo de firma (PAdES o visual)
        use_pades_signature = False

        # Convertir use_pades de string a boolean (FastAPI Form envía strings)
        use_pades_bool = use_pades.lower() in ("true", "1", "yes") if use_pades else True

        logger.info(f"DEBUG: tenant_id={tenant_id}, use_pades={use_pades_bool}, cert_file={'SI' if cert_file and cert_file.filename else 'NO'}")

        # Modo 1: Certificado enviado por Backend (R2 → multipart)
        if cert_file and cert_file.filename and cert_password and use_pades_bool:
            try:
                cert_bytes = await cert_file.read()
                loaded_cert = load_certificate_from_bytes(
                    cert_bytes, cert_password, tenant_id=tenant_id or ""
                )
                use_pades_signature = True
                logger.info(f"Certificado recibido via multipart para tenant '{tenant_id}'")
            except CertificateError as e:
                logger.warning(f"Error al cargar certificado desde multipart: {e}")
                if not FALLBACK_TO_VISUAL:
                    raise HTTPException(
                        status_code=400,
                        detail=f"CERTIFICATE_LOAD_ERROR: {str(e)}"
                    )
                logger.info("Fallback a firma visual activado")

        # Modo 2 deshabilitado - certificados solo via Modo 1 (R2 → multipart)
        elif tenant_id and use_pades_bool:
            logger.warning(f"No se recibio certificado via multipart para tenant '{tenant_id}'")
            if not FALLBACK_TO_VISUAL:
                raise HTTPException(
                    status_code=400,
                    detail=f"CERTIFICATE_NOT_PROVIDED: Backend debe enviar certificado via multipart para tenant '{tenant_id}'"
                )
            logger.info("Fallback a firma visual activado")

        # Procesar la firma digital del documento
        from .layout import calculate_signature_position, count_existing_signatures

        # Calcular posición de la firma
        existing_signatures = count_existing_signatures(pdf_content)
        x, y = calculate_signature_position(existing_signatures, pdf_content)

        # Preparar parámetros de la firma
        signature_params = {
            "name": name,
            "seal": seal,
            "department": department,
            "entity": entity
        }

        # Agregar parámetros opcionales si están presentes
        if document_number:
            signature_params["document_number"] = document_number
        if city:
            signature_params["city"] = city

        # Aplicar estampado si se proporcionan document_number y city.
        # stamp_document usa pypdf + ReportLab (CPU/IO bound) → se corre en un
        # thread separado para no bloquear el event loop de asyncio.
        if document_number and city:
            position = stamp_position or "first"
            pdf_content = await asyncio.to_thread(
                stamp_document, pdf_content, document_number, city, position
            )
            logger.info(f"Estampado aplicado con número: {document_number}, ciudad: {city}, posición: {position}")

        # Firmar el documento (Combinada: Visual + PAdES, o solo Visual)
        signature_type = "visual"

        if use_pades_signature and loaded_cert:
            # Firma combinada: Visual (ReportLab elaborado) + PAdES (criptográfica clickeable)
            try:
                signed_pdf = await sign_pdf_combined(
                    pdf_content=pdf_content,
                    cert=loaded_cert,
                    signature_params=signature_params,
                    x=x,
                    y=y,
                    existing_signature_count=existing_signatures,
                )
                signature_type = "pades"
                logger.info(f"Firma combinada (Visual + PAdES) completada para: {name}")
            except PAdESTsaUnavailableError as e:
                # Circuit breaker abierto: TSA caído de forma sostenida.
                # Se devuelve 503 independientemente de FALLBACK_TO_VISUAL para
                # que el Backend pueda distinguir "TSA caído" de "error interno".
                logger.error(f"TSA no disponible (circuit breaker): {e}")
                if not FALLBACK_TO_VISUAL:
                    raise HTTPException(
                        status_code=503,
                        detail=f"TSA_UNAVAILABLE: {str(e)}"
                    )
                logger.info("Fallback a firma visual por TSA no disponible")
                signed_pdf = await asyncio.to_thread(
                    sign_pdf_document,
                    pdf_content,
                    signature_params,
                    x,
                    y,
                )
            except PAdESSigningError as e:
                logger.error(f"Error en firma PAdES: {e}")
                if not FALLBACK_TO_VISUAL:
                    raise HTTPException(status_code=500, detail=f"PADES_ERROR: {str(e)}")
                logger.info("Fallback a firma visual por error en PAdES")
                signed_pdf = await asyncio.to_thread(
                    sign_pdf_document,
                    pdf_content,
                    signature_params,
                    x,
                    y,
                )
        else:
            # Firma visual (comportamiento original)
            signed_pdf = await asyncio.to_thread(
                sign_pdf_document,
                pdf_content,
                signature_params,
                x,
                y,
            )

        # Generar nombre de archivo según la lógica solicitada
        if document_number:
            # Si se incluye número, el nombre debe ser numero.pdf
            safe_filename = sanitize_filename(f"{document_number}.pdf")
        else:
            # Si NO se incluye número, mantener el nombre original
            safe_filename = sanitize_filename(pdf_file.filename)

        logger.info(f"Firma completada exitosamente para: {name} (tipo: {signature_type})")

        # Retornar el PDF firmado
        return Response(
            content=signed_pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={safe_filename}",
                "Content-Type": "application/pdf",
                "X-Signature-Type": signature_type
            }
        )

    except (ValueError, SignatureError, LayoutError) as e:
        # Errores de validación o procesamiento
        logger.warning(f"Error de validación en firma: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    except HTTPException:
        # Re-lanzar HTTPExceptions (validaciones de parámetros)
        raise

    except Exception as e:
        # Errores internos del servidor
        logger.error(f"Error interno en firma: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail="Error interno del servidor durante el proceso de firma"
        )

    finally:
        # Limpiar temp file si se creó desde bytes
        if loaded_cert and getattr(loaded_cert, '_temp_file', None):
            try:
                import os as _os
                _os.unlink(loaded_cert._temp_file)
                logger.debug(f"Temp cert file eliminado: {loaded_cert._temp_file}")
            except OSError:
                pass



@app.post("/stamp-number")
async def stamp_number(
    pdf_file: UploadFile = File(..., description="PDF a estampar"),
    document_number: Optional[str] = Form(None, description="Número de documento (opcional — si se omite, solo calcula posición sin estampar)"),
    city: Optional[str] = Form(None, description="Ciudad para el sello (requerido si document_number presente)"),
    stamp_position: Optional[str] = Form(None, description="'first' (default) o 'last'"),
    existing_count: Optional[int] = Form(None, description="Número de firmas ya completadas (override de auto-detección)"),
    api_key: str = Depends(validate_api_key)
):
    """
    Estampa número/fecha en el PDF y devuelve el PDF estampado + posición para AutoFirma.

    Usado en el flujo de firma digital con AutoFirma: el Backend llama este endpoint
    para obtener el PDF listo con el sello y las coordenadas donde AutoFirma debe insertar
    la firma criptográfica.

    Returns:
        JSON con:
        - stamped_pdf_b64: PDF estampado en base64
        - sig_llx: LowerLeftX de la firma (coordenadas PDF)
        - sig_lly: LowerLeftY de la firma (coordenadas PDF)
        - sig_urx: UpperRightX de la firma (coordenadas PDF)
        - sig_ury: UpperRightY de la firma (coordenadas PDF)
    """
    import base64
    from .layout import calculate_signature_position, count_existing_signatures
    from .config import SIGNATURE_WIDTH, SIGNATURE_HEIGHT

    try:
        pdf_content = validate_pdf_format(pdf_file)

        if stamp_position:
            validate_stamp_position(stamp_position)

        # Solo estampar si hay número Y ciudad; si no, solo calcular posición.
        # stamp_document es CPU/IO bound → se corre en thread para no bloquear asyncio.
        if document_number and city:
            position = stamp_position or "first"
            stamped_pdf = await asyncio.to_thread(
                stamp_document, pdf_content, document_number, city, position
            )
        else:
            stamped_pdf = pdf_content

        if existing_count is not None:
            existing = existing_count
            logger.info(f"stamp_number existing_count override={existing_count}")
        else:
            existing = count_existing_signatures(stamped_pdf)
        x, y = calculate_signature_position(existing, stamped_pdf)

        return {
            "stamped_pdf_b64": base64.b64encode(stamped_pdf).decode(),
            "sig_llx": round(x, 2),
            "sig_lly": round(y, 2),
            "sig_urx": round(x + SIGNATURE_WIDTH, 2),
            "sig_ury": round(y + SIGNATURE_HEIGHT, 2),
        }

    except (ValueError, LayoutError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"stamp_number error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Error interno durante el estampado")


@app.get("/certificate/{tenant_id}")
async def get_certificate_status(
    tenant_id: str,
    api_key: str = Depends(validate_api_key)
):
    """
    Obtiene información sobre el certificado de un tenant.

    Args:
        tenant_id: ID del tenant

    Returns:
        dict: Información del certificado (existe, validez, fechas, etc.)
    """
    validate_tenant_id(tenant_id)
    return get_certificate_info(tenant_id)


@app.post("/sign-pdf/verify")
async def verify_pdf_signature(
    pdf_file: UploadFile = File(..., description="PDF to verify"),
    api_key: str = Depends(validate_api_key)
):
    """
    Verifica las firmas PAdES de un PDF.

    Returns:
        dict: {ok, failure_reason, signature_count, signature_visible, modification_level}
    """
    try:
        pdf_bytes = await pdf_file.read()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="EMPTY_FILE")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"verify_pdf_signature read error: {e}")
        raise HTTPException(status_code=400, detail="No se pudo procesar el PDF. Verificá que el archivo sea válido.")

    # Validar tamaño: límite 50MB para verify
    MAX_VERIFY_SIZE_MB = 50
    if len(pdf_bytes) > MAX_VERIFY_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"PDF file too large. Maximum size for verify: {MAX_VERIFY_SIZE_MB}MB",
            headers={"error_code": "FILE_TOO_LARGE"}
        )

    # Validar que sea un PDF válido
    if not pdf_bytes.startswith(b'%PDF'):
        raise HTTPException(
            status_code=400,
            detail="No se pudo procesar el PDF. Verificá que el archivo sea válido.",
            headers={"error_code": "INVALID_PDF_FORMAT"}
        )

    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from io import BytesIO

        reader = PdfFileReader(BytesIO(pdf_bytes))
        sig_fields = reader.embedded_signatures
        if not sig_fields:
            return {
                "ok": False,
                "failure_reason": "no_signatures_found",
                "signature_count": 0,
                "signature_visible": False,
                "modification_level": None,
            }

        results = []
        for sig in sig_fields:
            try:
                status = validate_pdf_signature(sig)
                rect = getattr(sig.sig_field, "Rect", None)
                visible = bool(
                    rect and not all(float(v) == 0.0 for v in rect)
                )
                results.append({
                    "intact": status.intact,
                    "valid": status.valid,
                    "modification_level": str(status.modification_level) if status.modification_level else None,
                    "signature_visible": visible,
                })
            except Exception as sig_err:
                results.append({
                    "intact": False,
                    "valid": False,
                    "modification_level": None,
                    "signature_visible": False,
                    "error": str(sig_err),
                })

        all_valid = all(r.get("valid") and r.get("intact") for r in results)
        mod_level = results[0].get("modification_level") if results else None
        visible = any(r.get("signature_visible") for r in results)

        return {
            "ok": all_valid,
            "failure_reason": None if all_valid else "invalid_or_tampered_signature",
            "signature_count": len(results),
            "signature_visible": visible,
            "modification_level": mod_level,
            "signatures": results,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"verify_pdf_signature error: {e}")
        raise HTTPException(status_code=500, detail="Error al verificar la firma digital.")


@app.get("/certificates")
async def list_certificates(
    api_key: str = Depends(validate_api_key)
):
    """
    Lista todos los certificados disponibles.

    Returns:
        dict: Lista de tenant_ids con certificados disponibles
    """
    certs = list_available_certificates()
    return {
        "count": len(certs),
        "certificates": certs
    }


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """
    Manejador global de excepciones para capturar errores no controlados
    
    Args:
        request: Objeto de solicitud HTTP
        exc: Excepción capturada
        
    Returns:
        HTTPException: Respuesta de error estandarizada
    """
    logger.error(f"Excepción no controlada: {str(exc)}")
    logger.error(f"Traceback: {traceback.format_exc()}")
    
    return HTTPException(
        status_code=500,
        detail="Error interno del servidor"
    )

# Punto de entrada para ejecución directa del servidor
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )