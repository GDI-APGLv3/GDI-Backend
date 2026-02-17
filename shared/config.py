"""
Configuraciones centralizadas del sistema GDI Backend.

Este módulo contiene todas las configuraciones y constantes del sistema
organizadas por dominios para facilitar el mantenimiento y la escalabilidad.
"""

class PaginationConfig:
    """Configuraciones relacionadas con paginación de endpoints."""
    
    DEFAULT_PAGE_SIZE = 20
    """Número por defecto de elementos por página"""
    
    MIN_PAGE_SIZE = 1
    """Número mínimo de elementos por página"""
    
    MAX_PAGE_SIZE = 100
    """Número máximo de elementos por página"""


class DatabaseConfig:
    """Configuraciones relacionadas con la base de datos."""
    
    CONNECTION_TIMEOUT = 30
    """Timeout para conexiones de BD en segundos"""
    
    QUERY_TIMEOUT = 60
    """Timeout para queries de BD en segundos"""
    
    MAX_CONNECTIONS = 20
    """Número máximo de conexiones simultáneas"""


class APIConfig:
    """Configuraciones generales de la API."""
    
    REQUEST_TIMEOUT = 120
    """Timeout general para requests en segundos"""
    
    MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
    """Tamaño máximo de archivos subidos en bytes"""
    
    DEFAULT_ENCODING = "utf-8"
    """Encoding por defecto para archivos de texto"""


class DocumentConfig:
    """Configuraciones específicas para documentos."""
    
    MAX_DOCUMENT_TITLE_LENGTH = 200
    """Longitud máxima del título de documento"""
    
    MAX_DOCUMENT_CONTENT_LENGTH = 50000
    """Longitud máxima del contenido de documento"""
    
    SUPPORTED_FORMATS = ["pdf", "docx", "txt"]
    """Formatos de archivo soportados"""


class ValidationConfig:
    """Configuraciones para validaciones del sistema."""
    
    MIN_PASSWORD_LENGTH = 8
    """Longitud mínima de contraseñas"""
    
    UUID_VERSION = 4
    """Versión de UUID utilizada en el sistema"""
    
    MAX_SEARCH_TERM_LENGTH = 100
    """Longitud máxima de términos de búsqueda"""


class ExternalAPIConfig:
    """Configuraciones para APIs externas."""
    
    # Legal Orchestrator API
    LEGAL_ORCHESTRATOR_BASE_URL = "http://localhost:8006"
    """URL base de la API Legal Orchestrator"""
    
    LEGAL_ORCHESTRATOR_TIMEOUT = 60
    """Timeout para requests a Legal Orchestrator en segundos"""
    
    # Variables de entorno requeridas
    REQUIRED_ENV_VARS = [
        "LEGAL_ORCHESTRATOR_API_KEY"  # X-API-Key para autenticación
    ]
    """Variables de entorno requeridas para APIs externas"""


def get_external_api_config():
    """
    Retorna una instancia de configuración para APIs externas.
    
    Returns:
        ExternalAPIConfig: Configuración con base_url y timeout
    """
    config = ExternalAPIConfig()
    config.base_url = ExternalAPIConfig.LEGAL_ORCHESTRATOR_BASE_URL
    config.timeout = ExternalAPIConfig.LEGAL_ORCHESTRATOR_TIMEOUT
    return config