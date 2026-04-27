"""
Módulo de estampado de documentos PDF

Este módulo maneja la aplicación de sellos o estampas en documentos PDF,
agregando información como número de documento, ciudad y fecha en la primera página.

Características principales:
- Estampado en la primera página del documento
- Formato de fecha en español
- Posicionamiento configurable del sello
- Texto en negro sobre fondo transparente
- Integración con el sistema de coordenadas PDF

Autor: Sistema de Notarización Digital
Versión: 1.0.0
"""
from datetime import datetime
from typing import Tuple
import io
from reportlab.pdfgen import canvas
from reportlab.lib.colors import black
from reportlab.lib.units import inch
from PyPDF2 import PdfReader, PdfWriter
from . import config


class StampError(Exception):
    """
    Excepción personalizada para errores de estampado
    
    Se utiliza para manejar errores específicos durante el proceso
    de estampado de documentos, como problemas de formato o escritura.
    """
    pass


def format_date_spanish() -> str:
    """
    Formatea la fecha actual en español para el estampado
    
    Genera una fecha en formato legible en español siguiendo el patrón:
    "día de mes de año" (ej: "15 de enero de 2025")
    
    Returns:
        str: Fecha formateada en español
        
    Example:
        >>> date_str = format_date_spanish()
        >>> print(date_str)  # "15 de enero de 2025"
    """
    now = datetime.now()
    
    months = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    
    month_name = months[now.month - 1]
    formatted_date = f"{now.day} de {month_name} de {now.year}"
    
    return formatted_date


def create_stamp_text(document_number: str, city: str = "Bogotá") -> Tuple[str, str]:
    """
    Crea las líneas de texto para el sello del documento
    
    Genera el texto que aparecerá en el sello, incluyendo el número
    de documento y la información de ciudad y fecha.
    
    Args:
        document_number (str): Número del documento a estampar
        city (str, optional): Ciudad para el sello. Por defecto "Bogotá"
        
    Returns:
        Tuple[str, str]: Tupla con (línea_documento, línea_ciudad_fecha)
            - línea_documento: Número del documento
            - línea_ciudad_fecha: Ciudad y fecha formateada
            
    Example:
        >>> doc_line, city_line = create_stamp_text("12345678", "Medellín")
        >>> print(doc_line)      # "12345678"
        >>> print(city_line)     # "Medellín, 15 de enero de 2025"
    """
    document_line = document_number
    city_date_line = f"{city}, {format_date_spanish()}"
    
    return document_line, city_date_line


