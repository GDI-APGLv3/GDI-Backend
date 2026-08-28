
from typing import Optional, Literal
from shared.logging import get_logger

logger = get_logger(__name__)

AuthSource = Literal["jwt", "api_key", "mcp_oauth", "testing", "system"]


async def set_audit_context(
    conn,
    user_id: Optional[str] = None,
    auth_source: Optional[AuthSource] = None
) -> None:
    if user_id:
        await conn.execute(
            "SELECT set_config('app.user_id', $1, true)",
            str(user_id)
        )
        logger.debug(f"[AUDIT] app.user_id={user_id}")

    if auth_source:
        await conn.execute(
            "SELECT set_config('app.auth_source', $1, true)",
            auth_source
        )
        logger.debug(f"[AUDIT] app.auth_source={auth_source}")


async def clear_audit_context(conn) -> None:
    await conn.execute("SELECT set_config('app.user_id', '', true)")
    await conn.execute("SELECT set_config('app.auth_source', '', true)")
    logger.debug("[AUDIT] Contexto limpiado")
