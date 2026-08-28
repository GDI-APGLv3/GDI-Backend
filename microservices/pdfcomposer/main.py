
from fastapi import FastAPI, Depends, HTTPException, Header, Request, Response, Form, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from typing import Optional
import asyncio
import os
import hmac
import hashlib
from dotenv import load_dotenv
import uvicorn
import uuid
import json
from pydantic import ValidationError
import logging
from logtail import LogtailHandler
import io
import fitz
import pdfplumber

from app.services.pdf_service import (
    generate_preview_pdf,
    generate_case_pdf,
    generate_general_pdf,
    generate_move_pdf,
    generate_import_pdf,
    generate_note_pdf,
    generate_note_preview_pdf,
    generate_ifrlm_pdf,
    merge_pdfs,
    embed_files_in_pdf,
    get_logo_cache_stats,
)
from app.models.pdf_models import PDFRequest, CaseRequest, MoveRequest, ImportRequest, NoteRequest, IFRLMRequest
from app.config import API_KEY, DEFAULT_TIMEOUT, ENV
from app.version import VERSION, GIT_SHA
from error_alerts import report_error

load_dotenv()

logger = logging.getLogger(__name__)

if os.getenv("BETTERSTACK_SOURCE_TOKEN"):
    handler = LogtailHandler(source_token=os.getenv("BETTERSTACK_SOURCE_TOKEN"))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

API_KEY_NAME = "X-API-Key"

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

_docs_url = "/docs" if ENV != "production" else None
_redoc_url = "/redoc" if ENV != "production" else None
_openapi_url = "/openapi.json" if ENV != "production" else None


_PDF_MAX_CONCURRENCY = int(os.getenv("PDF_MAX_CONCURRENCY", "2"))
_pdf_semaphore = asyncio.Semaphore(_PDF_MAX_CONCURRENCY)

_PDF_MAX_QUEUE = int(os.getenv("PDF_MAX_QUEUE", "30"))
_pdf_in_flight = 0


async def _run_pdf(func, *args, **kwargs):
    global _pdf_in_flight
    if _pdf_in_flight >= _PDF_MAX_QUEUE:
        raise HTTPException(
            status_code=503,
            detail="Servicio de generacion de PDF saturado, reintente en unos segundos",
        )
    _pdf_in_flight += 1
    try:
        async with _pdf_semaphore:
            return await asyncio.to_thread(func, *args, **kwargs)
    finally:
        _pdf_in_flight -= 1


app = FastAPI(
    title="PDF Composer API",
    description="API para generar documentos PDF utilizando WeasyPrint.",
    version=VERSION,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
)

@app.exception_handler(TimeoutError)
async def timeout_error_handler(request: Request, exc: TimeoutError):
    logger.error(f"[S6-003] TimeoutError en {request.url.path}: {exc}")
    report_error(request, exc, kind="TIMEOUT")
    return JSONResponse(
        status_code=504,
        content={"detail": "La generacion del PDF excedio el tiempo limite. Intente con un documento mas corto."},
    )


@app.exception_handler(Exception)
async def _global_error_handler(request, exc):
    if isinstance(exc, HTTPException):
        raise exc
    logger.error("[UNHANDLED] %s en %s %s: %s", type(exc).__name__, request.method, request.url.path, exc, exc_info=True)
    report_error(request, exc, kind="UNHANDLED")
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor"})


async def verify_api_key(api_key: Optional[str] = Header(None, alias=API_KEY_NAME)):
    if api_key is None or not hmac.compare_digest(api_key, API_KEY):
        raise HTTPException(status_code=403, detail="API Key invalida o no proporcionada")
    return api_key


if os.getenv("ENABLE_DEBUG_BOOM") == "1":
    @app.get("/_debug/boom", include_in_schema=False)
    async def _debug_boom(api_key: str = Depends(verify_api_key)):
        raise RuntimeError("Error de prueba (DIY error-mail): validacion del pipeline de alertas")


MAX_EMBEDDED_FILE_SIZE = 50 * 1024 * 1024
MAX_TOTAL_EMBEDDED_SIZE = 60 * 1024 * 1024

