"""Utilidades para sectores de usuario."""
from typing import List
from database import execute_query
from shared.logging import get_logger
from services.shared.user_queries import get_user_sectors_query

logger = get_logger(__name__)


def get_user_sector_ids(user_id: str, *, schema_name: str) -> List[str]:
    """Obtiene sectores donde usuario puede VER (can_view=true).

    Args:
        user_id: ID del usuario
        schema_name: Schema del tenant (keyword-only)

    Returns:
        Lista de sector_ids donde el usuario tiene permiso de visualización
    """
    query = get_user_sectors_query()
    results = execute_query(query, (user_id, user_id), schema_name=schema_name)
    sector_ids = [row['sector_id'] for row in results if row['sector_id']]

    if not sector_ids:
        logger.warning(f"Usuario {user_id} sin sectores con permiso de visualización")

    return sector_ids
