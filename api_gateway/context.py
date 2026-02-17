"""
Context para operaciones MCP.
Maneja el mapping de municipality_id -> schema_name.
"""
import logging
from dataclasses import dataclass
from typing import Optional
from database import execute_query

logger = logging.getLogger(__name__)


@dataclass
class MCPContext:
    """
    Contexto de ejecución para herramientas MCP.

    Attributes:
        api_key: API Key validada
        municipality_id: ID de la municipalidad (requerido)
        schema_name: Schema derivado del municipality_id
        auth_source: Origen de la autenticación para trazabilidad.
                     Valores: "api_key" (REST API), "mcp_oauth" (Claude/ChatGPT/Gemini)
        user_id: ID del usuario que ejecuta la operación (requerido para REST API)
    """
    api_key: str
    municipality_id: str
    schema_name: str
    auth_source: str = "api_key"  # Default para REST API
    user_id: Optional[str] = None  # Requerido para REST API con X-User-ID


def get_schema_from_municipality(municipality_id: str) -> Optional[str]:
    """
    Obtiene el schema_name correspondiente a un municipality_id.

    Args:
        municipality_id: ID de la municipalidad

    Returns:
        Schema name (ej: "100_test") o None si no existe

    Raises:
        Exception: Si hay error de BD
    """
    try:
        # Query contra tabla public.municipalities
        query = """
            SELECT schema_name
            FROM public.municipalities
            WHERE id = %s AND is_active = true
        """

        result = execute_query(
            query,
            (municipality_id,),
            fetch_one=True,
            schema_name="public"  # Tabla municipalities está en schema public
        )

        if not result:
            logger.warning(f"Municipality {municipality_id} no encontrada o inactiva")
            return None

        schema_name = result.get("schema_name")
        logger.info(f"Municipality {municipality_id} -> schema {schema_name}")

        return schema_name

    except Exception as e:
        logger.error(f"Error obteniendo schema para municipality {municipality_id}: {e}")
        raise


def create_context(
    api_key: str,
    municipality_id: str,
    auth_source: str = "api_key",
    user_id: Optional[str] = None
) -> MCPContext:
    """
    Crea contexto MCP validado.

    Args:
        api_key: API Key validada
        municipality_id: ID de municipalidad
        auth_source: Origen de la autenticación (api_key, mcp_oauth)
        user_id: ID del usuario que ejecuta la operación (requerido para REST API)

    Returns:
        MCPContext con schema_name resuelto

    Raises:
        ValueError: Si municipality_id no es válido
    """
    schema_name = get_schema_from_municipality(municipality_id)

    if not schema_name:
        raise ValueError(f"Municipality ID '{municipality_id}' no válido o inactivo")

    return MCPContext(
        api_key=api_key,
        municipality_id=municipality_id,
        schema_name=schema_name,
        auth_source=auth_source,
        user_id=user_id
    )
