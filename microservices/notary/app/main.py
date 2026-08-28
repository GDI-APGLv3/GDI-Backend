from fastapi import FastAPI, File, UploadFile, Form, Depends, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from typing import Optional
import asyncio
import hashlib
import logging
import os
import traceback

from .auth import validate_api_key
from .internal_hmac import validate_internal_hmac
from .config import MAX_SIGNABLE_PDF_SIZE, MAX_SIGNABLE_PDF_SIZE_MB
from .validators import (
    validate_pdf_format, validate_signature_params,
    validate_stamp_params, validate_stamp_position, sanitize_filename,
    validate_tenant_id
)
from .signature_inserter import sign_pdf_document, get_signature_info, SignatureError
from .document_stamper import stamp_document, StampError
from .layout import LayoutError
from .config import (
    SERVICE_NAME, FALLBACK_TO_VISUAL,
    REQUIRE_EXPECTED_SHA256, MAX_REQUEST_BODY_SIZE,
)
from .certificate_loader import (
    certificate_exists, load_certificate, load_certificate_from_bytes,
    get_certificate_info, list_available_certificates,
    CertificateError, CertificateNotFoundError
)
from .pades_signer import (
    sign_pdf_combined,
    async_add_document_timestamp,
    count_pades_signatures,
    count_pades_timestamps,
    get_tsa_breaker_state,
    get_pades_signature_info,
    PAdESSigningError, PAdESTimestampError, PAdESTsaUnavailableError,
)
from app.error_alerts import report_error
from .version import VERSION, GIT_SHA

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RequestBodyTooLargeError(Exception):
    pass


class MaxBodySizeMiddleware:

    def __init__(self, app, max_body_size: int):
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > self.max_body_size:
                response = JSONResponse(
                    {
                        "detail": (
                            f"Request body too large. Maximum: "
                            f"{self.max_body_size} bytes"
                        ),
                        "error_code": "REQUEST_BODY_TOO_LARGE",
                    },
                    status_code=413,
                )
                await response(scope, receive, send)
                return

        max_body_size = self.max_body_size
        total_read = 0

        async def limited_receive():
            nonlocal total_read
            message = await receive()
            if message["type"] == "http.request":
                total_read += len(message.get("body", b""))
                if total_read > max_body_size:
                    raise RequestBodyTooLargeError(
                        f"Request body too large. Maximum: {max_body_size} bytes"
                    )
            return message

        await self.app(scope, limited_receive, send)


app = FastAPI(
    title="Notary - Visual PDF Signing Service",
    description="Servicio de firma visual de documentos PDF con layout automático",
    version=VERSION,
    docs_url="/docs",
    redoc_url="/redoc"
)


@app.exception_handler(RequestBodyTooLargeError)
async def _handle_request_body_too_large(request, exc: RequestBodyTooLargeError):
    logger.warning(f"notary.request_body_too_large path={request.url.path}: {exc}")
    return JSONResponse(
        {"detail": str(exc), "error_code": "REQUEST_BODY_TOO_LARGE"},
        status_code=413,
    )


app.add_middleware(MaxBodySizeMiddleware, max_body_size=MAX_REQUEST_BODY_SIZE)

if os.getenv("ENABLE_DEBUG_BOOM") == "1":
    @app.get("/_debug/boom", include_in_schema=False)
    async def _debug_boom(api_key: str = Depends(validate_api_key)):
        raise RuntimeError("Error de prueba (DIY error-mail): validacion del pipeline de alertas")

