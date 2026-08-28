from shared.logging import get_logger
from dataclasses import dataclass
from typing import Optional
from database import fetch_one

logger = get_logger(__name__)


@dataclass
class MCPContext:
    api_key: str
    municipality_id: str
    schema_name: str
    auth_source: str = "api_key"
    user_id: Optional[str] = None


async def get_schema_from_municipality(municipality_id: str) -> Optional[str]:
    try:
        query = """
            SELECT schema_name
            FROM public.municipalities
            WHERE id = $1 AND is_active = true
        """

        result = await fetch_one(
            query,
            municipality_id,
            schema_name="public"
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


async def create_context(
    api_key: str,
    municipality_id: str,
    auth_source: str = "api_key",
    user_id: Optional[str] = None
) -> MCPContext:
    schema_name = await get_schema_from_municipality(municipality_id)

    if not schema_name:
        raise ValueError(f"Municipality ID '{municipality_id}' no válido o inactivo")

    return MCPContext(
        api_key=api_key,
        municipality_id=municipality_id,
        schema_name=schema_name,
        auth_source=auth_source,
        user_id=user_id
    )
