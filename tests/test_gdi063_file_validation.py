import struct

import pytest

from services.shared.file_validation import (
    validate_embedded_file,
    sanitize_embedded_file_name,
)
from shared.exceptions import ValidationError


def _ooxml_bytes(marker_entry: bytes = b"[Content_Types].xml") -> bytes:
    return b"PK\x03\x04" + b"\x00" * 26 + marker_entry + b"\x00" * 50


def _zip_local_file_header(filename: bytes, data: bytes, *, compression_method: int = 0) -> bytes:
    header = bytearray(b"PK\x03\x04")
    header += struct.pack("<H", 20)
    header += struct.pack("<H", 0)
    header += struct.pack("<H", compression_method)
    header += struct.pack("<H", 0)
    header += struct.pack("<H", 0)
    header += struct.pack("<I", 0)
    header += struct.pack("<I", len(data))
    header += struct.pack("<I", len(data))
    header += struct.pack("<H", len(filename))
    header += struct.pack("<H", 0)
    header += filename
    header += data
    return bytes(header)


def _odf_bytes() -> bytes:
    return _zip_local_file_header(b"mimetype", b"application/vnd.oasis.opendocument.text")


def _ole2_bytes() -> bytes:
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100


PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 50
XLSX_BYTES = _ooxml_bytes()
DOCX_BYTES = _ooxml_bytes()
ODT_BYTES = _odf_bytes()
ODS_BYTES = _odf_bytes()
XLS_BYTES = _ole2_bytes()
DOC_BYTES = _ole2_bytes()
CSV_BYTES = "col1,col2\nvalor1,valor2\n".encode("utf-8")
TXT_BYTES = "hola mundo\ncon acentos: ñ á é".encode("utf-8")
DXF_BYTES = b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n"
DXF_BYTES_WITH_BOM = b"\xef\xbb\xbf" + DXF_BYTES


@pytest.mark.parametrize(
    "filename,content,expected_ext",
    [
        ("informe.pdf", PDF_BYTES, "pdf"),
        ("foto.png", PNG_BYTES, "png"),
        ("foto.jpg", JPEG_BYTES, "jpg"),
        ("foto.jpeg", JPEG_BYTES, "jpeg"),
        ("planilla.xlsx", XLSX_BYTES, "xlsx"),
        ("documento.docx", DOCX_BYTES, "docx"),
        ("documento.odt", ODT_BYTES, "odt"),
        ("planilla.ods", ODS_BYTES, "ods"),
        ("planilla_vieja.xls", XLS_BYTES, "xls"),
        ("documento_viejo.doc", DOC_BYTES, "doc"),
        ("datos.csv", CSV_BYTES, "csv"),
        ("notas.txt", TXT_BYTES, "txt"),
        ("plano.dxf", DXF_BYTES, "dxf"),
        ("plano_con_bom.dxf", DXF_BYTES_WITH_BOM, "dxf"),
    ],
)
def test_validate_embedded_file_formatos_validos(filename, content, expected_ext):
    assert validate_embedded_file(content, filename) == expected_ext


def test_validate_embedded_file_exe_renombrado_a_docx_rechaza():
    exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff"
    with pytest.raises(ValidationError):
        validate_embedded_file(exe_bytes, "malicioso.docx")


def test_validate_embedded_file_extension_fuera_de_allowlist_rechaza():
    with pytest.raises(ValidationError):
        validate_embedded_file(b"PK\x03\x04algo", "archivo.zip")

    with pytest.raises(ValidationError):
        validate_embedded_file(b"algo", "plano.dwg")


def test_validate_embedded_file_vacio_rechaza():
    with pytest.raises(ValidationError):
        validate_embedded_file(b"", "vacio.pdf")


def test_validate_embedded_file_pdf_con_extension_pero_bytes_incorrectos_rechaza():
    with pytest.raises(ValidationError):
        validate_embedded_file(b"esto no es un pdf", "falso.pdf")


def test_validate_embedded_file_xlsx_zip_generico_sin_content_types_rechaza():
    generic_zip = b"PK\x03\x04" + b"\x00" * 40 + b"cualquier_cosa.txt"
    with pytest.raises(ValidationError):
        validate_embedded_file(generic_zip, "no_es_excel.xlsx")


def test_validate_embedded_file_zip_polyglot_dual_entry_rechaza_como_odf():
    polyglot = _zip_local_file_header(b"notmimetype", b"contenido cualquiera", compression_method=8)
    with pytest.raises(ValidationError):
        validate_embedded_file(polyglot, "falso.odt")


def test_validate_embedded_file_txt_con_bom_utf8_acepta():
    content = b"\xef\xbb\xbf" + "hola mundo con BOM".encode("utf-8")
    assert validate_embedded_file(content, "notas_bom.txt") == "txt"


def test_sanitize_embedded_file_name_reemplaza_caracteres_invalidos():
    result = sanitize_embedded_file_name("plano final(1).dxf")
    assert result == "plano_final_1_.dxf"
    result_accented = sanitize_embedded_file_name("plano áéí Ñ.dxf")
    assert "á" not in result_accented
    assert "é" not in result_accented
    assert "Ñ" not in result_accented
    assert result_accented.endswith(".dxf")


def test_sanitize_embedded_file_name_trunca_preservando_extension():
    long_name = ("a" * 300) + ".pdf"
    result = sanitize_embedded_file_name(long_name)
    assert len(result) <= 200
    assert result.endswith(".pdf")


def test_sanitize_embedded_file_name_nombre_vacio_usa_default():
    assert sanitize_embedded_file_name("   ") == "archivo"
