"""
Servicios compartidos - Funcionalidad común entre módulos.

Este módulo contiene servicios que son utilizados por múltiples dominios:
- external_api: Integraciones con APIs externas
- user_queries: Queries SQL para permisos de usuario
- retry: Retry con tenacity para microservicios

Uso:
    from services.shared.external_api import generate_final_document_pdf, call_signature_stamping_api
    from services.shared.user_queries import get_user_sectors_query
    from services.shared.retry import retry_async_call, RetryConfig
"""

# Importaciones de external_api
from .external_api import (
    generate_final_document_pdf,
    call_signature_stamping_api,
    call_external_signing_api_for_numerator,
    validate_external_document,
    get_external_services_status
)

# Importaciones de user_queries
from .user_queries import get_user_sectors_query

# Importaciones de retry (tenacity)
from .retry import retry_async_call, RetryConfig, get_service_config

__all__ = [
    # External API
    "generate_final_document_pdf",
    "call_signature_stamping_api",
    "call_external_signing_api_for_numerator",
    "validate_external_document",
    "get_external_services_status",
    # User Queries
    "get_user_sectors_query",
    # Retry
    "retry_async_call",
    "RetryConfig",
    "get_service_config",
]