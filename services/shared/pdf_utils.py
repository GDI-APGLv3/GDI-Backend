"""
Utilidades para manipulación de PDFs.
Maneja operaciones comunes de PDFs como agregar páginas para firma.
"""

import io
import os
from pypdf import PdfReader, PdfWriter
from shared.logging import get_logger

logger = get_logger(__name__)

# Ruta al archivo SignPage.pdf que contiene el marcador "end-text" para Notary
SIGN_PAGE_PATH = os.path.join(
    os.path.dirname(__file__),
    "assets",
    "SignPage.pdf"
)


def add_blank_page_to_pdf(pdf_bytes: bytes) -> bytes:
    """
    Agrega una página de firma (SignPage.pdf) al final de un PDF.

    Esta función es usada cuando Notary responde con FULLPAGE,
    indicando que no hay espacio para el sello de firma.

    La página agregada contiene el marcador "end-text" que Notary
    necesita para ubicar dónde colocar la firma.

    Args:
        pdf_bytes: Contenido binario del PDF original

    Returns:
        bytes: PDF con página de firma agregada al final

    Raises:
        Exception: Si hay error manipulando el PDF o si SignPage.pdf no existe

    Examples:
        >>> pdf_bytes = open('document.pdf', 'rb').read()
        >>> augmented_pdf = add_blank_page_to_pdf(pdf_bytes)
        >>> len(augmented_pdf) > len(pdf_bytes)
        True
    """
    try:
        logger.info(f"Agregando página de firma con marcador 'end-text'")
        logger.info(f"Tamaño original: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

        # Verificar que SignPage.pdf existe
        if not os.path.exists(SIGN_PAGE_PATH):
            raise Exception(f"No se encontró SignPage.pdf en: {SIGN_PAGE_PATH}")

        logger.info(f"Usando SignPage.pdf desde: {SIGN_PAGE_PATH}")

        # Leer PDF original
        reader = PdfReader(io.BytesIO(pdf_bytes))
        num_pages = len(reader.pages)
        logger.info(f"Páginas originales: {num_pages}")

        # Leer SignPage.pdf en memoria para evitar problemas de archivo cerrado
        with open(SIGN_PAGE_PATH, 'rb') as sign_file:
            sign_page_bytes = sign_file.read()

        sign_page_reader = PdfReader(io.BytesIO(sign_page_bytes))
        sign_page = sign_page_reader.pages[0]
        logger.info(f"SignPage.pdf cargado (contiene marcador 'end-text')")

        # Crear writer y copiar todas las páginas del documento original
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        # Agregar la página de firma al final
        writer.add_page(sign_page)
        logger.info(f"Página de firma agregada al final")

        # Escribir PDF a bytes
        output_buffer = io.BytesIO()
        writer.write(output_buffer)
        augmented_pdf = output_buffer.getvalue()

        logger.info(f"PDF aumentado exitosamente con página de firma")
        logger.info(f"Tamaño nuevo: {len(augmented_pdf)} bytes ({len(augmented_pdf)/1024:.2f} KB)")
        logger.info(f"Páginas nuevas: {num_pages + 1}")

        return augmented_pdf

    except Exception as e:
        logger.error(f"Error agregando página de firma: {type(e).__name__} - {str(e)}")
        raise Exception(f"Error manipulando PDF: {str(e)}")
