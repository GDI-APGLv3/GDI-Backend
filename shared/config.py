
from config.constants import MAX_PAGE_SIZE as _MAX_PAGE_SIZE


class PaginationConfig:

    DEFAULT_PAGE_SIZE = 20
    """Número por defecto de elementos por página"""

    MIN_PAGE_SIZE = 1
    """Número mínimo de elementos por página"""

    MAX_PAGE_SIZE = _MAX_PAGE_SIZE
    """Número máximo de elementos por página (GDI-028: fuente única en config/constants.py)"""


class APIConfig:
    
    REQUEST_TIMEOUT = 120
    """Timeout general para requests en segundos"""
    
    MAX_UPLOAD_SIZE = 50 * 1024 * 1024
    """Tamaño máximo de archivos subidos en bytes"""
    
    DEFAULT_ENCODING = "utf-8"
    """Encoding por defecto para archivos de texto"""