def create_stamp_overlay(document_number: str, city: str) -> bytes:
    """
    Crea un overlay PDF con el estampado para superponer al documento
    
    Genera un PDF temporal que contiene únicamente el sello con el número
    de documento y la información de ciudad/fecha, posicionado según las
    coordenadas configuradas.
    
    Args:
        document_number (str): Número del documento a incluir en el sello
        city (str): Ciudad para incluir en el sello
        
    Returns:
        bytes: Contenido binario del PDF overlay con el estampado
        
    Raises:
        StampError: Si ocurre un error durante la creación del overlay
        
    Note:
        - Utiliza tamaño de página Letter (612x792 puntos)
        - Posiciona el texto según coordenadas configuradas en config.py
        - El texto se dibuja en negro sin fondo
        - Utiliza fuente Helvetica de 11 puntos
        
    Example:
        >>> overlay_bytes = create_stamp_overlay("12345678", "Bogotá")
        >>> len(overlay_bytes) > 0  # True
    """
    buffer = io.BytesIO()
    from reportlab.lib.pagesizes import A4
    c = canvas.Canvas(buffer, pagesize=A4)  # A4 size (595.28 x 841.89)

    # Coordenadas alineadas al template HTML de WeasyPrint (@page { size: A4; margin: 10mm })
    # En PDF: Y=0 es abajo, Y=842 es arriba (A4)
    # X=28 ≈ 10mm (margen izquierdo del template HTML)
    DOC_NUMBER_X = 28
    DOC_NUMBER_Y = 755  # Número del documento (segundo, más abajo) - 87pts desde top
    CITY_DATE_X = 28
    CITY_DATE_Y = 770   # Ciudad y fecha (primero, más arriba) - 72pts desde top
    
    # Crear texto del estampado
    document_line, city_date_line = create_stamp_text(document_number, city)
    
    # Configurar fuente Montserrat 11pts
    c.setFont("Helvetica", 11)  # Usar Helvetica como fallback
    
    # Dibujar texto en negro (sin fondo amarillo)
    c.setFillColor(black)
    c.drawString(DOC_NUMBER_X, DOC_NUMBER_Y, document_line)
    c.drawString(CITY_DATE_X, CITY_DATE_Y, city_date_line)
    
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def stamp_document(
    pdf_content: bytes,
    document_number: str,
    city: str = "Bogotá",
    page_position: str = "first"
) -> bytes:
    """
    Aplica un sello con información del documento en la página especificada del PDF

    Esta función toma un PDF existente y le aplica un sello que incluye
    el número del documento, ciudad y fecha actual en la página indicada.
    El sello se posiciona según las coordenadas configuradas.

    Args:
        pdf_content (bytes): Contenido binario del PDF original
        document_number (str): Número del documento para incluir en el sello
        city (str, optional): Ciudad para el sello. Por defecto "Bogotá"
        page_position (str, optional): Posición del estampado.
            'first' para primera página (default), 'last' para última página.

    Returns:
        bytes: Contenido binario del PDF con el sello aplicado

    Raises:
        StampError: Si ocurre un error durante el proceso de estampado,
                   como problemas de lectura del PDF o escritura del resultado

    Note:
        - El sello se aplica en la página indicada por page_position
        - Las demás páginas se mantienen sin modificar
        - El sello se superpone al contenido existente
        - Utiliza el sistema de coordenadas PDF estándar

    Example:
        >>> with open("documento.pdf", "rb") as f:
        ...     pdf_bytes = f.read()
        >>> stamped_pdf = stamp_document(pdf_bytes, "12345678", "Medellín", "last")
        >>> with open("documento_sellado.pdf", "wb") as f:
        ...     f.write(stamped_pdf)
    """
    try:
        # Leer el PDF original
        pdf_reader = PdfReader(io.BytesIO(pdf_content))
        pdf_writer = PdfWriter()

        # Crear el overlay con el estampado
        stamp_overlay_bytes = create_stamp_overlay(document_number, city)
        stamp_reader = PdfReader(io.BytesIO(stamp_overlay_bytes))
        stamp_page = stamp_reader.pages[0]

        # Calcular total de páginas para determinar página objetivo
        total_pages = len(pdf_reader.pages)

        # Procesar todas las páginas
        for i, page in enumerate(pdf_reader.pages):
            # Determinar si esta es la página objetivo para estampado
            if page_position == "last":
                is_target = (i == total_pages - 1)
            else:  # "first" por defecto
                is_target = (i == 0)

            if is_target:
                page.merge_page(stamp_page)
            pdf_writer.add_page(page)

        # Generar el PDF final
        output_buffer = io.BytesIO()
        pdf_writer.write(output_buffer)
        output_buffer.seek(0)

        return output_buffer.getvalue()

    except Exception as e:
        raise StampError(f"Error applying stamp: {str(e)}")


def validate_stamp_area(x: float, y: float, width: float, height: float) -> bool:
    """
    Valida que el área del sello esté dentro de los límites de la página
    
    Args:
        x: Coordenada X del sello
        y: Coordenada Y del sello
        width: Ancho del sello
        height: Alto del sello
        
    Returns:
        bool: True si el área es válida
    """
    # Verificar límites de página Letter
    if x < 0 or y < 0:
        return False
    
    if x + width > config.LETTER_WIDTH or y + height > config.LETTER_HEIGHT:
        return False
    
    return True