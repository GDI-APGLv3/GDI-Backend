
import io
import os
from pypdf import PdfReader, PdfWriter
from shared.logging import get_logger

logger = get_logger(__name__)

SIGN_PAGE_PATH = os.path.join(os.path.dirname(__file__), "assets", "SignPage.pdf")


def add_blank_page_to_pdf(pdf_bytes: bytes) -> bytes:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        num_pages = len(reader.pages)
        logger.info(f"Agregando página de firma. Original: {num_pages} páginas, {len(pdf_bytes)/1024:.2f} KB")

        sign_page_reader = PdfReader(SIGN_PAGE_PATH)

        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_page(sign_page_reader.pages[0])

        output_buffer = io.BytesIO()
        writer.write(output_buffer)
        augmented_pdf = output_buffer.getvalue()

        logger.info(f"Página de firma agregada. Nuevo: {num_pages + 1} páginas, {len(augmented_pdf)/1024:.2f} KB")
        return augmented_pdf

    except Exception as e:
        logger.error(f"Error agregando página de firma: {type(e).__name__} - {str(e)}")
        raise Exception(f"Error manipulando PDF: {str(e)}")
