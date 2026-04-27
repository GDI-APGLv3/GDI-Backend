"""
Servicio de historial para el módulo RLM.
Registra todos los cambios en record_history.
"""

import json
from typing import Any, Optional
from shared.logging import get_logger
from database import execute_query, execute_update
from services.rlm.queries import (
    insert_history_query,
    get_record_history_query,
    get_record_history_count_query,
)

logger = get_logger(__name__)


def record_action(
    record_id: str,
    action: str,
    user_id: str,
    sector_id: Optional[str] = None,
    field_name: Optional[str] = None,
    before_value: Any = None,
    after_value: Any = None,
    *,
    schema_name: str
) -> None:
    """
    Registra una acción en el historial de un legajo.

    Args:
        record_id: UUID del legajo
        action: Tipo de acción (created, state_changed, field_updated, field_verified, etc.)
        user_id: UUID del usuario
        sector_id: UUID del sector del usuario
        field_name: Nombre del campo afectado (opcional)
        before_value: Valor anterior (serializable a JSON)
        after_value: Valor posterior (serializable a JSON)
        schema_name: Schema del tenant
    """
    try:
        before_json = json.dumps(before_value, default=str) if before_value is not None else None
        after_json = json.dumps(after_value, default=str) if after_value is not None else None

        execute_update(
            insert_history_query(),
            (
                record_id,
                action,
                field_name,
                before_json,
                after_json,
                user_id,
                sector_id,
            ),
            schema_name=schema_name
        )

        logger.debug(f"History recorded: {action} on record {record_id[:8]} by user {user_id[:8]}")

    except Exception as e:
        # No propagar errores de historial para no bloquear operaciones principales
        logger.error(f"Error recording history: {e}")


def get_history(
    record_id: str,
    user_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    Obtiene el historial de cambios de un legajo.

    Args:
        record_id: UUID del legajo
        user_id: UUID del usuario
        schema_name: Schema del tenant
        page: Página actual
        page_size: Items por página

    Returns:
        Dict con historial paginado

    Raises:
        NotFoundError: Si el legajo no existe
        AuthorizationError: Si no tiene permiso can_view
    """
    from services.rlm.permissions import verify_record_view_permission
    verify_record_view_permission(record_id, user_id, schema_name=schema_name)

    # Count
    count_result = execute_query(
        get_record_history_count_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    total = count_result["total"] if count_result else 0

    # List
    offset = (page - 1) * page_size
    results = execute_query(
        get_record_history_query(),
        (record_id, page_size, offset),
        schema_name=schema_name
    )

    entries = []
    for row in (results or []):
        entries.append({
            "id": row["id"],
            "action": row["action"],
            "field_name": row["field_name"],
            "before_value": row["before_value"],
            "after_value": row["after_value"],
            "user_id": row["user_id"],
            "user_name": row["user_name"],
            "sector_name": row["sector_name"],
            "created_at": str(row["created_at"]),
        })

    total_pages = max(1, -(-total // page_size))

    return {
        "entries": entries,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }
