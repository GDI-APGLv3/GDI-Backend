
import os
import functools
import requests
import base64
import contextlib
import io
import re
import socket
import ipaddress
import logging
import threading
import pdfplumber
import nh3
import concurrent.futures
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from typing import Union, Optional, Tuple
import fitz
from urllib.parse import urlparse, urlunparse
from weasyprint import HTML
from weasyprint.text.fonts import FontConfiguration
from weasyprint.urls import default_url_fetcher
from app.models.pdf_models import PDFRequest, CaseRequest, MoveRequest, ImportRequest, NoteRequest, IFRLMRequest
from datetime import datetime, timezone
from app.config import DEFAULT_TIMEOUT, LOGO_FETCH_TIMEOUT, PDF_GENERATION_TIMEOUT, WEASYPRINT_RESOURCE_TIMEOUT

logger = logging.getLogger(__name__)

SIGNATURE_ANCHOR_TEXT = "end-text"

templates_dir = Path(__file__).parent.parent / "templates"
env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)

FONT_CONFIG = FontConfiguration()

TEMPLATE_PREVIEW = env.get_template("plantilla.html")
TEMPLATE_GENERATE = env.get_template("generate-pdf.html")
TEMPLATE_CASE = env.get_template("caratula.html")
TEMPLATE_MOVE = env.get_template("movimiento.html")
TEMPLATE_IMPORT = env.get_template("Importado.html")
TEMPLATE_NOTE = env.get_template("nota.html")
TEMPLATE_NOTE_PREVIEW = env.get_template("nota_preview.html")
TEMPLATE_IFRLM = env.get_template("ifrlm.html")

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

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}

_TRUSTED_HOST_EXACT = frozenset(
    h.strip().lower()
    for h in os.getenv("PDF_TRUSTED_HOSTS", "").split(",")
    if h.strip()
)
_TRUSTED_HOST_SUFFIXES = tuple(
    h.strip().lower()
    for h in os.getenv(
        "PDF_TRUSTED_HOST_SUFFIXES", ".r2.cloudflarestorage.com"
    ).split(",")
    if h.strip()
)
_TRUSTED_R2_DEV_RE = re.compile(r"^pub-[0-9a-f]+\.r2\.dev$", re.IGNORECASE)


def _is_trusted_host(hostname_lower: str) -> bool:
    if hostname_lower in _TRUSTED_HOST_EXACT:
        return True
    if hostname_lower.endswith(_TRUSTED_HOST_SUFFIXES):
        return True
    if _TRUSTED_R2_DEV_RE.match(hostname_lower):
        return True
    return False


def validate_url_safety(url: str) -> Optional[str]:
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Scheme no permitido: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL sin hostname")

    hostname_lower = hostname.lower()
    if hostname_lower in BLOCKED_HOSTNAMES:
        raise ValueError(f"Hostname bloqueado: {hostname}")

    if hostname_lower.endswith(".internal"):
        raise ValueError(f"Hostname interno bloqueado: {hostname}")

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise ValueError(f"No se pudo resolver hostname: {hostname}")

    resolved_ip = None
    for addr_info in addr_infos:
        ip = ipaddress.ip_address(addr_info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"IP privada/reservada bloqueada: {ip}")
        if resolved_ip is None:
            resolved_ip = str(ip)

    if _is_trusted_host(hostname_lower):
        return None

    return resolved_ip


_dns_pin_lock = threading.Lock()


@contextlib.contextmanager
def _pin_dns_to_ip(hostname: str, ip: str):
    with _dns_pin_lock:
        original_getaddrinfo = socket.getaddrinfo

        def _pinned_getaddrinfo(host, *args, **kwargs):
            if host == hostname:
                return original_getaddrinfo(ip, *args, **kwargs)
            return original_getaddrinfo(host, *args, **kwargs)

        socket.getaddrinfo = _pinned_getaddrinfo
        try:
            yield
        finally:
            socket.getaddrinfo = original_getaddrinfo


_DATA_HREF_RE = re.compile(r'(<a\b[^>]*\bhref\s*=\s*)(["\'])data:(?!image/)([^"\']*)(\2)', re.IGNORECASE)


def _strip_non_image_data_href(html_content: str) -> str:
    return _DATA_HREF_RE.sub(lambda m: f'{m.group(1)}{m.group(2)}#{m.group(2)}', html_content)


def sanitize_html(html_content: str) -> str:
    if not html_content:
        return ""
    cleaned = nh3.clean(
        html_content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto", "data"},
    )
    return _strip_non_image_data_href(cleaned)

