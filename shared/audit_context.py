"""
Contexto de auditoría para trazabilidad de origen.

Inyecta variables GUC (Grand Unified Configuration) en PostgreSQL
para que los triggers de auditoría sepan quién realizó la acción
y desde qué origen.

Valores de auth_source:
- jwt: Frontend Auth0 (login normal via navegador)
- api_key: REST API MCP (Header X-API-Key)
- mcp_oauth: Claude Code, ChatGPT, Gemini (OAuth 2.0)
- testing: TESTING_MODE (Header X-User-ID en desarrollo)
- system: Operaciones automáticas (CAEX, PV, triggers internos)
"""

from typing import Optional, Literal
from shared.logging import get_logger

logger = get_logger(__name__)

# Tipos válidos de auth_source
AuthSource = Literal["jwt", "api_key", "mcp_oauth", "testing", "system"]


def set_audit_context(
    cursor,
    user_id: Optional[str] = None,
    auth_source: Optional[AuthSource] = None
) -> None:
    """
    Configura el contexto de auditoría para la transacción actual.

    Los triggers de auditoría (fn_log_change) leen estas variables GUC
    para registrar quién realizó la acción y desde dónde.

    Usa set_config con is_local=true para que el contexto se resetee
    automáticamente al finalizar la transacción (compatible con PgBouncer).

    Args:
        cursor: Cursor de psycopg2 con transacción activa
        user_id: UUID del usuario que realiza la acción (opcional)
        auth_source: Origen de la autenticación (opcional)

    Example:
        with get_db_cursor(commit=True, schema_name=schema_name) as cursor:
            set_audit_context(cursor, user_id=creator_id, auth_source="jwt")
            cursor.execute("INSERT INTO documents ...")
    """
    if user_id:
        cursor.execute(
            "SELECT set_config('app.user_id', %s, true)",
            (str(user_id),)
        )
        logger.debug(f"[AUDIT] app.user_id={user_id}")

    if auth_source:
        cursor.execute(
            "SELECT set_config('app.auth_source', %s, true)",
            (auth_source,)
        )
        logger.debug(f"[AUDIT] app.auth_source={auth_source}")


def clear_audit_context(cursor) -> None:
    """
    Limpia el contexto de auditoría explícitamente.

    Normalmente no es necesario porque set_config con is_local=true
    se resetea al finalizar la transacción, pero puede usarse
    para limpiar en medio de una transacción larga.

    Args:
        cursor: Cursor de psycopg2
    """
    cursor.execute("SELECT set_config('app.user_id', '', true)")
    cursor.execute("SELECT set_config('app.auth_source', '', true)")
    logger.debug("[AUDIT] Contexto limpiado")
