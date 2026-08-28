
from shared.logging import get_logger

logger = get_logger(__name__)


def has_end_text_marker(pdf_bytes: bytes) -> bool:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
        import io

        reader = PdfReader(io.BytesIO(pdf_bytes))
        if not reader.pages:
            logger.warning("PDF sin paginas - retornando False")
            return False

        last_page_text = reader.pages[-1].extract_text() or ""
        last_page_text_cleaned = last_page_text.replace("\x00", "")
        return "end-text" in last_page_text_cleaned

    except PdfReadError as e:
        logger.error(f"PDF corrupto o invalido (pypdf no pudo parsear): {e}")
        return False
    except Exception as e:
        logger.error(f"Error inesperado validando end-text: {type(e).__name__}: {e}")
        return False