EMBEDDED_FILE_ALLOWED_EXTENSIONS = {
    "pdf", "xls", "xlsx", "doc", "docx", "odt", "ods",
    "csv", "txt", "png", "jpg", "jpeg", "dxf",
}


def _looks_like_allowed_embedded_file(filename: Optional[str], content: bytes) -> bool:
    if not filename or not content:
        return False

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EMBEDDED_FILE_ALLOWED_EXTENSIONS:
        return False

    if ext == "pdf":
        return content.startswith(b"%PDF")
    if ext == "png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in ("jpg", "jpeg"):
        return content.startswith(b"\xff\xd8\xff")
    if ext in ("xlsx", "docx", "odt", "ods"):
        return content.startswith(b"PK\x03\x04")
    if ext in ("xls", "doc"):
        return content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if ext == "dxf":
        head_text = content[:256].decode("utf-8-sig", errors="ignore")
        return "SECTION" in head_text or content[:2] == b"AC"
    if ext in ("csv", "txt"):
        try:
            content.decode("utf-8")
            return True
        except UnicodeDecodeError:
            try:
                content.decode("latin-1")
                return True
            except Exception:
                return False
    return False

@app.get("/")
async def root():
    """
    Endpoint raiz que proporciona informacion basica sobre la API.
    """
    return {
        "message": "Bienvenido al servicio de generacion de PDF",
        "documentation": "/docs",
        "version": VERSION
    }


@app.get("/health")
async def health():
    """Health check basico. No requiere API Key."""
    return {"status": "healthy", "version": VERSION, "commit": GIT_SHA}


@app.get("/health/details")
async def health_details(api_key: str = Depends(verify_api_key)):
    """Health detallado con self-test de WeasyPrint. Requiere API Key."""
    engine_status = "unknown"
    try:
        from weasyprint import HTML
        test_html = "<html><body><p>health-check</p></body></html>"
        pdf_bytes = await _run_pdf(HTML(string=test_html).write_pdf)
        engine_status = "ok" if pdf_bytes and len(pdf_bytes) > 0 else "error"
    except Exception as e:
        logger.error(f"WeasyPrint health check failed: {e}")
        engine_status = "error"
    overall = "healthy" if engine_status == "ok" else "degraded"
    return {
        "status": overall,
        "service": "pdfcomposer",
        "version": VERSION,
        "commit": GIT_SHA,
        "pdf_engine": "weasyprint",
        "engine_status": engine_status,
    }

@app.get("/cache/stats")
async def cache_stats(api_key: str = Depends(verify_api_key)):
    """
    Stats del cache de logos (per-worker, no global).
    Para observabilidad real complementar con logs cache_hit/cache_miss en Better Stack.
    """
    return {"logo_cache": get_logo_cache_stats()}


@app.post("/preview-pdf/", summary="Genera PDF preview con marca de agua")
async def preview_pdf(
    urlLogo: Optional[str] = Form(None, json_schema_extra={"example": "https://example.com/logo.png"}),
    NameAcronyType: str = Form(..., json_schema_extra={"example": "GDI"}),
    TypeDocument: str = Form(..., json_schema_extra={"example": "INFORME"}),
    Reference: str = Form(..., json_schema_extra={"example": "Ref: Solicitud de presupuesto 2025"}),
    frase_anual: Optional[str] = Form(None),
    Text: str = Form(..., json_schema_extra={"example": '{"html": "<p>Por medio de la presente, informamos que el proyecto ha sido aprobado.</p>"}'}),
    api_key: str = Depends(verify_api_key)
):
    """
    Genera un PDF a partir de los datos proporcionados utilizando WeasyPrint.

    Args:
        urlLogo (str, optional): URL del logo a incluir. Defaults to None.
        NameAcronyType (str): Acronimo del nombre del tipo (ej. 'GDI', 'ACME').
        TypeDocument (str): Tipo de documento.
        Reference (str): Referencia del documento.
        Text (str): Contenido principal del documento en formato JSON string.
        api_key (str): API Key para autenticacion.

    Returns:
        Response: Archivo PDF para descargar.

    Raises:
        HTTPException: Error 422 si los datos son invalidos, 500 si la generacion falla.
    """
    try:
        text_dict = json.loads(Text)

        pdf_request_data = PDFRequest(
            urlLogo=urlLogo,
            NameAcronyType=NameAcronyType,
            TypeDocument=TypeDocument,
            Reference=Reference,
            frase_anual=frase_anual,
            Text=text_dict
        )

        pdf_bytes = await _run_pdf(generate_preview_pdf, request_data=pdf_request_data)

        file_name = f"{uuid.uuid4()}.pdf"

        headers = {
            "Content-Disposition": f"attachment; filename={file_name}"
        }

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers=headers
        )
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Error en preview_pdf: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar la solicitud")

