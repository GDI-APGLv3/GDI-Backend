from typing import Dict, Optional, Tuple
import os
import io
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import black, blue, Color, white
from reportlab.lib.units import inch
from pypdf import PdfReader, PdfWriter
from . import config


class SignatureError(Exception):
    pass


def get_signature_info() -> Dict[str, str]:
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
    required_params = ["name", "seal", "department", "entity"]
    for param in required_params:
        if not signature_params.get(param):
            raise SignatureError(f"Parámetro requerido faltante: {param}")
    
    try:
        pdf_reader = PdfReader(io.BytesIO(pdf_content))
        pdf_writer = PdfWriter()
        pdf_writer.clone_document_from_reader(pdf_reader)

        signature_overlay = create_signature_overlay(signature_params, x, y)

        last_index = len(pdf_writer.pages) - 1
        for page_num, page in enumerate(pdf_writer.pages):
            if page_num == last_index:
                page.merge_page(signature_overlay)

        output_buffer = io.BytesIO()
        pdf_writer.write(output_buffer)
        return output_buffer.getvalue()
        
    except Exception as e:
        raise SignatureError(f"Error al procesar PDF: {str(e)}")


def create_signature_overlay(signature_params: Dict[str, str], x: float, y: float) -> object:
    overlay_buffer = io.BytesIO()

    c = canvas.Canvas(overlay_buffer, pagesize=A4)

    c.setFont("Helvetica-Bold", 10)

    c.setStrokeColor(white)
    c.setLineWidth(0.5)
    c.rect(x, y, config.SIGNATURE_WIDTH, config.SIGNATURE_HEIGHT)

    c.setFillColor(black)
    
    text_y = y + config.SIGNATURE_HEIGHT - 15
    
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x + 5, text_y, signature_params["name"])
    
    text_y -= 12
    c.setFont("Helvetica", 10)
    c.drawString(x + 5, text_y, signature_params["seal"])
    
    text_y -= 11
    c.drawString(x + 5, text_y, signature_params["department"])
    
    text_y -= 11
    c.drawString(x + 5, text_y, signature_params["entity"])
    
    text_y -= 14
    c.setFont("Helvetica", 7)
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    footer_text = f"Digitally Signed by TEST SERVER | {current_time}"
    c.drawString(x + 5, text_y, footer_text)

    c.save()
    
    overlay_buffer.seek(0)
    overlay_reader = PdfReader(overlay_buffer)
    return overlay_reader.pages[0]


def validate_signature_position(x: float, y: float) -> bool:
    if x < 0 or y < 0:
        return False
    
    if x + config.SIGNATURE_WIDTH > config.LETTER_WIDTH:
        return False
    
    if y + config.SIGNATURE_HEIGHT > config.LETTER_HEIGHT:
        return False
    
    return True
