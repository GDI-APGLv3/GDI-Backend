"""
Servicio para contar memos no leidos (badge).
Modulo nuevo sin equivalente en NOTAS.
"""

from shared.logging import get_logger
from database import fetch_one
from .queries import get_unread_memo_count_query

logger = get_logger(__name__)


async def get_unread_memo_count(user_id: str, *, schema_name: str) -> int:
    """
    Obtiene la cantidad de memos no leidos para un usuario.
    Util para mostrar badge/contador en el frontend.

    Cuenta memos donde:
    - El usuario es recipient
    - No esta archivado
    - No ha sido abierto (opened_at IS NULL)
    - El documento esta oficializado (existe en official_documents)

    Returns:
        Cantidad de memos no leidos (int)
    """
    result = await fetch_one(
        get_unread_memo_count_query(), user_id,
        schema_name=schema_name
    )
    count = result['unread_count'] if result else 0

    logger.debug(f"[{schema_name}] User {user_id}: {count} memos no leidos")

    return count