@app.post("/generate-pdf/", summary="Genera PDF final (sin marca de agua)")
async def generate_pdf(
    urlLogo: Optional[str] = Form(None, json_schema_extra={"example": "https://example.com/logo.png"}),
    NameAcronyType: str = Form(..., json_schema_extra={"example": "GDI"}),
    TypeDocument: str = Form(..., json_schema_extra={"example": "INFORME"}),
    Reference: str = Form(..., json_schema_extra={"example": "Ref: Solicitud de presupuesto 2025"}),
    frase_anual: Optional[str] = Form(None),
    Text: str = Form(..., json_schema_extra={"example": '{"html": "<p>Por medio de la presente, informamos que el proyecto ha sido aprobado.</p>"}'}),
    attachment_file: Optional[UploadFile] = File(None, description="PDF adjunto a anexar al final (opcional)"),
    embedded_files: list[UploadFile] = File(default=[], description="Archivos a embeber dentro del PDF (GDI-063, opcional, varios)"),
    api_key: str = Depends(verify_api_key)
):
    """
    Genera un PDF a partir de los datos proporcionados utilizando la plantilla generate-pdf.html.
    Opcionalmente anexa paginas de un PDF adjunto al final y/o embebe archivos dentro del PDF.

    Args:
        urlLogo (str, optional): URL del logo a incluir. Defaults to None.
        NameAcronyType (str): Acronimo del nombre del tipo (ej. 'GDI', 'ACME').
        TypeDocument (str): Tipo de documento.
        Reference (str): Referencia del documento.
        Text (str): Contenido principal del documento en formato JSON string.
        attachment_file (UploadFile, optional): PDF adjunto a anexar al final del documento.
        embedded_files (list[UploadFile], optional): Archivos a embeber dentro del PDF
            (GDI-063, PDF /EmbeddedFiles). Campo multipart repetido, SEPARADO de attachment_file.
        api_key (str): API Key para autenticacion.

    Returns:
        Response: Archivo PDF para descargar.

    Raises:
        HTTPException: Error 400 si el adjunto/embebido no es valido, 413 si excede
                       los limites de tamano, 422 si los datos son invalidos,
                       500 si la generacion falla.
    """
    try:
        text_dict = json.loads(Text)

        pdf_request_data = PDFRequest(
            urlLogo=urlLogo,
            NameAcronyType=NameAcronyType,
            TypeDocument=TypeDocument,
            Reference=Reference,
            frase_anual=frase_anual,
            Text=text_dict
        )

        pdf_bytes = await _run_pdf(generate_general_pdf, request_data=pdf_request_data)

        if attachment_file:
            attachment_bytes = await attachment_file.read()

            if len(attachment_bytes) > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail="Archivo adjunto excede 25MB")

            if not attachment_bytes.startswith(b'%PDF'):
                raise HTTPException(status_code=400, detail="El archivo adjunto debe ser un PDF valido")

            try:
                pdf_bytes = await _run_pdf(merge_pdfs, pdf_bytes, attachment_bytes)
            except Exception as e:
                logger.error(f"Error al procesar PDF adjunto: {e}")
                raise HTTPException(status_code=400, detail="Error al procesar el PDF adjunto")

        if embedded_files:
            total_size = 0
            embedded_payload = []

            for uploaded in embedded_files:
                content = await uploaded.read()
                total_size += len(content)

                if len(content) > MAX_EMBEDDED_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Adjunto embebido '{uploaded.filename}' excede 50MB",
                    )
                if total_size > MAX_TOTAL_EMBEDDED_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="La suma de adjuntos embebidos excede 60MB",
                    )
                if not _looks_like_allowed_embedded_file(uploaded.filename, content):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Adjunto embebido '{uploaded.filename}' no supera la validacion de contenido",
                    )

                embedded_payload.append((uploaded.filename or "adjunto", content))

            try:
                pdf_bytes = await _run_pdf(embed_files_in_pdf, pdf_bytes, embedded_payload)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error al embeber archivos en PDF: {e}")
                raise HTTPException(status_code=400, detail="Error al embeber los archivos adjuntos")

        file_name = f"{uuid.uuid4()}.pdf"

        headers = {
            "Content-Disposition": f"attachment; filename={file_name}",
            "X-PDF-SHA256": hashlib.sha256(pdf_bytes).hexdigest(),
        }

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers=headers
        )
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Error en generate_pdf: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar la solicitud")

