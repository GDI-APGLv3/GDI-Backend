"""
Módulo de layout automático para posicionamiento de firmas digitales

Este módulo maneja el posicionamiento inteligente de firmas digitales en documentos PDF,
utilizando el texto "end-text" como punto de referencia para el cálculo de posiciones.

Características principales:
- Detección automática del texto "end-text" en documentos
- Cálculo de posiciones para múltiples firmas
- Conversión entre sistemas de coordenadas (PyMuPDF ↔ ReportLab)
- Validación de espacio disponible en la página

Sistemas de coordenadas:
- PyMuPDF: Origen (0,0) en esquina superior izquierda, Y crece hacia abajo
- ReportLab: Origen (0,0) en esquina inferior izquierda, Y crece hacia arriba

Autor: Sistema de Notarización Digital
Versión: 1.0.0
"""
from typing import Dict, List, Tuple, Optional
import io
import logging
import fitz  # PyMuPDF
from . import config

# Configurar logger para el módulo de layout
# Este logger se utiliza para registrar información sobre el posicionamiento de firmas,
# validaciones de espacio y errores durante el procesamiento de documentos
logger = logging.getLogger(__name__)


class LayoutError(Exception):
    """
    Excepción personalizada para errores de layout y posicionamiento
    
    Se utiliza para manejar errores específicos del sistema de layout,
    como falta de espacio en la página o problemas de detección de texto.
    """
    pass


def find_end_text_positions(pdf_content: bytes) -> List[Tuple[float, float]]:
    """
    Busca todas las posiciones del texto "end-text" en la última página del PDF
    
    Esta función escanea la última página del documento buscando instancias
    del texto "end-text" que sirve como marcador para el posicionamiento
    de firmas digitales.
    
    Args:
        pdf_content (bytes): Contenido binario del archivo PDF
        
    Returns:
        List[Tuple[float, float]]: Lista de posiciones (x, y) donde se encontró
                                  el texto "end-text" en coordenadas PyMuPDF
                                  
    Note:
        - Solo busca en la última página del documento
        - Utiliza coordenadas PyMuPDF (origen superior izquierdo)
        - Retorna lista vacía si no encuentra el texto
        
    Example:
        >>> positions = find_end_text_positions(pdf_bytes)
        >>> print(positions)  # [(100.5, 200.3), (150.2, 250.8)]
    """
    try:
        doc = fitz.open(stream=pdf_content, filetype="pdf")
        if len(doc) == 0:
            return []
        
        # Buscar en la última página del documento
        last_page = doc[-1]
        text_instances = last_page.search_for("end-text")
        
        positions = []
        for rect in text_instances:
            # Usar la coordenada Y del texto encontrado (esquina superior del texto)
            positions.append((rect.x0, rect.y0))
        
        doc.close()
        return positions
        
    except Exception:
        # Si hay error en la lectura del PDF, retornar lista vacía
        return []


def count_existing_signatures(pdf_content: bytes) -> int:
    """
    Cuenta el número de firmas digitales existentes en la última página del PDF
    
    Esta función analiza la última página del documento para determinar
    cuántas firmas digitales ya están presentes, lo que permite calcular
    la posición correcta para nuevas firmas y evitar superposiciones.
    
    El algoritmo de detección:
    1. Busca primero el texto configurado en SIGNATURE_DETECTION_TEXT
    2. Si no encuentra coincidencias, utiliza patrones de respaldo comunes
    3. Retorna el número total de firmas detectadas
    
    Args:
        pdf_content (bytes): Contenido binario del archivo PDF
        
    Returns:
        int: Número de firmas digitales encontradas en la última página
        
    Note:
        La detección se basa en la búsqueda de patrones específicos de texto
        que indican la presencia de una firma digital (nombres, sellos, etc.)
        Esta función es crítica para el posicionamiento automático en 2 columnas.
    """
    try:
        # 1. Contar firmas por texto visual (metodo original)
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

        # 2. Contar firmas PAdES embebidas
        from .pades_signer import count_pades_signatures
        pades_count = count_pades_signatures(pdf_content)

        # Usar el mayor: cubre tanto firmas visuales como PAdES
        total = max(visual_count, pades_count)
        logger.info(f"Firmas detectadas: visual={visual_count}, pades={pades_count}, total={total}")
        return total

    except Exception:
        # En caso de error, asumir que no hay firmas existentes
        return 0


