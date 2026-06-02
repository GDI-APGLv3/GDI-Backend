"""
Servicio para la generacion de documentos PDF utilizando WeasyPrint.

Este modulo proporciona funciones para renderizar plantillas HTML y convertirlas
a PDF usando WeasyPrint (Pango/Cairo).
"""

import os
import functools
import requests
import base64
import io
import socket
import ipaddress
import logging
import pdfplumber
import nh3
import concurrent.futures
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from typing import Union, Optional, Tuple
import fitz  # PyMuPDF
from urllib.parse import urlparse, urlunparse
from weasyprint import HTML
from weasyprint.text.fonts import FontConfiguration
from weasyprint.urls import default_url_fetcher
from app.models.pdf_models import PDFRequest, CaseRequest, MoveRequest, ImportRequest, NoteRequest, IFRLMRequest
from datetime import datetime, timezone
from app.config import DEFAULT_TIMEOUT, LOGO_FETCH_TIMEOUT, PDF_GENERATION_TIMEOUT, WEASYPRINT_RESOURCE_TIMEOUT

logger = logging.getLogger(__name__)

# Constante para el texto de anclaje que se buscara en el PDF
SIGNATURE_ANCHOR_TEXT = "end-text"

# Configuracion de plantillas
templates_dir = Path(__file__).parent.parent / "templates"
env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)

# Cache de FontConfiguration a nivel modulo (Paso 0.2 Mejora-PDFComposer).
# Con preload_app=False, cada worker importa este modulo post-fork de forma
# independiente, asi que cada worker tiene su propia FontConfiguration. Evita
# rescanear fontconfig en cada request: ahorro 5-20ms warm, 50-100ms en cold start.
FONT_CONFIG = FontConfiguration()

# Templates Jinja2 precompilados a nivel modulo (Paso 0.3 Mejora-PDFComposer).
# Evita ir al loader/parser en cada request.
TEMPLATE_PREVIEW = env.get_template("plantilla.html")
TEMPLATE_GENERATE = env.get_template("generate-pdf.html")
TEMPLATE_CASE = env.get_template("caratula.html")
TEMPLATE_MOVE = env.get_template("movimiento.html")
TEMPLATE_IMPORT = env.get_template("Importado.html")
TEMPLATE_NOTE = env.get_template("nota.html")
TEMPLATE_NOTE_PREVIEW = env.get_template("nota_preview.html")
TEMPLATE_IFRLM = env.get_template("ifrlm.html")

# Tags y atributos permitidos para sanitizacion HTML (editor Quill)
ALLOWED_TAGS = {
    "p", "br", "strong", "em", "u", "s", "ol", "ul", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "code",
    "a", "img", "span", "div", "table", "thead", "tbody", "tr", "td", "th",
    "sub", "sup", "hr",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "*": {"class", "style"},
}

