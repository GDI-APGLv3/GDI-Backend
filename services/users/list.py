"""
Servicio para listar usuarios del sistema.
Aplicando Clean Code: Servicio dedicado con responsabilidad única.
"""

from shared.logging import get_logger
from typing import List, Dict, Any
from database import get_db_cursor
from services.users.queries import list_all_users_query
from shared.exceptions import DatabaseError

logger = get_logger(__name__)


def list_all_active_users(*, schema_name: str) -> List[Dict[str, Any]]:
    """
    Lista todos los usuarios activos del sistema.

    Args:
        schema_name: Nombre del schema (para multi-tenant)

    Returns:
        Lista de usuarios con user_id, full_name y email

    Raises:
        DatabaseError: Si hay error al consultar la base de datos
    """
    logger.info("Fetching all active users from database")

    try:
        query = list_all_users_query()

        with get_db_cursor(schema_name=schema_name) as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
        
        # Convertir a lista de diccionarios con UUIDs como string
        users_list = []
        for user in results:
            users_list.append({
                "user_id": str(user["user_id"]),
                "full_name": user["full_name"],
                "email": user["email"]
            })
        
        logger.info(f"Found {len(users_list)} active users")
        return users_list
        
    except Exception as e:
        logger.error(f"Error fetching active users: {str(e)}", exc_info=True)
        raise DatabaseError(f"Error al obtener lista de usuarios: {str(e)}")