@app.post("/create-case/", summary="Genera caratula de expediente (CAEX)")
async def create_case(
    urlLogo: Optional[str] = Form(None, json_schema_extra={"example": "https://example.com/logo.png"}),
    NameAcronyType: str = Form(..., json_schema_extra={"example": "GDI"}),
    document_type: str = Form(..., json_schema_extra={"example": "CARATULA DE EXPEDIENTE"}),
    reference: str = Form(..., json_schema_extra={"example": "CAEX-2025-0000123-MUNI"}),
    frase_anual: Optional[str] = Form(None),
    case_number: str = Form(..., json_schema_extra={"example": "EX-2025-0000123-MUNI"}),
    acrony_case_type: str = Form(..., json_schema_extra={"example": "HAB"}),
    case_type: str = Form(..., json_schema_extra={"example": "Habilitacion Comercial"}),
    case_motive: str = Form(..., json_schema_extra={"example": "Solicitud de habilitacion para panaderia San Jose"}),
    initiating_division: str = Form(..., json_schema_extra={"example": "Direccion de Comercio e Industria"}),
    creator: str = Form(..., json_schema_extra={"example": "Juan Perez"}),
    api_key: str = Depends(verify_api_key)
):
    """
    Genera la caratula de un caso en PDF a partir de la plantilla `caratula.html`.

    La fecha de caratula se genera automaticamente (UTC).

    Args:
        urlLogo (str, optional): URL del logo a incluir.
        NameAcronyType (str): Acronimo del nombre del tipo (ej. 'GDI', 'ACME').
        document_type (str): Tipo de documento.
        reference (str): Referencia del documento.
        case_number (str): Numero de expediente.
        acrony_case_type (str): Acronimo del tipo de expediente.
        case_type (str): Tipo de expediente.
        case_motive (str): Motivo del expediente.
        initiating_division (str): Reparticion iniciadora.
        creator (str): Nombre del caratulador.
        api_key (str): API Key para autenticacion.

    Returns:
        Response: Archivo PDF para descargar.
    """
    try:
        case_request_data = CaseRequest(
            urlLogo=urlLogo,
            NameAcronyType=NameAcronyType,
            document_type=document_type,
            reference=reference,
            frase_anual=frase_anual,
            case_number=case_number,
            acrony_case_type=acrony_case_type,
            case_type=case_type,
            case_motive=case_motive,
            initiating_division=initiating_division,
            creator=creator
        )

        pdf_bytes = await _run_pdf(generate_case_pdf, request_data=case_request_data)

        file_name = f"{uuid.uuid4()}.pdf"

        headers = {
            "Content-Disposition": f"attachment; filename={file_name}",
            "X-PDF-SHA256": hashlib.sha256(pdf_bytes).hexdigest(),
        }

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers=headers
        )
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Error en create_case: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar la solicitud")