@app.get("/health")
async def health_check():
    """
    T-3.4 — Health real de Notary.

    Responde la pregunta "¿puede este Notary firmar un PDF ahora?".
    El sello de tiempo (TSA) es diferible (B-B es válido por Ley 25.506),
    así que TSA caído NO da 503 — solo degrada el status a "degraded".

    Lógica de status:
        "healthy"  → pipeline de firma operativo Y breaker TSA CLOSED.
        "degraded" → pipeline de firma operativo PERO breaker TSA OPEN/HALF_OPEN
                     (las firmas salen en B-B; el sello se agrega después vía /timestamp-pdf).
        "unhealthy"→ pipeline de firma roto (503). No se puede firmar en absoluto.

    Chequeos realizados (todos < 500ms, ninguno llama al TSA):
        1. signing_infrastructure: intenta crear un canvas ReportLab en memoria
           y verificar que pyHanko + pypdf son importables. Cubre el pipeline
           visual y el pipeline PAdES (sin necesitar un certificado concreto,
           que llega vía multipart en cada request de firma).
        2. tsa_breaker: lee el estado en memoria del TsaCircuitBreaker
           (closed / open / half_open). Lectura instantánea, sin red.

    Returns (HTTP 200 para healthy/degraded, HTTP 503 para unhealthy):
        {
          "status": "healthy" | "degraded" | "unhealthy",
          "service": "Notary",
          "version": "<VERSION>",
          "commit": "<GIT_SHA>",
          "can_sign_bb": true | false,
          "tsa_breaker": "closed" | "open" | "half_open",
          "checks": {
            "signing_infrastructure": "ok" | "error: <motivo>",
            "tsa_timestamp": "ok" | "degraded" | "half_open"
          },
          "available_disk_certs": <int>,
          "fallback_to_visual": true | false
        }
    """
    signing_ok = True
    signing_error = None
    try:
        import io as _io
        from reportlab.pdfgen import canvas as _rl_canvas
        _buf = _io.BytesIO()
        _c = _rl_canvas.Canvas(_buf)
        _c.save()

        from pyhanko.pdf_utils.reader import PdfFileReader as _PdfReader
        _PdfReader(_io.BytesIO(_buf.getvalue()))

    except Exception as exc:
        signing_ok = False
        signing_error = str(exc)
        logger.warning(f"health_check: signing_infrastructure KO — {exc}")

    tsa_breaker = get_tsa_breaker_state()

    can_sign_bb = signing_ok
    if not can_sign_bb:
        status = "unhealthy"
    elif tsa_breaker != "closed":
        status = "degraded"
    else:
        status = "healthy"

    tsa_check = (
        "ok" if tsa_breaker == "closed"
        else ("half_open" if tsa_breaker == "half_open" else "degraded")
    )

    body = {
        "status": status,
        "service": "Notary",
        "version": VERSION,
        "commit": GIT_SHA,
        "can_sign_bb": can_sign_bb,
        "tsa_breaker": tsa_breaker,
        "checks": {
            "signing_infrastructure": "ok" if signing_ok else f"error: {signing_error}",
            "tsa_timestamp": tsa_check,
        },
        "available_disk_certs": len(list_available_certificates()),
        "fallback_to_visual": FALLBACK_TO_VISUAL,
    }

    if not signing_ok:
        logger.error(f"health_check: unhealthy — {signing_error}")

    http_status = 503 if not can_sign_bb else 200
    return JSONResponse(content=body, status_code=http_status)

