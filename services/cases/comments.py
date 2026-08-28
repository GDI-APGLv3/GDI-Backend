
from typing import Optional, Dict, Any

from database import fetch_all, execute
from shared.exceptions import BusinessLogicError, NotFoundError, ValidationError
from shared.logging import get_logger
from services.cases.history import create_movement
from services.case_queries import (
    get_user_with_sector_query,
    get_admin_sector_query,
    get_movement_for_closing_query,
    close_movement_query,
)
from config.constants import (
    MOVEMENT_TYPE_COMMENT,
    MOVEMENT_TYPE_TASK,
    CASE_NOT_FOUND_ERROR,
)

logger = get_logger(__name__)


async def create_case_comment(
    case_id: str,
    user_id: str,
    movement_type: str,
    text: str,
    mentioned_user_id: Optional[str] = None,
    *,
    schema_name: str,
) -> Dict[str, Any]:
    if movement_type not in (MOVEMENT_TYPE_COMMENT, MOVEMENT_TYPE_TASK):
        raise ValidationError("type debe ser 'comment' o 'task'")

    user_rows = await fetch_all(get_user_with_sector_query(), user_id, schema_name=schema_name)
    if not user_rows or not user_rows[0].get('sector_id'):
        raise BusinessLogicError("El usuario no tiene un sector asignado")
    creator_sector_id = str(user_rows[0]['sector_id'])

    admin_rows = await fetch_all(get_admin_sector_query(), case_id, schema_name=schema_name)
    if not admin_rows or not admin_rows[0].get('admin_sector_id'):
        raise BusinessLogicError("No se pudo determinar el sector administrador del expediente")
    admin_sector_id = str(admin_rows[0]['admin_sector_id'])

    if mentioned_user_id is not None:
        mentioned_user_row = await fetch_all(
            """
            SELECT id FROM users
            WHERE id = $1
              AND estado = 1
            """,
            mentioned_user_id,
            schema_name=schema_name,
        )
        if not mentioned_user_row:
            raise ValidationError(
                "El usuario mencionado no existe o no pertenece a este municipio."
            )

    is_active = (movement_type == MOVEMENT_TYPE_TASK)

    movement_id = await create_movement(
        case_id=case_id,
        movement_type=movement_type,
        user_id=user_id,
        creator_sector_id=creator_sector_id,
        admin_sector_id=admin_sector_id,
        reason=text,
        assigned_user_id=mentioned_user_id or None,
        is_active=is_active,
        schema_name=schema_name,
    )

    created_rows = await fetch_all(
        "SELECT created_at FROM case_movements WHERE id = $1",
        movement_id, schema_name=schema_name
    )
    created_at = created_rows[0]['created_at'].isoformat() if created_rows and created_rows[0].get('created_at') else None

    logger.info(f"Comentario/tarea creado: {movement_id} (type={movement_type}, case={case_id})")
    return {
        "movement_id": movement_id,
        "type": movement_type,
        "is_active": is_active,
        "created_at": created_at,
    }


async def complete_case_task(
    case_id: str,
    movement_id: str,
    user_id: str,
    closing_reason: str,
    *,
    schema_name: str,
) -> Dict[str, Any]:
    rows = await fetch_all(get_movement_for_closing_query(), movement_id, case_id, schema_name=schema_name)
    if not rows:
        raise NotFoundError(CASE_NOT_FOUND_ERROR)

    movement = rows[0]
    if movement['type'] != MOVEMENT_TYPE_TASK:
        raise ValidationError("El movimiento no es una tarea")
    if not movement['is_active']:
        raise ValidationError("La tarea ya fue completada")

    await execute(
        close_movement_query(),
        closing_reason, user_id, movement_id,
        schema_name=schema_name, user_id=user_id, auth_source="jwt",
    )

    closed_rows = await fetch_all(
        "SELECT closed_at FROM case_movements WHERE id = $1",
        movement_id, schema_name=schema_name
    )
    closed_at = closed_rows[0]['closed_at'].isoformat() if closed_rows and closed_rows[0].get('closed_at') else None

    logger.info(f"Tarea completada: {movement_id} (case={case_id}, by={user_id})")
    return {
        "movement_id": movement_id,
        "case_id": case_id,
        "closed_at": closed_at,
        "closing_reason": closing_reason,
    }
