"""
Servicio de responsables de expediente.
Gestiona la asignación y remoción de responsables (ADMIN y ADDITIONAL) en expedientes.
"""

import uuid
from typing import Optional
from fastapi import HTTPException
import asyncpg

from database import fetch_all, execute
from shared.logging import get_logger

logger = get_logger(__name__)

MOVEMENT_TYPE_RESPONSIBLE_ADD = "responsible_add"
MOVEMENT_TYPE_RESPONSIBLE_REMOVE = "responsible_remove"


async def get_case_responsibles(case_id: str, *, schema_name: str) -> dict:
    """
    Retorna los responsables activos del expediente.
    """
    query = """
        SELECT
            cr.id,
            cr.case_id,
            cr.user_id,
            cr.sector_id,
            cr.type,
            cr.added_by,
            cr.added_at,
            u.full_name,
            u.email,
            d.acronym || '#' || s.acronym AS sector_acronym,
            d.name AS department_name,
            d.acronym AS department_acronym
        FROM case_responsibles cr
        JOIN users u ON cr.user_id = u.id
        JOIN sectors s ON cr.sector_id = s.id
        JOIN departments d ON s.department_id = d.id
        WHERE cr.case_id = $1
          AND cr.is_active = true
        ORDER BY cr.type DESC, cr.added_at ASC
    """
    rows = await fetch_all(query, case_id, schema_name=schema_name)

    admin = None
    additional = []

    for row in rows:
        entry = {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "sector_id": str(row["sector_id"]),
            "type": row["type"],
            "full_name": row["full_name"],
            "email": row["email"],
            "sector_acronym": row["sector_acronym"],
            "department_name": row["department_name"],
            "department_acronym": row["department_acronym"],
            "added_at": row["added_at"].isoformat() if row["added_at"] else None,
        }
        if row["type"] == "ADMIN":
            admin = entry
        else:
            additional.append(entry)

    return {"admin": admin, "additional": additional}


async def add_responsible(
    case_id: str,
    user_id: str,
    responsible_type: str,
    sector_id: str,
    added_by: str,
    movement_reason: str,
    *,
    schema_name: str,
) -> dict:
    """
    Agrega un responsable al expediente.
    """
    # Validar que sector_id y user_id existan en el tenant
    validation_query = """
        SELECT u.id AS uid, s.id AS sid
        FROM users u, sectors s
        WHERE u.id = $1 AND s.id = $2 AND u.estado = 1 AND s.is_active = true
    """
    valid = await fetch_all(validation_query, user_id, sector_id, schema_name=schema_name)
    if not valid:
        raise HTTPException(
            status_code=400,
            detail={"message": "Usuario o sector inválido para este expediente", "type": "ValidationError"},
        )

    # Si es ADMIN, desactivar el actual primero
    if responsible_type == "ADMIN":
        await _deactivate_current_admin(case_id, added_by, movement_reason, schema_name=schema_name)

    new_id = str(uuid.uuid4())
    insert_query = """
        INSERT INTO case_responsibles (
            id, case_id, user_id, sector_id, type, added_by, added_at, is_active
        ) VALUES (
            $1, $2, $3, $4, $5, $6, NOW(), true
        )
    """
    try:
        await execute(
            insert_query,
            new_id, case_id, user_id, sector_id, responsible_type, added_by,
            schema_name=schema_name,
            user_id=added_by,
            auth_source="jwt",
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail={"message": "Ya existe un responsable ADMIN activo para este expediente", "type": "UniqueViolation"},
        )

    await _create_responsible_movement(
        case_id=case_id,
        target_user_id=user_id,
        movement_type=MOVEMENT_TYPE_RESPONSIBLE_ADD,
        actor_user_id=added_by,
        reason=movement_reason,
        schema_name=schema_name,
    )

    logger.info(f"Responsible {user_id} ({responsible_type}) added to case {case_id} by {added_by}")
    return {"id": new_id, "case_id": case_id, "user_id": user_id, "type": responsible_type}


async def remove_responsible(
    responsible_id: str,
    removed_by: str,
    movement_reason: str,
    *,
    schema_name: str,
) -> None:
    """
    Soft delete de un responsable: marca is_active=false.
    """
    fetch_query = """
        SELECT case_id, user_id, sector_id, type
        FROM case_responsibles
        WHERE id = $1 AND is_active = true
    """
    rows = await fetch_all(fetch_query, responsible_id, schema_name=schema_name)
    if not rows:
        raise HTTPException(status_code=404, detail={"message": "Responsable no encontrado o ya inactivo", "type": "NotFoundError"})

    row = rows[0]
    case_id = str(row["case_id"])
    user_id = str(row["user_id"])

    update_query = """
        UPDATE case_responsibles
        SET is_active = false,
            removed_by = $1,
            removed_at = NOW()
        WHERE id = $2 AND is_active = true
    """
    await execute(
        update_query,
        removed_by, responsible_id,
        schema_name=schema_name,
        user_id=removed_by,
        auth_source="jwt",
    )

    await _create_responsible_movement(
        case_id=case_id,
        target_user_id=user_id,
        movement_type=MOVEMENT_TYPE_RESPONSIBLE_REMOVE,
        actor_user_id=removed_by,
        reason=movement_reason,
        schema_name=schema_name,
    )

    logger.info(f"Responsible {responsible_id} removed from case {case_id} by {removed_by}")