def safe_url_fetcher(url, timeout=None, ssl_context=None):
    if url.startswith("data:"):
        return default_url_fetcher(url, timeout=WEASYPRINT_RESOURCE_TIMEOUT, ssl_context=ssl_context)

    if url.startswith("file:"):
        raise ValueError(f"URI file: bloqueada por seguridad: {url}")

    try:
        pinned_ip = validate_url_safety(url)
    except ValueError as e:
        logger.warning(f"[S6-003] safe_url_fetcher: URL bloqueada SSRF url={url} err={e}")
        raise

    try:
        if pinned_ip:
            with _pin_dns_to_ip(urlparse(url).hostname, pinned_ip):
                return default_url_fetcher(url, timeout=WEASYPRINT_RESOURCE_TIMEOUT, ssl_context=ssl_context)
        return default_url_fetcher(url, timeout=WEASYPRINT_RESOURCE_TIMEOUT, ssl_context=ssl_context)
    except Exception as e:
        logger.warning(f"[S6-003] safe_url_fetcher: fetch fallido url={url} timeout={WEASYPRINT_RESOURCE_TIMEOUT}s err={type(e).__name__}: {e}")
        raise


MAX_LOGO_BYTES = 2 * 1024 * 1024

ALLOWED_LOGO_MIMES = {
    "image/png", "image/jpeg", "image/jpg",
    "image/svg+xml", "image/webp", "image/gif",
}


@functools.lru_cache(maxsize=128)
def _fetch_logo_cached(url: str) -> Optional[Tuple[bytes, str]]:
    try:
        pinned_ip = validate_url_safety(url)
        if pinned_ip:
            with _pin_dns_to_ip(urlparse(url).hostname, pinned_ip):
                response = requests.get(url, timeout=LOGO_FETCH_TIMEOUT)
        else:
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
    if not url:
        return None
    result = _fetch_logo_cached(url)
    if result is None:
        return None
    content, mime = result
    return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def get_logo_cache_stats() -> dict:
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


def get_image_as_base64(url: str) -> Union[str, None]:
    if not url:
        return None
    try:
        pinned_ip = validate_url_safety(url)
        if pinned_ip:
            with _pin_dns_to_ip(urlparse(url).hostname, pinned_ip):
                response = requests.get(url, timeout=DEFAULT_TIMEOUT)
        else:
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
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    template = TEMPLATE_PREVIEW

    text_html = sanitize_html(request_data.Text.get("html", ""))

    html_content = template.render(
        logo_data=logo_data,
        name_acrony_type=request_data.NameAcronyType,
        document_type=request_data.TypeDocument,
        reference=request_data.Reference,
        frase_anual=request_data.frase_anual,
        text=text_html
    )

    pdf_content = generate_pdf_from_html(html_content)

    return pdf_content

def generate_case_pdf(request_data: CaseRequest) -> bytes:
    logo_data = get_logo_base64_cached(request_data.urlLogo)

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

    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")
    return pdf_content

def generate_ifrlm_pdf(request_data: IFRLMRequest) -> bytes:
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    generated_at_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    template = TEMPLATE_IFRLM

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

    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")
    return pdf_content

def generate_general_pdf(request_data: PDFRequest) -> bytes:
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    template = TEMPLATE_GENERATE

    text_html = sanitize_html(request_data.Text.get("html", ""))

    html_content = template.render(
        logo_data=logo_data,
        name_acrony_type=request_data.NameAcronyType,
        document_type=request_data.TypeDocument,
        reference=request_data.Reference,
        frase_anual=request_data.frase_anual,
        text=text_html
    )

    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")

    return pdf_content


def generate_move_pdf(request_data: MoveRequest) -> bytes:
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

    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")
    return pdf_content


def generate_import_pdf(request_data: ImportRequest) -> bytes:
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

    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")
    return pdf_content


def generate_note_pdf(request_data: NoteRequest) -> bytes:
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    template = TEMPLATE_NOTE

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

    pdf_content = generate_pdf_from_html(html_content, pdf_variant="pdf/a-3b")
    return pdf_content


def generate_note_preview_pdf(request_data: NoteRequest) -> bytes:
    logo_data = get_logo_base64_cached(request_data.urlLogo)

    template = TEMPLATE_NOTE_PREVIEW

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
    doc_base = fitz.open(stream=base_pdf, filetype="pdf")
    doc_attachment = fitz.open(stream=attachment_pdf, filetype="pdf")

    doc_base.insert_pdf(doc_attachment)

    merged_pdf = doc_base.tobytes()

    doc_base.close()
    doc_attachment.close()

    return merged_pdf


def embed_files_in_pdf(base_pdf: bytes, embedded: list) -> bytes:
    doc = fitz.open(stream=base_pdf, filetype="pdf")

    used_names: dict[str, int] = {}
    for file_name, content in embedded:
        safe_name = file_name
        if safe_name in used_names:
            used_names[safe_name] += 1
            stem, dot, ext = file_name.rpartition(".")
            if dot:
                safe_name = f"{stem}_{used_names[file_name]}.{ext}"
            else:
                safe_name = f"{file_name}_{used_names[file_name]}"
        else:
            used_names[safe_name] = 1

        doc.embfile_add(safe_name, content, filename=safe_name, desc="Adjunto GDI")

    result = doc.tobytes()
    doc.close()

    return result


def find_text_position_in_pdf(pdf_bytes: bytes, text_to_find: str) -> Optional[float]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            for i, word in enumerate(words):
                if word.get("text") == text_to_find:
                    return word.get("top")
    return None
