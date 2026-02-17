"""
Autenticación simple por API Key para MCP Server.
NO usa Auth0/JWT, solo valida X-API-Key contra variable de entorno.
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Variable de entorno obligatoria
MCP_API_KEY = os.getenv("MCP_API_KEY")


def validate_api_key(api_key: Optional[str]) -> bool:
    """
    Valida que la API Key proporcionada coincida con MCP_API_KEY.

    Args:
        api_key: API Key del header X-API-Key

    Returns:
        True si es válida, False si no

    Raises:
        ValueError: Si MCP_API_KEY no está configurada
    """
    if not MCP_API_KEY:
        logger.error("MCP_API_KEY no está configurada en variables de entorno")
        raise ValueError("MCP_API_KEY no configurada en el servidor")

    if not api_key:
        logger.warning("API Key no proporcionada")
        return False

    is_valid = api_key == MCP_API_KEY

    if not is_valid:
        logger.warning("API Key inválida")

    return is_valid
