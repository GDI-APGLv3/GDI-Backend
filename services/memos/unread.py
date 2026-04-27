"""
Servicio para contar memos no leidos (badge).
Modulo nuevo sin equivalente en NOTAS.
"""

from shared.logging import get_logger
from database import get_db_connection
from .queries import get_unread_memo_count_query

logger = get_logger(__name__)


def get_unread_memo_count(user_id: str, *, schema_name: str) -> int:
    """
    Obtiene la cantidad de memos no leidos para un usuario.
    Util para mostrar badge/contador en el frontend.

    Cuenta memos donde:
    - El usuario es recipient
    - No esta archivado
    - No ha sido abierto (opened_at IS NULL)
    - El documento esta oficializado (existe en official_documents)

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Returns:
        Cantidad de memos no leidos (int)
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_unread_memo_count_query(), (user_id,))
            result = cursor.fetchone()
            count = result['unread_count'] if result else 0

            logger.debug(f"[{schema_name}] User {user_id}: {count} memos no leidos")

            return count
