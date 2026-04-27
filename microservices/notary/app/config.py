"""
Configuración centralizada para el servicio Notary

Este módulo contiene todas las configuraciones y constantes utilizadas
por el servicio de notarización digital, organizadas por categorías
para facilitar el mantenimiento y la comprensión del sistema.

Categorías de configuración:
- Certificados digitales y seguridad
- Dimensiones de página y formato PDF
- Posicionamiento de firmas y estampado
- Validaciones y límites del sistema
- Configuración de API y autenticación
- Tipografía y presentación visual

Autor: Sistema de Notarización Digital
Versión: 1.0.0
"""
import os
from pathlib import Path

# =============================================================================
# DIMENSIONES DE PÁGINA Y FORMATO PDF
# =============================================================================

# Formato A4 estándar (210 x 297 mm)
# Dimensiones en puntos (1 mm ≈ 2.835 puntos)
PAGE_WIDTH = 595   # A4: 595.28 pts (redondeado)
PAGE_HEIGHT = 842  # A4: 841.89 pts (redondeado)

# Compatibilidad con nombres anteriores
LETTER_WIDTH = PAGE_WIDTH
LETTER_HEIGHT = PAGE_HEIGHT

# =============================================================================
# CONFIGURACIÓN DE ESTAMPADO (PRIMERA PÁGINA)
# =============================================================================

# Posición del estampado en la primera página del documento
STAMP_X = 400      # Coordenada X (desde borde izquierdo)
STAMP_Y = 700      # Coordenada Y (desde borde inferior en ReportLab)
STAMP_WIDTH = 150  # Ancho del área de estampado
STAMP_HEIGHT = 40  # Alto del área de estampado

# =============================================================================
# CONFIGURACIÓN DE LAYOUT DE FIRMAS (ÚLTIMA PÁGINA)
# =============================================================================

# Texto de referencia para posicionamiento automático de firmas
END_TEXT_SEARCH = "end-text"

# Posicionamiento de firmas digitales según requerimientos originales
DEFAULT_FIRST_Y = 400          # Posición Y por defecto si no se encuentra "end-text"
SIGNATURE_OFFSET_BELOW = 15    # Gap real entre bottom de end-text y top de firma (~5mm)

# Layout de 2 columnas según especificación
FIRST_SIGNATURE_X = 50         # Columna izquierda (firmas 1, 3, 5, 7...)
SECOND_SIGNATURE_X = 270       # Columna derecha (firmas 2, 4, 6, 8...)
SIGNATURE_WIDTH = 200          # Ancho de cada firma
SIGNATURE_HEIGHT = 80          # Alto de cada firma
ROW_SPACING = 20               # Espaciado entre filas (no entre firmas individuales)

# Validación de espacio
MIN_SIGNATURE_Y = 100          # Posición Y mínima para firmas (margen inferior)

# Detección de firmas existentes
SIGNATURE_DETECTION_TEXT = "Digitally Signed by"

# Márgenes de seguridad (mantenidos para compatibilidad)
SIGNATURE_MARGIN_LEFT = 20     # Margen izquierdo mínimo
SIGNATURE_MARGIN_RIGHT = 20    # Margen derecho mínimo
SIGNATURE_MARGIN_TOP = 20      # Margen superior mínimo
SIGNATURE_MARGIN_BOTTOM = 20   # Margen inferior mínimo

# Configuraciones heredadas (compatibilidad)
SIGNATURE_X_POSITION = FIRST_SIGNATURE_X
SIGNATURE_Y_START = DEFAULT_FIRST_Y
SIGNATURE_SPACING = SIGNATURE_HEIGHT + ROW_SPACING  # Espaciado total entre filas

# =============================================================================
# VALIDACIONES Y LÍMITES DEL SISTEMA
# =============================================================================

# Límites de archivos y procesamiento
MAX_PDF_SIZE_MB = 10        # Tamaño máximo de archivo PDF en MB
REQUEST_TIMEOUT = 30        # Timeout para requests HTTP en segundos