@app.post("/sign-pdf")
async def sign_pdf(
    request: Request,
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
    defer_timestamp: Optional[str] = Form("false", description="N-1a: si 'true', firma PAdES-B-B (sin TSA, < 2s). Default 'false' = comportamiento actual B-T intacto."),
    api_key: str = Depends(validate_api_key),
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
        1. Valida archivo PDF (max 64MB, signatura %PDF válida)
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
        pdf_file: Archivo PDF a firmar. Max 64MB, debe contener "end-text" en última página.
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
            - FILE_TOO_LARGE: PDF excede 64MB
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
    loaded_cert = None

    try:
        if tenant_id:
            validate_tenant_id(tenant_id)

        logger.info(f"Iniciando proceso de firma para: {name}")
        if tenant_id:
            logger.info(f"  - Tenant ID: {tenant_id}")
            logger.info(f"  - Use PAdES: {use_pades}")

        pdf_content = validate_pdf_format(pdf_file)

        await validate_internal_hmac(request, body=pdf_content)

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
        elif REQUIRE_EXPECTED_SHA256:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "MISSING_EXPECTED_SHA256",
                    "detail": "expected_sha256 es obligatorio (REQUIRE_EXPECTED_SHA256=true)"
                }
            )
        else:
            logger.warning(f"notary.sha256_check_skipped endpoint=/sign-pdf name={name}")

        validate_signature_params(name, seal, department, entity)

        if document_number or city:
            validate_stamp_params(document_number, city)

        if stamp_position:
            validate_stamp_position(stamp_position)

        use_pades_signature = False

        use_pades_bool = use_pades.lower() in ("true", "1", "yes") if use_pades else True

        if not FALLBACK_TO_VISUAL and not use_pades_bool:
            raise HTTPException(
                status_code=400,
                detail=(
                    "USE_PADES_FALSE_NOT_ALLOWED: firma puramente visual "
                    "deshabilitada en este ambiente (FALLBACK_TO_VISUAL=false); "
                    "use_pades no puede ser false"
                ),
                headers={"error_code": "USE_PADES_FALSE_NOT_ALLOWED"},
            )

        defer_timestamp_bool = defer_timestamp.lower() in ("true", "1", "yes") if defer_timestamp else False
        if defer_timestamp_bool:
            logger.info("  - defer_timestamp=true: se firmará B-B (sin TSA)")

        logger.info(f"DEBUG: tenant_id={tenant_id}, use_pades={use_pades_bool}, cert_file={'SI' if cert_file and cert_file.filename else 'NO'}")

        if cert_file and cert_file.filename and cert_password and use_pades_bool:
            try:
                cert_bytes = await cert_file.read()
                loaded_cert = await asyncio.to_thread(
                    load_certificate_from_bytes,
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

        elif tenant_id and use_pades_bool:
            logger.warning(f"No se recibio certificado via multipart para tenant '{tenant_id}'")
            if not FALLBACK_TO_VISUAL:
                raise HTTPException(
                    status_code=400,
                    detail=f"CERTIFICATE_NOT_PROVIDED: Backend debe enviar certificado via multipart para tenant '{tenant_id}'"
                )
            logger.info("Fallback a firma visual activado")

        from .layout import calculate_signature_position, count_existing_signatures

        existing_signatures = count_existing_signatures(pdf_content)
        x, y = calculate_signature_position(existing_signatures, pdf_content)

        signature_params = {
            "name": name,
            "seal": seal,
            "department": department,
            "entity": entity
        }

        if document_number:
            signature_params["document_number"] = document_number
        if city:
            signature_params["city"] = city

        if document_number and city:
            position = stamp_position or "first"
            pdf_content = await asyncio.to_thread(
                stamp_document, pdf_content, document_number, city, position
            )
            logger.info(f"Estampado aplicado con número: {document_number}, ciudad: {city}, posición: {position}")

        signature_type = "visual"

        if use_pades_signature and loaded_cert:
            try:
                signed_pdf = await sign_pdf_combined(
                    pdf_content=pdf_content,
                    cert=loaded_cert,
                    signature_params=signature_params,
                    x=x,
                    y=y,
                    existing_signature_count=existing_signatures,
                    defer_timestamp=defer_timestamp_bool,
                )
                signature_type = "pades"
                logger.info(f"Firma combinada (Visual + PAdES) completada para: {name}")
            except PAdESTsaUnavailableError as e:
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
            signed_pdf = await asyncio.to_thread(
                sign_pdf_document,
                pdf_content,
                signature_params,
                x,
                y,
            )

        if document_number:
            safe_filename = sanitize_filename(f"{document_number}.pdf")
        else:
            safe_filename = sanitize_filename(pdf_file.filename)

        logger.info(f"Firma completada exitosamente para: {name} (tipo: {signature_type})")

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
        logger.warning(f"Error de validación en firma: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"Error interno en firma: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail="Error interno del servidor durante el proceso de firma"
        )

    finally:
        if loaded_cert and getattr(loaded_cert, '_temp_file', None):
            try:
                import os as _os
                _os.unlink(loaded_cert._temp_file)
                logger.debug(f"Temp cert file eliminado: {loaded_cert._temp_file}")
            except OSError:
                pass


@app.post("/timestamp-pdf")
async def timestamp_pdf(
    request: Request,
    pdf_file: UploadFile = File(..., description="PDF firmado en B-B al que se agrega el DocTimeStamp"),
    expected_sha256: Optional[str] = Form(None, description="M-1: SHA-256 hex (64 chars) del PDF esperado. Si se envía y no coincide → 409."),
    api_key: str = Depends(validate_api_key),
):
    """
    N-1b — Agrega un DocTimeStamp PAdES (B-T) a un PDF ya firmado en B-B.

    Operación incremental: no invalida la firma criptográfica existente.
    El caller (Backend/worker escri) es responsable de reintentar si el TSA
    falla; un B-B sin sello sigue siendo un documento firmado válido (Ley 25.506).

    Flujo de uso (firma diferida D13):
        1. Backend llama /sign-pdf con defer_timestamp=true → PDF B-B (< 2s).
        2. Backend sube el B-B a R2 (el documento ya está firmado y es válido).
        3. Backend encola job dts en signing_sessions (worker escri).
        4. Worker escri descarga el B-B, llama POST /timestamp-pdf → PDF B-T.
        5. Worker sobreescribe el key en R2 con el B-T.

    Args:
        pdf_file: PDF con al menos una firma PAdES-B-B embebida.

    Returns:
        Response: PDF con DocTimeStamp como descarga (application/pdf).
            Header X-Timestamp-Type: "document_timestamp"

    Raises:
        HTTPException 400: PDF inválido, vacío, o sin firmas previas.
        HTTPException 401: API key inválida o faltante.
        HTTPException 409: PDF_HASH_MISMATCH — el hash del PDF recibido no coincide
            con expected_sha256 (el PDF fue alterado en tránsito o es incorrecto).
        HTTPException 503:
            - TSA_UNAVAILABLE: circuit breaker abierto (reintentar en ~60s).
            - TSA_ERROR: TSA respondió con error tras los reintentos (reintentar).
        HTTPException 500: Error interno inesperado.
    """
    try:
        pdf_bytes = await pdf_file.read()
    except Exception as e:
        logger.error(f"timestamp_pdf: error al leer PDF: {e}")
        raise HTTPException(status_code=400, detail="No se pudo leer el archivo PDF")

    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="EMPTY_FILE: el PDF está vacío")

    await validate_internal_hmac(request, body=pdf_bytes)

    if len(pdf_bytes) > MAX_SIGNABLE_PDF_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"FILE_TOO_LARGE: el PDF supera {MAX_SIGNABLE_PDF_SIZE_MB} MB"
        )

    if not pdf_bytes.startswith(b"%PDF"):
        raise HTTPException(
            status_code=400,
            detail="INVALID_PDF_FORMAT: el archivo no es un PDF válido"
        )

    existing_sig_count = count_pades_signatures(pdf_bytes)
    if existing_sig_count == 0:
        logger.warning(
            f"timestamp_pdf: PDF sin firmas recibido ({len(pdf_bytes)} bytes) — rechazado"
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "UNSIGNED_PDF: el PDF no contiene firmas; "
                "/timestamp-pdf solo sella PDFs firmados en B-B"
            ),
        )
    logger.info(f"timestamp_pdf: PDF con {existing_sig_count} firma(s) — procediendo")

    if expected_sha256:
        calculated = hashlib.sha256(pdf_bytes).hexdigest()
        if calculated.lower() != expected_sha256.lower():
            logger.warning(
                f"timestamp_pdf: PDF_HASH_MISMATCH — "
                f"esperado={expected_sha256[:8]}... calculado={calculated[:8]}..."
            )
            raise HTTPException(
                status_code=409,
                detail="PDF_HASH_MISMATCH: el PDF recibido no coincide con el hash esperado",
            )
        logger.info(f"timestamp_pdf: integridad SHA-256 OK ({calculated[:8]}...)")
    elif REQUIRE_EXPECTED_SHA256:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "MISSING_EXPECTED_SHA256",
                "detail": "expected_sha256 es obligatorio (REQUIRE_EXPECTED_SHA256=true)"
            }
        )
    else:
        logger.warning("notary.sha256_check_skipped endpoint=/timestamp-pdf")

    existing_ts_count = count_pades_timestamps(pdf_bytes)
    if existing_ts_count > 0:
        logger.info(
            f"timestamp_pdf: PDF ya tiene {existing_ts_count} DocTimeStamp(s) — "
            "idempotente, se devuelve sin cambios"
        )
        safe_filename = sanitize_filename(pdf_file.filename or "documento_timestamped.pdf")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={safe_filename}",
                "Content-Type": "application/pdf",
                "X-Timestamp-Type": "already_timestamped",
            },
        )

    try:
        result = await async_add_document_timestamp(pdf_bytes)
    except PAdESTsaUnavailableError as e:
        logger.warning(f"timestamp_pdf: TSA no disponible (circuit breaker): {e}")
        raise HTTPException(
            status_code=503,
            detail=f"TSA_UNAVAILABLE: {str(e)}"
        )
    except PAdESTimestampError as e:
        logger.warning(f"timestamp_pdf: error de TSA tras reintentos: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"TSA_ERROR: {str(e)}"
        )
    except PAdESSigningError as e:
        logger.error(f"timestamp_pdf: error de firma: {e}")
        raise HTTPException(status_code=500, detail=f"TIMESTAMP_ERROR: {str(e)}")
    except Exception as e:
        logger.error(f"timestamp_pdf: error inesperado: {e}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail="Error interno al agregar el DocTimeStamp"
        )

    safe_filename = sanitize_filename(pdf_file.filename or "documento_timestamped.pdf")
    return Response(
        content=result,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={safe_filename}",
            "Content-Type": "application/pdf",
            "X-Timestamp-Type": "document_timestamp",
        },
    )


