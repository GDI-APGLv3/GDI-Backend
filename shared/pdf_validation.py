"""
Utilidades de validación de PDFs para el flujo de firma digital.
"""

from shared.logging import get_logger

logger = get_logger(__name__)


def has_end_text_marker(pdf_bytes: bytes) -> bool:
    """
    Verifica si el PDF tiene el marker 'end-text' en la última página.
    Notary necesita este marker para ubicar dónde poner la firma.

    Usa pypdf (ya instalado en requirements.txt).

    NOTA sobre encoding: WeasyPrint genera PDFs con texto UTF-16, y
    pypdf.extract_text() devuelve cada caracter con un byte NUL ('\\x00')
    en el medio sin decodificar bien (la subcadena 'end-text' aparece
    como '\\x00e\\x00n\\x00d\\x00-\\x00t\\x00e\\x00x\\x00t'). Por eso
    limpiamos los \\x00 antes de buscar el marker.

    Notary usa PyMuPDF que decodifica UTF-16 correctamente y no necesita
    esta limpieza, pero PyMuPDF agrega ~30MB a la imagen Docker, asi que
    se prefirio mantener pypdf con este workaround.

    Returns:
        True si encuentra 'end-text' en la última página
        False si no lo encuentra o si hubo error parseando el PDF
    """
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
        import io

        reader = PdfReader(io.BytesIO(pdf_bytes))
        if not reader.pages:
            logger.warning("PDF sin paginas - retornando False")
            return False

        last_page_text = reader.pages[-1].extract_text() or ""
        # Limpiar bytes NUL del UTF-16 mal decodificado por pypdf
        last_page_text_cleaned = last_page_text.replace("\x00", "")
        return "end-text" in last_page_text_cleaned

    except PdfReadError as e:
        logger.error(f"PDF corrupto o invalido (pypdf no pudo parsear): {e}")
        return False  # Fail-safe
    except Exception as e:
        logger.error(f"Error inesperado validando end-text: {type(e).__name__}: {e}")
        return False  # Fail-safe
