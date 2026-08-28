from typing import Dict, List, Tuple, Optional
import io
import logging
import fitz
from . import config

logger = logging.getLogger(__name__)


class LayoutError(Exception):
    pass


def find_end_text_positions(pdf_content: bytes) -> List[Tuple[float, float]]:
    try:
        doc = fitz.open(stream=pdf_content, filetype="pdf")
        if len(doc) == 0:
            return []
        
        last_page = doc[-1]
        text_instances = last_page.search_for("end-text")
        
        positions = []
        for rect in text_instances:
            positions.append((rect.x0, rect.y0))
        
        doc.close()
        return positions
        
    except Exception:
        return []


def count_existing_signatures(pdf_content: bytes) -> int:
    try:
        visual_count = 0
        try:
            doc = fitz.open(stream=pdf_content, filetype="pdf")
            if len(doc) > 0:
                last_page = doc[-1]
                page_text = last_page.get_text()

                visual_count = page_text.count(config.SIGNATURE_DETECTION_TEXT)

                if visual_count == 0:
                    other_patterns = [
                        "Firmado digitalmente por:",
                        "Digitally signed by:",
                        "FIRMA DIGITAL",
                        "DIGITAL SIGNATURE"
                    ]
                    for pattern in other_patterns:
                        count = page_text.count(pattern)
                        if count > 0:
                            visual_count = count
                            break
            doc.close()
        except Exception:
            visual_count = 0

        from .pades_signer import count_pades_signatures
        pades_count = count_pades_signatures(pdf_content)

        total = max(visual_count, pades_count)
        logger.info(f"Firmas detectadas: visual={visual_count}, pades={pades_count}, total={total}")
        return total

    except Exception:
        return 0


def get_first_signature_y_position(pdf_content: bytes) -> Optional[float]:
    try:
        doc = fitz.open(stream=pdf_content, filetype="pdf")
        if len(doc) == 0:
            return None
        
        last_page = doc[-1]
        text_instances = last_page.search_for("end-text")
        
        if not text_instances:
            logger.info("No se encontró el texto 'end-text' en el documento")
            doc.close()
            return None
        
        text_rect = text_instances[0]
        text_pymupdf_y_bottom = text_rect.y1

        logger.info(f"Texto 'end-text' encontrado en PyMuPDF: x={text_rect.x0:.2f}, y0={text_rect.y0:.2f}, y1(bottom)={text_rect.y1:.2f}")
        
        page_height = last_page.rect.height
        
        signature_pymupdf_y = text_pymupdf_y_bottom + config.SIGNATURE_OFFSET_BELOW + config.SIGNATURE_HEIGHT

        reportlab_y = page_height - signature_pymupdf_y

        logger.info(f"Primera firma calculada - PyMuPDF: y={signature_pymupdf_y:.2f}, ReportLab: y={reportlab_y:.2f}")
        logger.info(f"Gap real entre end-text y firma: {config.SIGNATURE_OFFSET_BELOW}pts (~{config.SIGNATURE_OFFSET_BELOW / 2.835:.0f}mm)")
        
        doc.close()
        return reportlab_y
        
    except Exception:
        return None


def calculate_signature_position(existing_signatures_count: int = 0, pdf_content: bytes = None) -> Tuple[float, float]:
    try:
        if pdf_content:
            first_signature_y = get_first_signature_y_position(pdf_content)
            if first_signature_y is None:
                raise LayoutError("No se encontró el texto 'end-text' en el documento")
        else:
            first_signature_y = config.SIGNATURE_Y_START
        
        signature_number = existing_signatures_count + 1
        
        if signature_number % 2 == 1:
            x = config.FIRST_SIGNATURE_X
        else:
            x = config.SECOND_SIGNATURE_X
        
        row = (signature_number - 1) // 2
        y = first_signature_y - (row * (config.SIGNATURE_HEIGHT + config.ROW_SPACING))
        
        if not validate_signature_space(x, y):
            raise LayoutError(
                f"FULLPAGE: la firma {signature_number} no entra en la última página "
                f"(y={y:.1f}, mínimo={config.MIN_SIGNATURE_Y}). El documento no deja "
                f"espacio para el bloque de firma: hay que acortar el texto o agregar "
                f"una página."
            )
        
        return (x, y)
        
    except Exception as e:
        if isinstance(e, LayoutError):
            raise
        else:
            raise LayoutError(f"Error calculando posición de firma: {str(e)}")


def validate_signature_space(x: float, y: float) -> bool:
    try:
        if x < config.SIGNATURE_MARGIN_LEFT:
            logger.warning(f"Firma fuera del margen izquierdo: x={x}")
            return False
            
        if x + config.SIGNATURE_WIDTH > config.PAGE_WIDTH - config.SIGNATURE_MARGIN_RIGHT:
            logger.warning(f"Firma fuera del margen derecho: x={x}")
            return False
        
        if y < config.MIN_SIGNATURE_Y:
            logger.warning(f"Firma fuera del límite inferior: y={y}, mínimo={config.MIN_SIGNATURE_Y}")
            return False
        
        if y > config.PAGE_HEIGHT - config.SIGNATURE_MARGIN_TOP:
            logger.warning(f"Firma fuera del margen superior: y={y}")
            return False
        
        logger.info(f"Validación de espacio exitosa para posición ({x}, {y})")
        return True
        
    except Exception as e:
        logger.error(f"Error validando espacio para firma: {e}")
        return False