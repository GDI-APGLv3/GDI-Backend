import re
import struct

from config.constants import EMBEDDED_FILE_ALLOWED_EXTENSIONS
from shared.exceptions import ValidationError

_OOXML_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_PDF_MAGIC = b"%PDF"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

_UTF8_BOM = b"\xef\xbb\xbf"
_DXF_SEARCH_WINDOW = 256


def _extension_from_filename(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _looks_like_ooxml_docx_or_xlsx(content: bytes) -> bool:
    return b"[Content_Types].xml" in content[:4096] or b"[Content_Types].xml" in content


def _looks_like_odf(content: bytes) -> bool:
    if len(content) < 30 or content[:4] != b"PK\x03\x04":
        return False
    try:
        compression_method = struct.unpack_from("<H", content, 8)[0]
        name_len = struct.unpack_from("<H", content, 26)[0]
    except struct.error:
        return False
    name_start = 30
    name_end = name_start + name_len
    if name_end > len(content):
        return False
    return content[name_start:name_end] == b"mimetype" and compression_method == 0


def _looks_like_dxf(content: bytes) -> bool:
    window = content[:_DXF_SEARCH_WINDOW]
    if window.startswith(_UTF8_BOM):
        window = window[len(_UTF8_BOM):]
    return b"SECTION" in window or window.lstrip().startswith(b"999")


def _looks_like_text(content: bytes) -> bool:
    if not content:
        return True
    sample = content[:8192]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8-sig")
        return True
    except UnicodeDecodeError:
        pass
    try:
        sample.decode("latin-1")
        return True
    except UnicodeDecodeError:
        return False


def validate_embedded_file(content: bytes, original_name: str) -> str:
    if not content:
        raise ValidationError("El archivo está vacío")

    ext = _extension_from_filename(original_name)
    if ext not in EMBEDDED_FILE_ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Extensión '.{ext or '?'}' no permitida. "
            f"Extensiones válidas: {', '.join(sorted(EMBEDDED_FILE_ALLOWED_EXTENSIONS))}"
        )

    if ext == "pdf":
        if content.startswith(_PDF_MAGIC):
            return ext
        raise ValidationError("El archivo no es un PDF válido")

    if ext == "png":
        if content.startswith(_PNG_MAGIC):
            return ext
        raise ValidationError("El archivo no es un PNG válido")

    if ext in ("jpg", "jpeg"):
        if content.startswith(_JPEG_MAGIC):
            return ext
        raise ValidationError("El archivo no es un JPEG válido")

    if ext in ("xlsx", "docx"):
        if content.startswith(_OOXML_MAGIC) and _looks_like_ooxml_docx_or_xlsx(content):
            return ext
        raise ValidationError(f"El archivo no es un .{ext} válido (se esperaba OOXML)")

    if ext in ("odt", "ods"):
        if content.startswith(_OOXML_MAGIC) and _looks_like_odf(content):
            return ext
        raise ValidationError(f"El archivo no es un .{ext} válido (se esperaba OpenDocument)")

    if ext in ("xls", "doc"):
        if content.startswith(_OLE2_MAGIC):
            return ext
        raise ValidationError(f"El archivo no es un .{ext} válido (se esperaba OLE2/Compound File)")

    if ext == "dxf":
        if _looks_like_dxf(content):
            return ext
        raise ValidationError("El archivo no es un DXF válido")

    if ext in ("csv", "txt"):
        if _looks_like_text(content):
            return ext
        raise ValidationError(f"El archivo no parece ser un .{ext} de texto válido")

    raise ValidationError(f"Extensión '.{ext}' no soportada")


def sanitize_embedded_file_name(original_name: str) -> str:
    name = original_name.strip() or "archivo"
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)

    if len(safe) <= 200:
        return safe

    if "." in safe:
        stem, ext = safe.rsplit(".", 1)
        ext = ext[:16]
        max_stem = 200 - len(ext) - 1
        return f"{stem[:max_stem]}.{ext}"

    return safe[:200]