async def get_available_responsibles(
    case_id: str,
    responsible_type: str,
    *,
    sector_id: str | None = None,
    schema_name: str,
) -> list:
    """
    Retorna lista de usuarios disponibles para ser responsables.
    """
    if sector_id:
        users_query = """
            SELECT DISTINCT
                u.id AS user_id,
                u.full_name,
                u.sector_id,
                u.profile_picture_url,
                d.acronym || '#' || s.acronym AS sector_acronym,
                d.acronym AS department_acronym
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE u.sector_id = $1
              AND u.estado = 1
              AND s.is_active = true
            ORDER BY u.full_name
        """
        rows = await fetch_all(users_query, sector_id, schema_name=schema_name)

    elif responsible_type == "ADMIN":
        admin_sector_query = """
            SELECT cm.admin_sector_id
            FROM case_movements cm
            WHERE cm.case_id = $1
              AND cm.is_active = false
              AND cm.type IN ('creation', 'transfer')
            ORDER BY cm.closed_at DESC
            LIMIT 1
        """
        result = await fetch_all(admin_sector_query, case_id, schema_name=schema_name)
        if not result:
            return []

        admin_sector_id = result[0]["admin_sector_id"]

        users_query = """
            SELECT DISTINCT
                u.id AS user_id,
                u.full_name,
                u.sector_id,
                u.profile_picture_url,
                d.acronym || '#' || s.acronym AS sector_acronym,
                d.acronym AS department_acronym
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE u.sector_id = $1
              AND u.estado = 1
              AND s.is_active = true
            ORDER BY u.full_name
        """
        rows = await fetch_all(users_query, admin_sector_id, schema_name=schema_name)

    else:
        # ADDITIONAL: usuarios de sectores admin o actuantes activos del expediente
        sectors_query = """
            (
                SELECT admin_sector_id AS sector_id
                FROM case_movements
                WHERE case_id = $1
                  AND is_active = false
                  AND type IN ('creation', 'transfer')
                ORDER BY closed_at DESC
                LIMIT 1
            )

            UNION

            SELECT DISTINCT assigned_sector_id AS sector_id
            FROM case_movements
            WHERE case_id = $2
              AND is_active = true
              AND assigned_sector_id IS NOT NULL
        """
        sector_rows = await fetch_all(sectors_query, case_id, case_id, schema_name=schema_name)
        if not sector_rows:
            return []

        sector_ids = [str(r["sector_id"]) for r in sector_rows if r["sector_id"]]
        if not sector_ids:
            return []

        users_query = """
            SELECT DISTINCT
                u.id AS user_id,
                u.full_name,
                u.sector_id,
                u.profile_picture_url,
                d.acronym || '#' || s.acronym AS sector_acronym,
                d.acronym AS department_acronym
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE u.sector_id = ANY($1::uuid[])
              AND u.estado = 1
              AND s.is_active = true

            UNION

            SELECT DISTINCT
                u2.id AS user_id,
                u2.full_name,
                usp.sector_id,
                u2.profile_picture_url,
                d2.acronym || '#' || s2.acronym AS sector_acronym,
                d2.acronym AS department_acronym
            FROM user_sector_permissions usp
            JOIN users u2 ON usp.user_id = u2.id
            JOIN sectors s2 ON usp.sector_id = s2.id
            JOIN departments d2 ON s2.department_id = d2.id
            WHERE usp.sector_id = ANY($2::uuid[])
              AND usp.can_edit = true
              AND u2.estado = 1
              AND s2.is_active = true

            ORDER BY full_name
        """
        rows = await fetch_all(users_query, sector_ids, sector_ids, schema_name=schema_name)

    return [
        {
            "user_id": str(row["user_id"]),
            "full_name": row["full_name"],
            "sector_id": str(row["sector_id"]),
            "sector_acronym": row["sector_acronym"],
            "profile_picture_url": row.get("profile_picture_url"),
            "department_acronym": row.get("department_acronym"),
        }
        for row in rows
    ]