@app.post("/move/", summary="Genera pase de expediente (PV)")
async def move(
    urlLogo: Optional[str] = Form(None, json_schema_extra={"example": "https://example.com/logo.png"}),
    NameAcronyType: str = Form(..., json_schema_extra={"example": "GDI"}),
    document_type: str = Form(..., json_schema_extra={"example": "PASE DE VISTA"}),
    reference: str = Form(..., json_schema_extra={"example": "PV-2025-0000456-MUNI"}),
    frase_anual: Optional[str] = Form(None),
    tipo_movimiento: str = Form(..., json_schema_extra={"example": "Pase para dictamen tecnico"}),
    area_requiriente: str = Form(..., json_schema_extra={"example": "Direccion de Comercio e Industria"}),
    area_receptora: str = Form(..., json_schema_extra={"example": "Direccion de Obras Particulares"}),
    motivo: str = Form(..., json_schema_extra={"example": "Se solicita inspeccion tecnica del local"}),
    api_key: str = Depends(verify_api_key)
):
    """
    Genera un PDF de movimiento a partir de la plantilla `movimiento.html`.

    Args:
        urlLogo (str, optional): URL del logo a incluir.
        NameAcronyType (str): Acronimo del nombre del tipo (ej. 'GDI', 'ACME').
        document_type (str): Tipo de documento.
        reference (str): Referencia del documento.
        tipo_movimiento (str): Tipo de movimiento.
        area_requiriente (str): Area requiriente (DE).
        area_receptora (str): Area receptora (A).
        motivo (str): Motivo del movimiento.
        api_key (str): API Key para autenticacion.

    Returns:
        Response: Archivo PDF para descargar.
    """
    try:
        move_request_data = MoveRequest(
            urlLogo=urlLogo,
            NameAcronyType=NameAcronyType,
            document_type=document_type,
            reference=reference,
            frase_anual=frase_anual,
            tipo_movimiento=tipo_movimiento,
            area_requiriente=area_requiriente,
            area_receptora=area_receptora,
            motivo=motivo
        )

        pdf_bytes = await _run_pdf(generate_move_pdf, request_data=move_request_data)

        file_name = f"{uuid.uuid4()}.pdf"

        headers = {
            "Content-Disposition": f"attachment; filename={file_name}",
            "X-PDF-SHA256": hashlib.sha256(pdf_bytes).hexdigest(),
        }

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers=headers
        )
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Error en move: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar la solicitud")

MAX_FILE_SIZE = 25 * 1024 * 1024

@app.post("/import/", summary="Importa PDF externo con pagina informativa")
async def import_document(
    pdf_file: UploadFile = File(..., description="Archivo PDF a importar"),
    urlLogo: Optional[str] = Form(None, json_schema_extra={"example": "https://example.com/logo.png"}),
    NameAcronyType: str = Form(..., json_schema_extra={"example": "GDI"}),
    document_type: str = Form(..., json_schema_extra={"example": "DOCUMENTO IMPORTADO"}),
    reference: str = Form(..., json_schema_extra={"example": "IMP-2025-0000789-MUNI"}),
    frase_anual: Optional[str] = Form(None),
    api_key: str = Depends(verify_api_key)
):
    """
    Importa un PDF externo, cuenta sus paginas, genera una pagina informativa
    y devuelve un PDF compilado (original + pagina info al final).

    Args:
        pdf_file (UploadFile): Archivo PDF a importar.
        urlLogo (str, optional): URL del logo a incluir.
        NameAcronyType (str): Acronimo del nombre del tipo (ej. 'GDI', 'ACME').
        document_type (str): Tipo de documento.
        reference (str): Referencia del documento.
        api_key (str): API Key para autenticacion.

    Returns:
        Response: PDF compilado (original + pagina informativa al final).

    Raises:
        HTTPException: Error 400 si no es PDF, 413 si excede 25 MB, 500 si falla.
    """
    try:
        pdf_original = await pdf_file.read()

        if len(pdf_original) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="PDF excede 25 MB")

        if not pdf_original[:5].startswith(b'%PDF-'):
            raise HTTPException(status_code=400, detail="El archivo no es un PDF valido")

        with pdfplumber.open(io.BytesIO(pdf_original)) as pdf:
            cantidad_paginas = len(pdf.pages)

        import_request = ImportRequest(
            urlLogo=urlLogo,
            NameAcronyType=NameAcronyType,
            document_type=document_type,
            reference=reference,
            frase_anual=frase_anual,
            cantidad_paginas=cantidad_paginas
        )
        pdf_info = await _run_pdf(generate_import_pdf, import_request)

        doc_original = fitz.open(stream=pdf_original, filetype="pdf")
        doc_info = fitz.open(stream=pdf_info, filetype="pdf")

        doc_original.insert_pdf(doc_info)

        pdf_compilado = doc_original.tobytes()

        doc_original.close()
        doc_info.close()

        file_name = f"{uuid.uuid4()}.pdf"

        headers = {
            "Content-Disposition": f"attachment; filename={file_name}",
            "X-PDF-SHA256": hashlib.sha256(pdf_compilado).hexdigest(),
        }

        return Response(
            content=pdf_compilado,
            media_type="application/pdf",
            headers=headers
        )
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Error en import_document: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar la solicitud")