def get_first_signature_y_position(pdf_content: bytes) -> Optional[float]:
    """
    Calcula la posición Y correcta para la primera firma basada en el texto "end-text"
    
    Esta función localiza el texto "end-text" en el documento y calcula la posición
    Y apropiada para colocar la primera firma digital, considerando el offset
    configurado y convirtiendo entre sistemas de coordenadas.
    
    Args:
        pdf_content (bytes): Contenido binario del archivo PDF
        
    Returns:
        Optional[float]: Posición Y en coordenadas ReportLab para la primera firma,
                        o None si no se encuentra el texto "end-text"
                        
    Note:
        - Busca el texto "end-text" en coordenadas PyMuPDF
        - Aplica el offset SIGNATURE_OFFSET_BELOW
        - Convierte el resultado a coordenadas ReportLab
        
    Cálculo matemático:
        1. Encuentra "end-text" en PyMuPDF: text_y1 (bottom del texto)
        2. Calcula posición: signature_y = text_y1 + OFFSET + SIGNATURE_HEIGHT
        3. Convierte a ReportLab: reportlab_y = page_height - signature_y
        4. Gap real visible = OFFSET (entre bottom de end-text y top de firma)
    """
    try:
        doc = fitz.open(stream=pdf_content, filetype="pdf")
        if len(doc) == 0:
            return None
        
        # Buscar "end-text" en la última página
        last_page = doc[-1]
        text_instances = last_page.search_for("end-text")
        
        if not text_instances:
            logger.info("No se encontró el texto 'end-text' en el documento")
            doc.close()
            return None
        
        # Usar la primera instancia encontrada
        text_rect = text_instances[0]
        text_pymupdf_y_bottom = text_rect.y1  # Bottom del end-text en PyMuPDF

        logger.info(f"Texto 'end-text' encontrado en PyMuPDF: x={text_rect.x0:.2f}, y0={text_rect.y0:.2f}, y1(bottom)={text_rect.y1:.2f}")
        
        # Obtener dimensiones de la página
        page_height = last_page.rect.height
        
        # Calcular posición de la primera firma
        # OFFSET es el gap REAL visible entre bottom de end-text y top de firma
        # Sumamos SIGNATURE_HEIGHT porque reportlab_y es el bottom del rect de firma
        # (en ReportLab el rect de firma crece 80pts hacia arriba desde reportlab_y)
        signature_pymupdf_y = text_pymupdf_y_bottom + config.SIGNATURE_OFFSET_BELOW + config.SIGNATURE_HEIGHT

        # Convertir a coordenadas ReportLab (Y crece hacia arriba)
        reportlab_y = page_height - signature_pymupdf_y

        logger.info(f"Primera firma calculada - PyMuPDF: y={signature_pymupdf_y:.2f}, ReportLab: y={reportlab_y:.2f}")
        logger.info(f"Gap real entre end-text y firma: {config.SIGNATURE_OFFSET_BELOW}pts (~{config.SIGNATURE_OFFSET_BELOW / 2.835:.0f}mm)")
        
        doc.close()
        return reportlab_y
        
    except Exception:
        return None