# Hostnames bloqueados para prevencion de SSRF
BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def validate_url_safety(url: str) -> None:
    """
    Valida que una URL sea segura antes de hacer requests externos.
    Previene SSRF bloqueando IPs privadas, localhost y hosts internos.

    Raises:
        ValueError: Si la URL no es segura.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Scheme no permitido: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL sin hostname")

    # Bloquear hostnames conocidos
    hostname_lower = hostname.lower()
    if hostname_lower in BLOCKED_HOSTNAMES:
        raise ValueError(f"Hostname bloqueado: {hostname}")

    if hostname_lower.endswith(".internal"):
        raise ValueError(f"Hostname interno bloqueado: {hostname}")

    # Resolver hostname y verificar que no sea IP privada
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise ValueError(f"No se pudo resolver hostname: {hostname}")

    for addr_info in addr_infos:
        ip = ipaddress.ip_address(addr_info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"IP privada/reservada bloqueada: {ip}")


def sanitize_html(html_content: str) -> str:
    """
    Sanitiza HTML usando nh3, permitiendo solo tags seguros del editor Quill.
    Elimina <script>, <iframe>, <object>, <embed>, event handlers, etc.
    """
    if not html_content:
        return ""
    return nh3.clean(
        html_content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
    )

def safe_url_fetcher(url, timeout=None, ssl_context=None):
    """
    URL fetcher para WeasyPrint que valida SSRF y aplica timeout propio.

    S6-003: WeasyPrint puede pasar timeout=None (sin limite). Se ignora el valor
    que pase WeasyPrint y se usa WEASYPRINT_RESOURCE_TIMEOUT para garantizar que
    un recurso externo caido no bloquee la generacion del PDF completo.

    Si el fetch falla por red (timeout, conexion, SSL), se loguea y se re-raise
    para que WeasyPrint omita el recurso sin colgar el worker.
    """
    # data: URIs (fuentes embebidas, imagenes base64) -> directo, sin red
    if url.startswith("data:"):
        return default_url_fetcher(url, timeout=WEASYPRINT_RESOURCE_TIMEOUT, ssl_context=ssl_context)

    # file: URIs -> bloqueado (no se necesitan recursos del filesystem)
    if url.startswith("file:"):
        raise ValueError(f"URI file: bloqueada por seguridad: {url}")

    # Validar SSRF antes de cualquier conexion
    try:
        validate_url_safety(url)
    except ValueError as e:
        logger.warning(f"[S6-003] safe_url_fetcher: URL bloqueada SSRF url={url} err={e}")
        raise

    # Fetch con timeout propio (ignora el valor que pase WeasyPrint, que puede ser None)
    try:
        return default_url_fetcher(url, timeout=WEASYPRINT_RESOURCE_TIMEOUT, ssl_context=ssl_context)
    except Exception as e:
        # Loguear y re-raise: WeasyPrint ignorara el recurso fallido pero
        # continuara generando el PDF en lugar de quedar colgado esperando.
        logger.warning(f"[S6-003] safe_url_fetcher: fetch fallido url={url} timeout={WEASYPRINT_RESOURCE_TIMEOUT}s err={type(e).__name__}: {e}")
        raise


# --- Cache de logos (Paso 3 Mejora-PDFComposer) -------------------------------
# Cachea hasta 128 logos por worker en memoria. Cada hit es 0ms (sin red).
# Cuando el cliente sube un logo nuevo en BackOffice, R2 le da otra URL ->
# cache miss automatico, sin redeploy. NO sobrevive al reciclaje del worker
# (cada GUNICORN_MAX_REQUESTS), se asume y es trivial.
MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2MB

ALLOWED_LOGO_MIMES = {
    "image/png", "image/jpeg", "image/jpg",
    "image/svg+xml", "image/webp", "image/gif",
}


@functools.lru_cache(maxsize=128)
def _fetch_logo_cached(url: str) -> Optional[Tuple[bytes, str]]:
    """
    Descarga el logo, valida tamano y mime, y devuelve (bytes, mime_type).
    Cacheado en memoria por worker. Devuelve None si falla cualquier validacion.
    """
    try:
        validate_url_safety(url)
        response = requests.get(url, timeout=LOGO_FETCH_TIMEOUT)
        response.raise_for_status()

        if len(response.content) > MAX_LOGO_BYTES:
            logger.warning(f"logo demasiado grande: {url} ({len(response.content)} bytes)")
            return None

        mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if mime not in ALLOWED_LOGO_MIMES:
            logger.warning(f"logo mime no permitido: {mime} para {url}")
            return None

        logger.info(f"logo cache_miss url={url} mime={mime} bytes={len(response.content)}")
        return (response.content, mime)
    except ValueError as e:
        logger.warning(f"logo URL bloqueada por SSRF: {url} - {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"logo download fallo url={url} err={e}")
        return None


def get_logo_base64_cached(url: Optional[str]) -> Optional[str]:
    """
    Devuelve el logo como data URI base64. Cacheado en memoria via lru_cache.
    Maneja None internamente (no hace falta el ternario en el caller).
    """
    if not url:
        return None
    result = _fetch_logo_cached(url)
    if result is None:
        return None
    content, mime = result
    return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def get_logo_cache_stats() -> dict:
    """Stats del cache de logos (per-worker). Expuesto via GET /cache/stats."""
    info = _fetch_logo_cached.cache_info()
    total = info.hits + info.misses
    hit_rate = round(info.hits / total, 3) if total > 0 else None
    return {
        "hits": info.hits,
        "misses": info.misses,
        "currsize": info.currsize,
        "maxsize": info.maxsize,
        "hit_rate": hit_rate,
    }
# --- Fin cache de logos -------------------------------------------------------


def get_image_as_base64(url: str) -> Union[str, None]:
    """
    Obtiene una imagen desde una URL y la convierte a base64.
    Valida la URL contra SSRF antes de hacer el request.

    Args:
        url (str): URL de la imagen a convertir.

    Returns:
        str: Representacion en base64 de la imagen, o None si falla.
    """
    if not url:
        return None
    try:
        validate_url_safety(url)
        response = requests.get(url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        image_data = base64.b64encode(response.content).decode('utf-8')
        content_type = response.headers.get('Content-Type', 'image/png')
        return f"data:{content_type};base64,{image_data}"
    except ValueError as e:
        logger.warning(f"URL bloqueada por SSRF: {url} - {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error al obtener la imagen: {e}")
        return None

def generate_pdf_from_html(html_content: str, pdf_variant: Optional[str] = None) -> bytes:
    """
    Convierte contenido HTML a PDF usando WeasyPrint.

    S6-003: La generacion corre en un ThreadPoolExecutor con un timeout maximo
    de PDF_GENERATION_TIMEOUT segundos. Si WeasyPrint se cuelga (ej. loop
    infinito en el motor Pango/Cairo, recurso externo que no responde), el
    worker no queda bloqueado indefinidamente: se cancela y se propaga
    TimeoutError como HTTP 500 al caller.

    Nota sobre cancelacion: concurrent.futures no puede interrumpir un hilo
    en ejecucion (Python no tiene cancelacion cooperativa de hilos). El hilo
    de WeasyPrint puede seguir corriendo en background hasta que termine o el
    proceso se recicle. El timeout protege el worker de Gunicorn para que
    pueda devolver error y recibir el siguiente request, no para matar el hilo.

    S6-005: Soporte PDF/A via el parametro pdf_variant. Cuando se pasa
    "pdf/a-3b" (o cualquier variante valida), WeasyPrint >= 60 agrega los
    metadatos XMP requeridos por ISO 19005-3 y garantiza conformidad PDF/A.
    Los previews (con marca de agua) usan pdf_variant=None (PDF 1.7 regular)
    porque no son documentos oficiales y no requieren archivo a largo plazo.

    Args:
        html_content (str): El contenido HTML a convertir.
        pdf_variant (Optional[str]): Variante PDF/A a generar. Opciones:
            "pdf/a-1b", "pdf/a-2b", "pdf/a-3b". None = PDF regular.

    Returns:
        bytes: El contenido del archivo PDF generado.

    Raises:
        TimeoutError: Si la generacion supera PDF_GENERATION_TIMEOUT segundos.
        Exception: Si la conversion falla por otro motivo.
    """
    def _do_generate():
        html = HTML(string=html_content, url_fetcher=safe_url_fetcher)
        kwargs = {"font_config": FONT_CONFIG}
        if pdf_variant is not None:
            kwargs["pdf_variant"] = pdf_variant
        return html.write_pdf(**kwargs)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_generate)
        try:
            return future.result(timeout=PDF_GENERATION_TIMEOUT)
        except concurrent.futures.TimeoutError:
            logger.error(
                f"[S6-003] WeasyPrint excedio timeout de {PDF_GENERATION_TIMEOUT}s — "
                f"HTML size={len(html_content)} bytes"
            )
            raise TimeoutError(
                f"La generacion del PDF excedio el limite de {PDF_GENERATION_TIMEOUT} segundos."
            )
        except Exception as e:
            logger.error(f"[S6-005] Error al generar PDF con WeasyPrint (variant={pdf_variant}): {e}")
            raise Exception("Fallo al generar el PDF.") from e

def generate_preview_pdf(request_data: PDFRequest) -> bytes:
    """
    Prepara los datos, renderiza la plantilla HTML y llama al servicio de generacion de PDF.

    Args:
        request_data (PDFRequest): Objeto con los datos para la generacion del PDF.

    Returns:
        bytes: El contenido del PDF generado.
    """
    # Convertir logo a base64 si se proporciona una URL
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    # Cargar la plantilla (precompilada a nivel modulo)
    template = TEMPLATE_PREVIEW

    # Extraer el HTML del campo Text (que es un dict con estructura {"html": "..."})
    text_html = sanitize_html(request_data.Text.get("html", ""))

    # Renderizar la plantilla con los datos proporcionados
    html_content = template.render(
        logo_data=logo_data,
        name_acrony_type=request_data.NameAcronyType,
        document_type=request_data.TypeDocument,
        reference=request_data.Reference,
        frase_anual=request_data.frase_anual,
        text=text_html
    )

    # Generar el PDF desde el HTML usando WeasyPrint
    # Preview: PDF regular (sin PDF/A — no es documento oficial)
    pdf_content = generate_pdf_from_html(html_content)

    return pdf_content

def generate_case_pdf(request_data: CaseRequest) -> bytes:
    """
    Prepara los datos para la caratula, renderiza la plantilla y genera el PDF.

    Args:
        request_data (CaseRequest): Objeto con los datos para la caratula.

    Returns:
        bytes: El contenido del PDF de la caratula generado.
    """
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    # Generar fecha de caratulacion automatica en UTC
    fecha_caratulacion_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    template = TEMPLATE_CASE

    html_content = template.render(
        logo_data=logo_data,
        name_acrony_type=request_data.NameAcronyType,
        document_type=request_data.document_type,
        reference=request_data.reference,
        frase_anual=request_data.frase_anual,
        numero_expediente=request_data.case_number,
        acrony_case_type=request_data.acrony_case_type,
        tipo_expediente=request_data.case_type,
        motivo_expediente=request_data.case_motive,
        reparticion_iniciadora=request_data.initiating_division,
        fecha_caratulacion=fecha_caratulacion_utc,
        caratulador=request_data.creator
    )

    # S6-005: PDF/A-3b para carátulas (documentos oficiales de archivo)
    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")
    return pdf_content

def generate_ifrlm_pdf(request_data: IFRLMRequest) -> bytes:
    """
    Prepara los datos para el informe de legajo, renderiza la plantilla y genera el PDF.

    Args:
        request_data (IFRLMRequest): Objeto con los datos para el informe de legajo.

    Returns:
        bytes: El contenido del PDF del informe de legajo generado.
    """
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    # Generar fecha automatica en UTC
    generated_at_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    template = TEMPLATE_IFRLM

    # Sanitizar el snapshot HTML (viene del backend)
    snapshot_html_clean = sanitize_html(request_data.snapshot_html)

    html_content = template.render(
        logo_data=logo_data,
        name_acrony_type=request_data.NameAcronyType,
        document_type=request_data.document_type,
        reference=request_data.reference,
        frase_anual=request_data.frase_anual,
        record_number=request_data.record_number,
        registry_name=request_data.registry_name,
        state=request_data.state,
        generated_at=generated_at_utc,
        snapshot_html=snapshot_html_clean,
    )

    # S6-005: PDF/A-3b para informes de legajo (documentos oficiales de archivo)
    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")
    return pdf_content

def generate_general_pdf(request_data: PDFRequest) -> bytes:
    """
    Prepara los datos, renderiza la plantilla generate-pdf.html y llama al servicio de generacion de PDF.

    Args:
        request_data (PDFRequest): Objeto con los datos para la generacion del PDF.

    Returns:
        bytes: El contenido del PDF generado.
    """
    # Convertir logo a base64 si se proporciona una URL
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    # Cargar la plantilla generate-pdf.html (precompilada a nivel modulo)
    template = TEMPLATE_GENERATE

    # Extraer el HTML del campo Text (que es un dict con estructura {"html": "..."})
    text_html = sanitize_html(request_data.Text.get("html", ""))

    # Renderizar la plantilla con los datos proporcionados
    html_content = template.render(
        logo_data=logo_data,
        name_acrony_type=request_data.NameAcronyType,
        document_type=request_data.TypeDocument,
        reference=request_data.Reference,
        frase_anual=request_data.frase_anual,
        text=text_html
    )

    # Generar el PDF desde el HTML usando WeasyPrint
    # S6-005: PDF/A-3b para documentos finales (sin marca de agua = documentos oficiales)
    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")

    return pdf_content


def generate_move_pdf(request_data: MoveRequest) -> bytes:
    """
    Prepara los datos para el movimiento, renderiza la plantilla y genera el PDF.

    Args:
        request_data (MoveRequest): Objeto con los datos para el movimiento.

    Returns:
        bytes: El contenido del PDF del movimiento generado.
    """
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    template = TEMPLATE_MOVE

    html_content = template.render(
        logo_data=logo_data,
        name_acrony_type=request_data.NameAcronyType,
        document_type=request_data.document_type,
        reference=request_data.reference,
        frase_anual=request_data.frase_anual,
        tipo_movimiento=request_data.tipo_movimiento,
        area_requiriente=request_data.area_requiriente,
        area_receptora=request_data.area_receptora,
        motivo=request_data.motivo
    )

    # S6-005: PDF/A-3b para pases de expediente (documentos oficiales de archivo)
    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")
    return pdf_content


def generate_import_pdf(request_data: ImportRequest) -> bytes:
    """
    Genera la pagina informativa de documento importado.

    Args:
        request_data (ImportRequest): Objeto con los datos para el documento importado.

    Returns:
        bytes: El contenido del PDF de la pagina informativa generado.
    """
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    template = TEMPLATE_IMPORT

    html_content = template.render(
        logo_data=logo_data,
        name_acrony_type=request_data.NameAcronyType,
        document_type=request_data.document_type,
        reference=request_data.reference,
        frase_anual=request_data.frase_anual,
        cantidad_paginas=request_data.cantidad_paginas
    )

    # S6-005: PDF/A-3b para documentos importados (documentos oficiales de archivo)
    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")
    return pdf_content


def generate_note_pdf(request_data: NoteRequest) -> bytes:
    """
    Genera un PDF de nota sin marca de agua.

    Args:
        request_data (NoteRequest): Objeto con los datos para la nota.

    Returns:
        bytes: El contenido del PDF de la nota generado.
    """
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    template = TEMPLATE_NOTE

    # Extraer el HTML del campo Text
    text_html = sanitize_html(request_data.Text.get("html", ""))

    html_content = template.render(
        logo_data=logo_data,
        name_acrony_type=request_data.NameAcronyType,
        document_type=request_data.document_type,
        reference=request_data.reference,
        frase_anual=request_data.frase_anual,
        para=request_data.para,
        cc=request_data.cc,
        text_html=text_html
    )

    # S6-005: PDF/A-3b para notas finales (documentos oficiales de archivo)
    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")
    return pdf_content


def generate_note_preview_pdf(request_data: NoteRequest) -> bytes:
    """
    Genera un PDF de nota con marca de agua de previsualizacion.

    Args:
        request_data (NoteRequest): Objeto con los datos para la nota.

    Returns:
        bytes: El contenido del PDF de la nota con marca de agua generado.
    """
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    template = TEMPLATE_NOTE_PREVIEW

    # Extraer el HTML del campo Text
    text_html = sanitize_html(request_data.Text.get("html", ""))

    html_content = template.render(
        logo_data=logo_data,
        name_acrony_type=request_data.NameAcronyType,
        document_type=request_data.document_type,
        reference=request_data.reference,
        frase_anual=request_data.frase_anual,
        para=request_data.para,
        cc=request_data.cc,
        text_html=text_html
    )

    pdf_content = generate_pdf_from_html(html_content)
    return pdf_content


def merge_pdfs(base_pdf: bytes, attachment_pdf: bytes) -> bytes:
    """
    Concatena las paginas del attachment_pdf al final del base_pdf.

    Args:
        base_pdf: PDF generado por WeasyPrint.
        attachment_pdf: PDF adjunto a anexar.

    Returns:
        bytes: PDF combinado con todas las paginas.

    Raises:
        Exception: Si alguno de los PDFs no puede abrirse.
    """
    doc_base = fitz.open(stream=base_pdf, filetype="pdf")
    doc_attachment = fitz.open(stream=attachment_pdf, filetype="pdf")

    # Anexar todas las paginas del attachment al final
    doc_base.insert_pdf(doc_attachment)

    merged_pdf = doc_base.tobytes()

    doc_base.close()
    doc_attachment.close()

    return merged_pdf


def find_text_position_in_pdf(pdf_bytes: bytes, text_to_find: str) -> Optional[float]:
    """
    Busca un texto en un PDF y devuelve su posicion vertical (coordenada 'top').

    Args:
        pdf_bytes (bytes): El contenido del PDF en bytes.
        text_to_find (str): El texto a buscar.

    Returns:
        Optional[float]: La coordenada 'top' del texto si se encuentra, de lo contrario None.
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            for i, word in enumerate(words):
                if word.get("text") == text_to_find:
                    return word.get("top")
    return None