@app.post("/note-preview/", summary="Genera nota con marca de agua (preview)")
async def note_preview(
    urlLogo: Optional[str] = Form(None, json_schema_extra={"example": "https://example.com/logo.png"}),
    NameAcronyType: str = Form(..., json_schema_extra={"example": "GDI"}),
    document_type: str = Form(..., json_schema_extra={"example": "NOTA"}),
    reference: str = Form(..., json_schema_extra={"example": "Ref: Comunicacion interna - Proyecto 2025"}),
    frase_anual: Optional[str] = Form(None),
    para: Optional[str] = Form(None, json_schema_extra={"example": "Director de Finanzas"}),
    cc: Optional[str] = Form(None, json_schema_extra={"example": "Secretario General, Tesorero Municipal"}),
    Text: str = Form(..., json_schema_extra={"example": '{"html": "<p>Por medio de la presente, me dirijo a usted.</p><ul><li>Punto 1</li><li>Punto 2</li></ul>"}'}),
    api_key: str = Depends(verify_api_key)
):
    """
    Genera un PDF de nota con marca de agua de previsualizacion.

    Args:
        urlLogo (str, optional): URL del logo a incluir.
        NameAcronyType (str): Acronimo del nombre del tipo (ej. 'GDI').
        document_type (str): Tipo de documento (ej. 'NOTA').
        reference (str): Referencia del documento.
        para (str): Destinatario principal (PARA).
        cc (str, optional): Destinatarios en copia (CC). Si vacio, no aparece.
        Text (str): Contenido HTML del documento en formato JSON string.
        api_key (str): API Key para autenticacion.

    Returns:
        Response: Archivo PDF para descargar con marca de agua.
    """
    try:
        text_dict = json.loads(Text)

        note_request_data = NoteRequest(
            urlLogo=urlLogo,
            NameAcronyType=NameAcronyType,
            document_type=document_type,
            reference=reference,
            frase_anual=frase_anual,
            para=para,
            cc=cc,
            Text=text_dict
        )

        pdf_bytes = await _run_pdf(generate_note_preview_pdf, request_data=note_request_data)

        file_name = f"{uuid.uuid4()}.pdf"

        headers = {
            "Content-Disposition": f"attachment; filename={file_name}"
        }

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers=headers
        )
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Error en note_preview: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar la solicitud")