async def remove_responsibles_for_sector(
    case_id: str,
    sector_id: str,
    removed_by: str,
    *,
    schema_name: str,
) -> None:
    """
    Desactiva todos los responsables ADDITIONAL con sector_id=X para ese case.
    """
    fetch_query = """
        SELECT id, user_id
        FROM case_responsibles
        WHERE case_id = $1
          AND sector_id = $2
          AND type = 'ADDITIONAL'
          AND is_active = true
    """
    rows = await fetch_all(fetch_query, case_id, sector_id, schema_name=schema_name)
    if not rows:
        return

    for row in rows:
        responsible_id = str(row["id"])
        user_id = str(row["user_id"])

        await execute(
            """
            UPDATE case_responsibles
            SET is_active = false,
                removed_by = $1,
                removed_at = NOW()
            WHERE id = $2 AND is_active = true
            """,
            removed_by, responsible_id,
            schema_name=schema_name,
            user_id=removed_by,
            auth_source="jwt",
        )

        await _create_responsible_movement(
            case_id=case_id,
            target_user_id=user_id,
            movement_type=MOVEMENT_TYPE_RESPONSIBLE_REMOVE,
            actor_user_id=removed_by,
            reason="Cierre de asignación del sector",
            schema_name=schema_name,
        )

    logger.info(f"Removed all ADDITIONAL responsibles for sector {sector_id} in case {case_id}")


# ============================================================================
# HELPERS INTERNOS
# ============================================================================

async def _deactivate_current_admin(
    case_id: str,
    removed_by: str,
    reason: str,
    *,
    schema_name: str,
) -> None:
    """Desactiva el responsable ADMIN actual del expediente (si existe)."""
    fetch_query = """
        SELECT id, user_id
        FROM case_responsibles
        WHERE case_id = $1
          AND type = 'ADMIN'
          AND is_active = true
        LIMIT 1
    """
    rows = await fetch_all(fetch_query, case_id, schema_name=schema_name)
    if not rows:
        return

    responsible_id = str(rows[0]["id"])
    user_id = str(rows[0]["user_id"])

    await execute(
        """
        UPDATE case_responsibles
        SET is_active = false,
            removed_by = $1,
            removed_at = NOW()
        WHERE id = $2 AND is_active = true
        """,
        removed_by, responsible_id,
        schema_name=schema_name,
        user_id=removed_by,
        auth_source="jwt",
    )

    await _create_responsible_movement(
        case_id=case_id,
        target_user_id=user_id,
        movement_type=MOVEMENT_TYPE_RESPONSIBLE_REMOVE,
        actor_user_id=removed_by,
        reason=reason or "Cambio de responsable administrador",
        schema_name=schema_name,
    )

    logger.info(f"Deactivated current ADMIN responsible {responsible_id} for case {case_id}")


async def _create_responsible_movement(
    case_id: str,
    target_user_id: str,
    movement_type: str,
    actor_user_id: str,
    reason: str,
    *,
    schema_name: str,
) -> None:
    """Crea un movimiento de historial para cambios de responsables."""
    try:
        actor_result = await fetch_all(
            "SELECT sector_id, full_name FROM users WHERE id = $1",
            actor_user_id,
            schema_name=schema_name,
        )
        actor_sector_id = str(actor_result[0]["sector_id"]) if actor_result else None
        actor_name = actor_result[0]["full_name"] if actor_result else "Usuario"

        target_result = await fetch_all(
            "SELECT full_name FROM users WHERE id = $1",
            target_user_id,
            schema_name=schema_name,
        )
        target_name = target_result[0]["full_name"] if target_result else "Usuario"

        if movement_type == MOVEMENT_TYPE_RESPONSIBLE_ADD:
            descriptive_reason = f"{actor_name} asignó a {target_name} como responsable"
        elif movement_type == MOVEMENT_TYPE_RESPONSIBLE_REMOVE:
            descriptive_reason = f"{actor_name} removió a {target_name} como responsable"
        else:
            descriptive_reason = reason or "Cambio de responsable"

        admin_result = await fetch_all(
            """
            SELECT admin_sector_id FROM case_movements
            WHERE case_id = $1 AND is_active = false AND type IN ('creation', 'transfer')
            ORDER BY closed_at DESC LIMIT 1
            """,
            case_id,
            schema_name=schema_name,
        )
        admin_sector_id = str(admin_result[0]["admin_sector_id"]) if admin_result else actor_sector_id

        movement_id = str(uuid.uuid4())
        await execute(
            """
            INSERT INTO case_movements (
                id, case_id, type, user_id, creator_sector_id,
                admin_sector_id, reason, is_active, closed_at, closing_reason
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, false, NOW(), $8
            )
            """,
            movement_id,
            case_id,
            movement_type,
            actor_user_id,
            actor_sector_id,
            admin_sector_id,
            descriptive_reason,
            descriptive_reason,
            schema_name=schema_name,
            user_id=actor_user_id,
            auth_source="jwt",
        )
    except Exception as exc:
        logger.warning(f"Error creating responsible movement for case {case_id}: {exc}")
