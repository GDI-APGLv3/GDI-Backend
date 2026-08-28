from typing import List
from database import fetch_all
from shared.logging import get_logger
from services.shared.user_queries import get_user_sectors_query

logger = get_logger(__name__)


async def get_user_sector_ids(user_id: str, *, schema_name: str) -> List[str]:
    query = get_user_sectors_query()
    results = await fetch_all(query, user_id, user_id, schema_name=schema_name)
    sector_ids = [row['sector_id'] for row in results if row['sector_id']]

    if not sector_ids:
        logger.warning(f"Usuario {user_id} sin sectores con permiso de visualización")

    return sector_ids