@app.post("/note/", summary="Genera nota final (sin marca de agua)")
async def note(
    urlLogo: Optional[str] = Form(None, json_schema_extra={"example": "https://example.com/logo.png"}),
    NameAcronyType: str = Form(..., json_schema_extra={"example": "GDI"}),
    document_type: str = Form(..., json_schema_extra={"example": "NOTA"}),
    reference: str = Form(..., json_schema_extra={"example": "Ref: Comunicacion interna - Proyecto 2025"}),
    frase_anual: Optional[str] = Form(None),
    para: Optional[str] = Form(None, json_schema_extra={"example": "Director de Finanzas"}),
    cc: Optional[str] = Form(None, json_schema_extra={"example": "Secretario General, Tesorero Municipal"}),
    Text: str = Form(..., json_schema_extra={"example": '{"html": "<p>Por medio de la presente, me dirijo a usted.</p><ul><li>Punto 1</li><li>Punto 2</li></ul>"}'}),
    api_key: str = Depends(verify_api_key)
):
    """
    Genera un PDF de nota sin marca de agua.

    Args:
        urlLogo (str, optional): URL del logo a incluir.
        NameAcronyType (str): Acronimo del nombre del tipo (ej. 'GDI').
        document_type (str): Tipo de documento (ej. 'NOTA').
        reference (str): Referencia del documento.
        para (str): Destinatario principal (PARA).
        cc (str, optional): Destinatarios en copia (CC). Si vacio, no aparece.
        Text (str): Contenido HTML del documento en formato JSON string.
        api_key (str): API Key para autenticacion.

    Returns:
        Response: Archivo PDF para descargar.
    """
    try:
        text_dict = json.loads(Text)

        note_request_data = NoteRequest(
            urlLogo=urlLogo,
            NameAcronyType=NameAcronyType,
            document_type=document_type,
            reference=reference,
            frase_anual=frase_anual,
            para=para,
            cc=cc,
            Text=text_dict
        )

        pdf_bytes = await _run_pdf(generate_note_pdf, request_data=note_request_data)

        file_name = f"{uuid.uuid4()}.pdf"

        headers = {
            "Content-Disposition": f"attachment; filename={file_name}",
            "X-PDF-SHA256": hashlib.sha256(pdf_bytes).hexdigest(),
        }

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers=headers
        )
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Error en note: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar la solicitud")


@app.post("/ifrlm/", summary="Genera informe de legajo (IFRLM)")
async def create_ifrlm(
    urlLogo: Optional[str] = Form(None, json_schema_extra={"example": "https://example.com/logo.png"}),
    NameAcronyType: str = Form(..., json_schema_extra={"example": "GDI"}),
    document_type: str = Form(..., json_schema_extra={"example": "INFORME DE LEGAJO"}),
    reference: str = Form(..., json_schema_extra={"example": "IFRLM-2026-0000001-MUNI"}),
    frase_anual: Optional[str] = Form(None),
    record_number: str = Form(..., json_schema_extra={"example": "LG-2026-0000001-MUNI"}),
    registry_name: str = Form(..., json_schema_extra={"example": "Legajo de Personal"}),
    state: str = Form(..., json_schema_extra={"example": "Activo"}),
    snapshot_html: str = Form(..., json_schema_extra={"example": "<p>Contenido del snapshot del legajo</p>"}),
    api_key: str = Depends(verify_api_key)
):
    """
    Genera un informe de legajo (IFRLM) en PDF.

    La fecha de generacion se calcula automaticamente (UTC).

    Args:
        urlLogo (str, optional): URL del logo a incluir.
        NameAcronyType (str): Acronimo del nombre del tipo (ej. 'GDI', 'ACME').
        document_type (str): Tipo de documento.
        reference (str): Referencia del documento.
        frase_anual (str, optional): Frase anual institucional.
        record_number (str): Numero del legajo.
        registry_name (str): Nombre de la familia del registro.
        state (str): Estado actual del legajo.
        snapshot_html (str): HTML del snapshot del legajo.
        api_key (str): API Key para autenticacion.

    Returns:
        Response: Archivo PDF para descargar.
    """
    try:
        ifrlm_request_data = IFRLMRequest(
            urlLogo=urlLogo,
            NameAcronyType=NameAcronyType,
            document_type=document_type,
            reference=reference,
            frase_anual=frase_anual,
            record_number=record_number,
            registry_name=registry_name,
            state=state,
            snapshot_html=snapshot_html
        )

        pdf_bytes = await _run_pdf(generate_ifrlm_pdf, request_data=ifrlm_request_data)

        file_name = f"{uuid.uuid4()}.pdf"

        headers = {
            "Content-Disposition": f"attachment; filename={file_name}",
            "X-PDF-SHA256": hashlib.sha256(pdf_bytes).hexdigest(),
        }

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers=headers
        )
    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"Error en create_ifrlm: {e}")
        raise HTTPException(status_code=500, detail="Error interno al procesar la solicitud")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8002))
    print(f"[DEV] Iniciando servidor en http://localhost:{port}")
    print(f"[DOCS] Documentacion disponible en: http://localhost:{port}/docs")
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=True)
