"""
Autenticación REST con API Key por Schema.

Valida API Key contra tabla public.api_keys,
obtiene municipality_id y schema_name automáticamente.
"""
import os
import sys
import logging
from typing import Tuple, Optional
from datetime import datetime

# Agregar path del backend para imports
backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_root)

from api_gateway.context import MCPContext

logger = logging.getLogger(__name__)


def validate_rest_api_key(api_key: str, user_id: str = None) -> MCPContext:
    """
    Valida API Key y retorna contexto MCP con user_id.

    La API Key determina automáticamente la municipalidad y schema.
    El user_id es REQUERIDO y debe estar autorizado para esta API Key.

    Args:
        api_key: Header X-API-Key
        user_id: Header X-User-ID (REQUERIDO)

    Returns:
        MCPContext con api_key, municipality_id, schema_name y user_id

    Raises:
        ValueError: Si API Key inválida, inactiva, expirada, o user_id no autorizado
    """
    from database import execute_query

    if not api_key:
        raise ValueError("X-API-Key header requerido")

    if not user_id:
        raise ValueError("X-User-ID header requerido para REST API")

    # Buscar API Key en tabla public.api_keys
    try:
        result = execute_query(
            """
            SELECT
                ak.id,
                ak.api_key,
                ak.municipality_id,
                ak.name as key_name,
                ak.expires_at,
                ak.rate_limit_per_minute,
                ak.is_active as key_active,
                m.schema_name,
                m.name as municipality_name,
                m.is_active as muni_active
            FROM public.api_keys ak
            JOIN public.municipalities m ON ak.municipality_id = m.id
            WHERE ak.api_key = %s
            """,
            (api_key,),
            fetch_one=True,
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[REST Auth] Error consultando API Key: {e}")
        raise ValueError("Error validando API Key")

    if not result:
        logger.warning(f"[REST Auth] API Key no encontrada: {api_key[:20]}...")
        raise ValueError("API Key inválida")

    # Verificar que la key está activa
    if not result.get("key_active"):
        logger.warning(f"[REST Auth] API Key inactiva: {result['key_name']}")
        raise ValueError("API Key inactiva")

    # Verificar que la municipalidad está activa
    if not result.get("muni_active"):
        logger.warning(f"[REST Auth] Municipalidad inactiva: {result['municipality_name']}")
        raise ValueError("Municipalidad inactiva")

    # Verificar expiración
    expires_at = result.get("expires_at")
    if expires_at and expires_at < datetime.now(expires_at.tzinfo):
        logger.warning(f"[REST Auth] API Key expirada: {result['key_name']}")
        raise ValueError("API Key expirada")

    # Verificar que user_id está autorizado para esta API Key
    api_key_id = result["id"]
    schema_name = result["schema_name"]

    try:
        user_allowed = execute_query(
            """
            SELECT user_id FROM public.api_key_users
            WHERE api_key_id = %s AND user_id = %s AND schema_name = %s
            """,
            (api_key_id, user_id, schema_name),
            fetch_one=True,
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[REST Auth] Error verificando usuario autorizado: {e}")
        raise ValueError("Error validando usuario")

    if not user_allowed:
        logger.warning(f"[REST Auth] Usuario {user_id} no autorizado para API Key {result['key_name']}")
        raise ValueError(f"Usuario no autorizado para esta API Key")

    # Actualizar last_used_at (async, no bloquea si falla)
    _update_last_used(api_key_id)

    ctx = MCPContext(
        api_key=api_key,
        municipality_id=str(result["municipality_id"]),
        schema_name=schema_name,
        auth_source="api_key",  # Trazabilidad: origen REST API
        user_id=user_id  # Usuario validado
    )

    logger.info(f"[REST Auth] API Key válida: {result['key_name']}, schema: {schema_name}, user: {user_id}")
    return ctx


def _update_last_used(api_key_id: str) -> None:
    """
    Actualiza el timestamp de último uso de la API Key.
    No falla si hay error (operación no crítica).
    """
    from database import execute_query

    try:
        execute_query(
            "UPDATE public.api_keys SET last_used_at = NOW() WHERE id = %s",
            (api_key_id,),
            schema_name="public"
        )
    except Exception as e:
        # No fallar por esto, solo loguear
        logger.debug(f"[REST Auth] Error actualizando last_used_at: {e}")


def get_api_key_info(api_key: str) -> Optional[dict]:
    """
    Obtiene información completa de una API Key (para debugging/admin).

    Args:
        api_key: La API Key a consultar

    Returns:
        Dict con toda la información de la key, o None si no existe
    """
    from database import execute_query

    try:
        result = execute_query(
            """
            SELECT
                ak.id,
                ak.api_key,
                ak.municipality_id,
                ak.name,
                ak.description,
                ak.is_active,
                ak.created_at,
                ak.expires_at,
                ak.last_used_at,
                ak.rate_limit_per_minute,
                ak.created_by,
                m.schema_name,
                m.name as municipality_name
            FROM public.api_keys ak
            JOIN public.municipalities m ON ak.municipality_id = m.id
            WHERE ak.api_key = %s
            """,
            (api_key,),
            fetch_one=True,
            schema_name="public"
        )

        if result:
            return {
                "id": str(result["id"]),
                "name": result["name"],
                "description": result["description"],
                "is_active": result["is_active"],
                "created_at": str(result["created_at"]) if result["created_at"] else None,
                "expires_at": str(result["expires_at"]) if result["expires_at"] else None,
                "last_used_at": str(result["last_used_at"]) if result["last_used_at"] else None,
                "rate_limit_per_minute": result["rate_limit_per_minute"],
                "created_by": result["created_by"],
                "municipality": {
                    "id": str(result["municipality_id"]),
                    "name": result["municipality_name"],
                    "schema_name": result["schema_name"]
                }
            }
        return None

    except Exception as e:
        logger.error(f"[REST Auth] Error obteniendo info de API Key: {e}")
        return None