@app.post("/stamp-number")
async def stamp_number(
    request: Request,
    pdf_file: UploadFile = File(..., description="PDF a estampar"),
    document_number: Optional[str] = Form(None, description="Número de documento (opcional — si se omite, solo calcula posición sin estampar)"),
    city: Optional[str] = Form(None, description="Ciudad para el sello (requerido si document_number presente)"),
    stamp_position: Optional[str] = Form(None, description="'first' (default) o 'last'"),
    existing_count: Optional[int] = Form(None, description="Número de firmas ya completadas (override de auto-detección)"),
    api_key: str = Depends(validate_api_key),
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

        await validate_internal_hmac(request, body=pdf_content)

        if stamp_position:
            validate_stamp_position(stamp_position)

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
    request: Request,
    pdf_file: UploadFile = File(..., description="PDF to verify"),
    api_key: str = Depends(validate_api_key),
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
        await validate_internal_hmac(request, body=pdf_bytes)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"verify_pdf_signature read error: {e}")
        raise HTTPException(status_code=400, detail="No se pudo procesar el PDF. Verificá que el archivo sea válido.")

    if len(pdf_bytes) > MAX_SIGNABLE_PDF_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"PDF file too large. Maximum size for verify: {MAX_SIGNABLE_PDF_SIZE_MB}MB",
            headers={"error_code": "FILE_TOO_LARGE"}
        )

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
    logger.error(f"Excepción no controlada: {str(exc)}")
    logger.error(f"Traceback: {traceback.format_exc()}")

    report_error(request, exc, kind="UNHANDLED")

    return HTTPException(
        status_code=500,
        detail="Error interno del servidor"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )