"""
Módulo de inserción de firmas digitales

Este módulo maneja la aplicación de firmas digitales en documentos PDF,
incluyendo la verificación de certificados y la inserción de elementos
visuales de firma en posiciones específicas del documento.

Características principales:
- Verificación de disponibilidad de certificados digitales
- Inserción de firmas digitales con elementos visuales
- Posicionamiento automático basado en detección de "end-text"
- Manejo de múltiples firmas en el mismo documento
- Integración con el sistema de coordenadas PDF

Nota: Esta es una versión simplificada que utiliza elementos visuales
en lugar de firmas criptográficas completas.

Autor: Sistema de Notarización Digital
Versión: 1.0.0
"""
from typing import Dict, Optional, Tuple
import os
import io
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import black, blue, Color, white
from reportlab.lib.units import inch
from PyPDF2 import PdfReader, PdfWriter
from . import config


class SignatureError(Exception):
    """
    Excepción personalizada para errores de firma digital
    
    Se utiliza para manejar errores específicos durante el proceso
    de firma digital, como problemas de certificados, posicionamiento
    o escritura del documento firmado.
    """
    pass


def get_signature_info() -> Dict[str, str]:
    """
    Obtiene información sobre el sistema de firma visual
    
    Returns:
        Dict[str, str]: Información del sistema de firma:
            - type: Tipo de sistema de firma
            - version: Versión del sistema
            - description: Descripción del sistema
    """
    return {
        "type": "digital_signature",
        "version": "2.0",
        "description": "Sistema de firma digital sin certificados PEM",
        "features": [
            "Firma digital profesional",
            "Layout automático 2 columnas", 
            "Posicionamiento inteligente",
            "Información completa del firmante"
        ]
    }


def sign_pdf_document(
    pdf_content: bytes,
    signature_params: Dict[str, str],
    x: float,
    y: float
) -> bytes:
    """
    Aplica una firma visual al documento PDF
    
    Esta función inserta elementos visuales de firma en el documento,
    incluyendo información del firmante, fecha y hora, y un indicador
    visual de que el documento ha sido firmado.
    
    Args:
        pdf_content (bytes): Contenido binario del PDF original
        signature_params (Dict[str, str]): Parámetros de la firma que incluyen:
            - name: Nombre del firmante
            - seal: Cargo o sello del firmante
            - department: Departamento
            - entity: Entidad
        x (float): Coordenada X para posicionar la firma (en puntos PDF)
        y (float): Coordenada Y para posicionar la firma (en puntos PDF)
        
    Returns:
        bytes: Contenido binario del PDF con la firma visual aplicada
        
    Raises:
        SignatureError: Si ocurre un error durante el proceso de firma
        
    Note:
        - Esta es una implementación de firma visual únicamente
        - La firma se aplica en la última página del documento
        - Utiliza el sistema de coordenadas PDF estándar
        - Los elementos visuales incluyen texto y líneas decorativas
        
    Example:
        >>> params = {
        ...     "name": "Juan Pérez",
        ...     "seal": "Director",
        ...     "department": "Administración",
        ...     "entity": "Empresa XYZ"
        ... }
        >>> signed_pdf = sign_pdf_document(pdf_bytes, params, 100, 200)
    """
    # Validar parámetros requeridos
    required_params = ["name", "seal", "department", "entity"]
    for param in required_params:
        if not signature_params.get(param):
            raise SignatureError(f"Parámetro requerido faltante: {param}")
    
    try:
        # Leer el PDF original
        pdf_reader = PdfReader(io.BytesIO(pdf_content))
        pdf_writer = PdfWriter()
        
        # Crear overlay con la firma visual
        signature_overlay = create_signature_overlay(signature_params, x, y)
        
        # Procesar cada página
        for page_num, page in enumerate(pdf_reader.pages):
            if page_num == len(pdf_reader.pages) - 1:  # Solo firmar la ÚLTIMA página
                # Combinar la página original con el overlay de firma
                page.merge_page(signature_overlay)
            pdf_writer.add_page(page)
        
        # Generar el PDF resultante
        output_buffer = io.BytesIO()
        pdf_writer.write(output_buffer)
        return output_buffer.getvalue()
        
    except Exception as e:
        raise SignatureError(f"Error al procesar PDF: {str(e)}")


def create_signature_overlay(signature_params: Dict[str, str], x: float, y: float) -> object:
    """
    Crea un overlay con la firma visual
    
    Args:
        signature_params: Parámetros de la firma
        x: Coordenada X
        y: Coordenada Y
        
    Returns:
        Página PDF con la firma visual
    """
    # Crear un buffer para el overlay
    overlay_buffer = io.BytesIO()

    # Crear canvas con reportlab
    c = canvas.Canvas(overlay_buffer, pagesize=A4)

    # Configurar fuente
    c.setFont("Helvetica-Bold", 10)

    # Dibujar rectángulo de firma
    c.setStrokeColor(white)  # Línea blanca (invisible pero presente)
    c.setLineWidth(0.5)  # Línea muy delgada
    c.rect(x, y, config.SIGNATURE_WIDTH, config.SIGNATURE_HEIGHT)

    # Añadir texto de la firma - FORMATO LIMPIO
    c.setFillColor(black)
    
    # Información del firmante
    text_y = y + config.SIGNATURE_HEIGHT - 15
    
    # Nombre (Bold, 10pts)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x + 5, text_y, signature_params["name"])
    
    # Sello, departamento, entidad (Regular, 10pts)
    text_y -= 12
    c.setFont("Helvetica", 10)
    c.drawString(x + 5, text_y, signature_params["seal"])
    
    text_y -= 11
    c.drawString(x + 5, text_y, signature_params["department"])
    
    text_y -= 11
    c.drawString(x + 5, text_y, signature_params["entity"])
    
    # Línea inferior: Identificador | Fecha (tamaño 7)
    text_y -= 14
    c.setFont("Helvetica", 7)
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    footer_text = f"Digitally Signed by TEST SERVER | {current_time}"
    c.drawString(x + 5, text_y, footer_text)

    # Finalizar el canvas
    c.save()
    
    # Convertir a página PDF
    overlay_buffer.seek(0)
    overlay_reader = PdfReader(overlay_buffer)
    return overlay_reader.pages[0]


def validate_signature_position(x: float, y: float) -> bool:
    """
    Valida que la posición de la firma esté dentro de los límites
    
    Args:
        x: Coordenada X
        y: Coordenada Y
        
    Returns:
        bool: True si la posición es válida
    """
    # Verificar límites de página
    if x < 0 or y < 0:
        return False
    
    if x + config.SIGNATURE_WIDTH > config.LETTER_WIDTH:
        return False
    
    if y + config.SIGNATURE_HEIGHT > config.LETTER_HEIGHT:
        return False
    
    return True
