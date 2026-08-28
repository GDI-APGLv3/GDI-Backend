
from shared.logging import get_logger
from typing import List, Dict, Any
from database import fetch_all
from services.users.queries import list_all_users_query
from shared.exceptions import DatabaseError

logger = get_logger(__name__)


async def list_all_active_users(*, schema_name: str) -> List[Dict[str, Any]]:
    logger.info("Fetching all active users from database")

    try:
        query = list_all_users_query()
        results = await fetch_all(query, schema_name=schema_name)

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