def calculate_signature_position(existing_signatures_count: int = 0, pdf_content: bytes = None) -> Tuple[float, float]:
    """
    Calcula la posición (x, y) para una nueva firma digital según el layout de 2 columnas
    
    Layout especificado:
    - Columna izquierda (x=50): firmas 1, 3, 5, 7...
    - Columna derecha (x=270): firmas 2, 4, 6, 8...
    - Espaciado entre filas: ROW_SPACING
    
    Args:
        existing_signatures_count (int): Número de firmas ya presentes en el documento
        pdf_content (bytes, optional): Contenido del PDF para análisis de posición
        
    Returns:
        Tuple[float, float]: Coordenadas (x, y) en sistema ReportLab donde colocar la firma
        
    Raises:
        LayoutError: Si no hay espacio suficiente en la página o si no se encuentra "end-text"
        
    Note:
        Algoritmo de posicionamiento:
        1. Determina la columna según el número de firma (impar=izquierda, par=derecha)
        2. Calcula la fila basándose en el número de firmas existentes
        3. Valida que haya espacio suficiente en la página
        
    Sistemas de coordenadas:
        - Entrada: Análisis en PyMuPDF (Y crece hacia abajo)
        - Salida: ReportLab (Y crece hacia arriba)
    """
    try:
        # Calcular posición Y de la primera firma
        if pdf_content:
            first_signature_y = get_first_signature_y_position(pdf_content)
            if first_signature_y is None:
                raise LayoutError("No se encontró el texto 'end-text' en el documento")
        else:
            # Fallback: usar posición por defecto
            first_signature_y = config.SIGNATURE_Y_START
        
        # Determinar posición de la nueva firma (siguiente número)
        signature_number = existing_signatures_count + 1
        
        # Calcular posición X según el layout de 2 columnas
        if signature_number % 2 == 1:  # Firmas impares: columna izquierda
            x = config.FIRST_SIGNATURE_X
        else:  # Firmas pares: columna derecha
            x = config.SECOND_SIGNATURE_X
        
        # Calcular posición Y según la fila
        row = (signature_number - 1) // 2  # Fila 0, 1, 2, 3...
        y = first_signature_y - (row * (config.SIGNATURE_HEIGHT + config.ROW_SPACING))
        
        # Validar que la posición calculada esté dentro de los límites de la página
        if not validate_signature_space(x, y):
            raise LayoutError("FULLPAGE")
        
        return (x, y)
        
    except Exception as e:
        if isinstance(e, LayoutError):
            raise
        else:
            raise LayoutError(f"Error calculando posición de firma: {str(e)}")


def validate_signature_space(x: float, y: float) -> bool:
    """
    Valida que haya espacio suficiente para colocar una firma en las coordenadas especificadas.
    
    Verifica que la firma no se salga de los márgenes de la página y que esté
    por encima del límite mínimo Y especificado en la configuración.
    
    Args:
        x (float): Coordenada X de la firma
        y (float): Coordenada Y de la firma
        
    Returns:
        bool: True si hay espacio suficiente, False en caso contrario
    """
    try:
        # Verificar límites horizontales
        if x < config.SIGNATURE_MARGIN_LEFT:
            logger.warning(f"Firma fuera del margen izquierdo: x={x}")
            return False
            
        if x + config.SIGNATURE_WIDTH > config.PAGE_WIDTH - config.SIGNATURE_MARGIN_RIGHT:
            logger.warning(f"Firma fuera del margen derecho: x={x}")
            return False
        
        # Verificar límite vertical inferior (más importante)
        # y es el bottom del rect de firma en ReportLab, comparar directamente
        if y < config.MIN_SIGNATURE_Y:
            logger.warning(f"Firma fuera del límite inferior: y={y}, mínimo={config.MIN_SIGNATURE_Y}")
            return False
        
        # Verificar límite vertical superior
        if y > config.PAGE_HEIGHT - config.SIGNATURE_MARGIN_TOP:
            logger.warning(f"Firma fuera del margen superior: y={y}")
            return False
        
        logger.info(f"Validación de espacio exitosa para posición ({x}, {y})")
        return True
        
    except Exception as e:
        logger.error(f"Error validando espacio para firma: {e}")
        return False