# Patrones de detección de firmas existentes
SIGNATURE_DETECTION_TEXT = "Digitally Signed by"

# =============================================================================
# CONFIGURACIÓN DE API Y AUTENTICACIÓN
# =============================================================================

# Clave de API para autenticación de requests
API_KEY = os.getenv("API_KEY", "")
if not API_KEY:
    raise RuntimeError(
        "FATAL: API_KEY environment variable is not set or is empty. "
        "Set it with: flyctl secrets set API_KEY=<value> -a <app-name>"
    )

# Configuración de zona horaria
TIMEZONE = "UTC"

# =============================================================================
# TIPOGRAFÍA Y PRESENTACIÓN VISUAL
# =============================================================================

# Fuente principal utilizada en firmas y estampado
FONT_NAME = "Montserrat"

# Tamaños de fuente para diferentes elementos
FONT_SIZE_SIGNATURE = 10    # Tamaño para texto de firmas
FONT_SIZE_STAMP = 11        # Tamaño para texto de estampado
FONT_SIZE_IDENTIFIER = 8    # Tamaño para identificadores del sistema

# =============================================================================
# MENSAJES Y METADATOS DEL SISTEMA
# =============================================================================

# Identificador que aparece en las firmas digitales
SIGNATURE_IDENTIFIER = "Digitally Signed by TEST SERVER"

# Información del servicio
SERVICE_NAME = "Notary"
SERVICE_VERSION = "2.0.0"

# =============================================================================
# VALIDACIÓN DE NOMBRES DE ARCHIVO
# =============================================================================

# Caracteres especiales no permitidos en nombres de archivo
SPECIAL_CHARS = r'/\:*?"<>|'

# =============================================================================
# CONFIGURACIÓN DE AMBIENTE
# =============================================================================

# Ambiente de ejecución: "test" o "prd"
# - test: permite fallback a firma visual si no hay certificado
# - prd: requiere certificado obligatoriamente (error 400 si no existe)
ENVIRONMENT = os.getenv("ENVIRONMENT", "test").lower()

# =============================================================================
# CONFIGURACIÓN DE FIRMA PAdES (FIRMA DIGITAL CRIPTOGRÁFICA)
# =============================================================================

# Directorio donde se almacenan los certificados .p12 por tenant
# Estructura esperada: {CERTS_DIR}/{tenant_id}.p12
CERTS_DIR = Path(os.getenv("CERTS_DIR", Path(__file__).parent.parent / "certs"))

# URL del servidor de timestamp (TSA) para firma PAdES-B-T
# Servidores públicos gratuitos:
# - http://timestamp.digicert.com
# - http://timestamp.sectigo.com
# - http://timestamp.globalsign.com/tsa/r6advanced1
TSA_URL = os.getenv("TSA_URL", "http://timestamp.digicert.com")

# Timeout por intento TSA en segundos
TSA_TIMEOUT = int(os.getenv("TSA_TIMEOUT", "3"))

# Número de reintentos ante fallo TSA (intentos totales = TSA_RETRIES + 1)
# Peor caso: (TSA_RETRIES+1) * TSA_TIMEOUT + ~0.8s backoffs ≈ 9.8s con valores default
TSA_RETRIES = int(os.getenv("TSA_RETRIES", "2"))

# Si no llega certificado via multipart, usar firma visual como fallback
# Default: false (requiere certificado). Setear FALLBACK_TO_VISUAL=true solo para pruebas manuales.
_default_fallback = "false"
FALLBACK_TO_VISUAL = os.getenv("FALLBACK_TO_VISUAL", _default_fallback).lower() in ("true", "1", "yes")

# Archivo JSON con mapeo tenant_id -> password del certificado
PASSWORDS_FILE = CERTS_DIR / "passwords.json"

# Configuración de firma PAdES
PADES_SIGNATURE_FIELD_NAME = "GDI_Signature"  # Nombre del campo de firma en el PDF
PADES_SIGNATURE_REASON = "Documento firmado digitalmente"  # Razón de la firma
PADES_SIGNATURE_LOCATION = "Sistema GDI"  # Ubicación de la firma