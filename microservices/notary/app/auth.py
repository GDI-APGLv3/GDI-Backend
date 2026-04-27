"""
Módulo de autenticación para el servicio Notary
"""
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from .config import API_KEY

# Configuración del header de API Key
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def validate_api_key(api_key: str = Security(api_key_header)):
    """
    Valida la API Key enviada en el header X-API-Key

    Args:
        api_key: API Key enviada en el header

    Returns:
        str: API Key válida

    Raises:
        HTTPException: Si la API Key es inválida o faltante
    """
    if api_key is None:
        raise HTTPException(
            status_code=401,
            detail="Missing API key",
            headers={"error_code": "MISSING_API_KEY"}
        )
    if not hmac.compare_digest(api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"error_code": "INVALID_API_KEY"}
        )
    return api_key