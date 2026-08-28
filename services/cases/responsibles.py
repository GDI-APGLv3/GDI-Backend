
import uuid
from typing import List, Optional, Tuple
from fastapi import HTTPException
from database import fetch_all, execute, execute_many, transaction
from shared.logging import get_logger

logger = get_logger(__name__)

MOVEMENT_TYPE_RESPONSIBLE_ADD = "responsible_add"
MOVEMENT_TYPE_RESPONSIBLE_REMOVE = "responsible_remove"


async def get_case_responsibles(case_id: str, *, schema_name: str) -> dict:
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
            u.profile_picture_url,
            d.acronym || '#' || s.acronym AS sector_acronym,
            d.name AS department_name,
            d.acronym AS department_acronym,
            seal.seal_name
        FROM case_responsibles cr
        JOIN users u ON cr.user_id = u.id
        JOIN sectors s ON cr.sector_id = s.id
        JOIN departments d ON s.department_id = d.id
        LEFT JOIN LATERAL (
            SELECT cs.name AS seal_name
            FROM user_seals us
            JOIN city_seals cs ON us.city_seal_id = cs.id
            WHERE us.user_id = cr.user_id
            LIMIT 1
        ) seal ON true
        WHERE cr.case_id = $1
          AND cr.is_active = true
        ORDER BY cr.type DESC, cr.added_at ASC
    """
    rows = await fetch_all(query, case_id, schema_name=schema_name)

    admin = []
    additional = []

    for row in rows:
        entry = {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "sector_id": str(row["sector_id"]),
            "type": row["type"],
            "full_name": row["full_name"],
            "email": row["email"],
            "profile_picture_url": row.get("profile_picture_url"),
            "sector_acronym": row["sector_acronym"],
            "department_name": row["department_name"],
            "department_acronym": row["department_acronym"],
            "seal_name": row.get("seal_name"),
            "added_at": row["added_at"].isoformat() if row["added_at"] else None,
        }
        if row["type"] == "ADMIN":
            admin.append(entry)
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
    conn=None,
) -> dict:
    validation_query = """
        SELECT u.id AS uid, s.id AS sid
        FROM users u, sectors s
        WHERE u.id = $1 AND s.id = $2 AND u.estado = 1 AND s.is_active = true
    """
    if conn is not None:
        valid = await conn.fetch(validation_query, user_id, sector_id)
    else:
        valid = await fetch_all(validation_query, user_id, sector_id, schema_name=schema_name)
    if not valid:
        raise HTTPException(
            status_code=400,
            detail={"message": "Usuario o sector inválido para este expediente", "type": "ValidationError"},
        )

    new_id = str(uuid.uuid4())
    insert_query = """
        INSERT INTO case_responsibles (
            id, case_id, user_id, sector_id, type, added_by, added_at, is_active
        ) VALUES (
            $1, $2, $3, $4, $5, $6, NOW(), true
        )
    """
    if conn is not None:
        await conn.execute(insert_query, new_id, case_id, user_id, sector_id, responsible_type, added_by)
    else:
        await execute(
            insert_query,
            new_id, case_id, user_id, sector_id, responsible_type, added_by,
            schema_name=schema_name,
            user_id=added_by,
            auth_source="jwt",
        )

    await _create_responsible_movement(
        case_id=case_id,
        target_user_id=user_id,
        movement_type=MOVEMENT_TYPE_RESPONSIBLE_ADD,
        actor_user_id=added_by,
        reason=movement_reason,
        schema_name=schema_name,
        responsible_type=responsible_type,
        target_sector_id=sector_id,
        conn=conn,
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
    sector_id = str(row["sector_id"]) if row["sector_id"] else None
    resp_type = row["type"]

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
        responsible_type=resp_type,
        target_sector_id=sector_id,
    )

    logger.info(f"Responsible {responsible_id} removed from case {case_id} by {removed_by}")


async def get_available_responsibles(
    case_id: str,
    responsible_type: str,
    *,
    sector_id: str | None = None,
    schema_name: str,
) -> list:
    if sector_id:
        users_query = """
            SELECT DISTINCT
                u.id AS user_id,
                u.full_name,
                u.sector_id,
                u.profile_picture_url,
                d.acronym || '#' || s.acronym AS sector_acronym,
                d.name AS department_name,
                d.acronym AS department_acronym,
                s.primary_color AS sector_color,
                seal.seal_name
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            LEFT JOIN LATERAL (
                SELECT cs.name AS seal_name
                FROM user_seals us
                JOIN city_seals cs ON us.city_seal_id = cs.id
                WHERE us.user_id = u.id
                LIMIT 1
            ) seal ON true
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
                d.name AS department_name,
                d.acronym AS department_acronym,
                s.primary_color AS sector_color,
                seal.seal_name
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            LEFT JOIN LATERAL (
                SELECT cs.name AS seal_name
                FROM user_seals us
                JOIN city_seals cs ON us.city_seal_id = cs.id
                WHERE us.user_id = u.id
                LIMIT 1
            ) seal ON true
            WHERE u.sector_id = $1
              AND u.estado = 1
              AND s.is_active = true

            UNION

            SELECT DISTINCT
                u2.id AS user_id,
                u2.full_name,
                usp.sector_id,
                u2.profile_picture_url,
                d2.acronym || '#' || s2.acronym AS sector_acronym,
                d2.name AS department_name,
                d2.acronym AS department_acronym,
                s2.primary_color AS sector_color,
                seal.seal_name
            FROM user_sector_permissions usp
            JOIN users u2 ON usp.user_id = u2.id
            JOIN sectors s2 ON usp.sector_id = s2.id
            JOIN departments d2 ON s2.department_id = d2.id
            LEFT JOIN LATERAL (
                SELECT cs.name AS seal_name
                FROM user_seals us
                JOIN city_seals cs ON us.city_seal_id = cs.id
                WHERE us.user_id = u2.id
                LIMIT 1
            ) seal ON true
            WHERE usp.sector_id = $1
              AND usp.can_edit = true
              AND u2.estado = 1
              AND s2.is_active = true

            ORDER BY full_name
        """
        rows = await fetch_all(users_query, admin_sector_id, schema_name=schema_name)

    else:
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
                d.name AS department_name,
                d.acronym AS department_acronym,
                s.primary_color AS sector_color,
                seal.seal_name
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            LEFT JOIN LATERAL (
                SELECT cs.name AS seal_name
                FROM user_seals us
                JOIN city_seals cs ON us.city_seal_id = cs.id
                WHERE us.user_id = u.id
                LIMIT 1
            ) seal ON true
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
                d2.name AS department_name,
                d2.acronym AS department_acronym,
                s2.primary_color AS sector_color,
                seal.seal_name
            FROM user_sector_permissions usp
            JOIN users u2 ON usp.user_id = u2.id
            JOIN sectors s2 ON usp.sector_id = s2.id
            JOIN departments d2 ON s2.department_id = d2.id
            LEFT JOIN LATERAL (
                SELECT cs.name AS seal_name
                FROM user_seals us
                JOIN city_seals cs ON us.city_seal_id = cs.id
                WHERE us.user_id = u2.id
                LIMIT 1
            ) seal ON true
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
            "sector_color": row.get("sector_color"),
            "profile_picture_url": row.get("profile_picture_url"),
            "department_name": row.get("department_name"),
            "department_acronym": row.get("department_acronym"),
            "seal_name": row.get("seal_name"),
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

    responsible_ids = [str(r["id"]) for r in rows]
    targets: List[Tuple[str, Optional[str]]] = [(str(r["user_id"]), sector_id) for r in rows]

    async with transaction(schema_name=schema_name, user_id=removed_by, auth_source="jwt") as conn:
        await conn.execute(
            """
            UPDATE case_responsibles
            SET is_active = false,
                removed_by = $1,
                removed_at = NOW()
            WHERE id = ANY($2::uuid[]) AND is_active = true
            """,
            removed_by, responsible_ids,
        )

        await _create_responsible_movements_bulk(
            case_id=case_id,
            targets=targets,
            movement_type=MOVEMENT_TYPE_RESPONSIBLE_REMOVE,
            actor_user_id=removed_by,
            reason="Cierre de asignación del sector",
            schema_name=schema_name,
            responsible_type="ADDITIONAL",
            conn=conn,
        )

    logger.info(f"Removed all ADDITIONAL responsibles for sector {sector_id} in case {case_id}")


async def _deactivate_all_admins(
    case_id: str,
    removed_by: str,
    reason: str,
    *,
    schema_name: str,
    conn=None,
) -> None:
    fetch_query = """
        SELECT id, user_id, sector_id
        FROM case_responsibles
        WHERE case_id = $1
          AND type = 'ADMIN'
          AND is_active = true
    """
    if conn is not None:
        rows = await conn.fetch(fetch_query, case_id)
    else:
        rows = await fetch_all(fetch_query, case_id, schema_name=schema_name)
    if not rows:
        return

    responsible_ids = [str(r["id"]) for r in rows]
    targets: List[Tuple[str, Optional[str]]] = [
        (str(r["user_id"]), str(r["sector_id"]) if r["sector_id"] else None) for r in rows
    ]

    update_sql = """
        UPDATE case_responsibles
        SET is_active = false,
            removed_by = $1,
            removed_at = NOW()
        WHERE id = ANY($2::uuid[]) AND is_active = true
    """

    async def _run(active_conn) -> None:
        await active_conn.execute(update_sql, removed_by, responsible_ids)
        await _create_responsible_movements_bulk(
            case_id=case_id,
            targets=targets,
            movement_type=MOVEMENT_TYPE_RESPONSIBLE_REMOVE,
            actor_user_id=removed_by,
            reason=reason or "Transferencia de expediente",
            schema_name=schema_name,
            responsible_type="ADMIN",
            conn=active_conn,
        )

    if conn is not None:
        await _run(conn)
    else:
        async with transaction(schema_name=schema_name, user_id=removed_by, auth_source="jwt") as own_conn:
            await _run(own_conn)

    logger.info(f"Deactivated {len(responsible_ids)} ADMIN responsible(s) for case {case_id} during transfer")


async def _create_responsible_movement(
    case_id: str,
    target_user_id: str,
    movement_type: str,
    actor_user_id: str,
    reason: str,
    *,
    schema_name: str,
    responsible_type: str = "ADDITIONAL",
    target_sector_id: Optional[str] = None,
    conn=None,
) -> None:
    try:
        if conn is not None:
            actor_rows = await conn.fetch(
                "SELECT sector_id, full_name FROM users WHERE id = $1", actor_user_id
            )
            actor_sector_id = str(actor_rows[0]["sector_id"]) if actor_rows else None
            actor_name = actor_rows[0]["full_name"] if actor_rows else "Usuario"

            target_rows = await conn.fetch(
                "SELECT full_name FROM users WHERE id = $1", target_user_id
            )
            target_name = target_rows[0]["full_name"] if target_rows else "Usuario"

            admin_rows = await conn.fetch(
                """
                SELECT admin_sector_id FROM case_movements
                WHERE case_id = $1 AND is_active = false AND type IN ('creation', 'transfer')
                ORDER BY closed_at DESC LIMIT 1
                """,
                case_id,
            )
            admin_sector_id = str(admin_rows[0]["admin_sector_id"]) if admin_rows else actor_sector_id
        else:
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

        subtipo = "administrador" if responsible_type == "ADMIN" else "adicional"
        if movement_type == MOVEMENT_TYPE_RESPONSIBLE_ADD:
            descriptive_reason = f"{actor_name} asignó a {target_name} como responsable {subtipo}"
        elif movement_type == MOVEMENT_TYPE_RESPONSIBLE_REMOVE:
            descriptive_reason = f"{actor_name} removió a {target_name} como responsable {subtipo}"
        else:
            descriptive_reason = reason or "Cambio de responsable"

        movement_id = str(uuid.uuid4())
        insert_sql = """
            INSERT INTO case_movements (
                id, case_id, type, user_id, creator_sector_id,
                admin_sector_id, assigned_user_id, assigned_sector_id,
                reason, is_active, closed_at, closing_reason
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, false, NOW(), $10
            )
        """
        if conn is not None:
            await conn.execute(
                insert_sql,
                movement_id, case_id, movement_type, actor_user_id, actor_sector_id,
                admin_sector_id, target_user_id, target_sector_id,
                descriptive_reason, descriptive_reason,
            )
        else:
            await execute(
                insert_sql,
                movement_id, case_id, movement_type, actor_user_id, actor_sector_id,
                admin_sector_id, target_user_id, target_sector_id,
                descriptive_reason, descriptive_reason,
                schema_name=schema_name,
                user_id=actor_user_id,
                auth_source="jwt",
            )
    except Exception as exc:
        logger.warning(f"Error creating responsible movement for case {case_id}: {exc}")


async def _create_responsible_movements_bulk(
    case_id: str,
    targets: List[Tuple[str, Optional[str]]],
    movement_type: str,
    actor_user_id: str,
    reason: str,
    *,
    schema_name: str,
    responsible_type: str = "ADDITIONAL",
    conn,
) -> None:
    if not targets:
        return

    target_user_ids = [t[0] for t in targets]

    try:
        actor_rows = await conn.fetch(
            "SELECT sector_id, full_name FROM users WHERE id = $1", actor_user_id
        )
        actor_sector_id = str(actor_rows[0]["sector_id"]) if actor_rows and actor_rows[0]["sector_id"] else None
        actor_name = actor_rows[0]["full_name"] if actor_rows else "Usuario"

        target_rows = await conn.fetch(
            "SELECT id, full_name FROM users WHERE id = ANY($1::uuid[])", target_user_ids
        )
        target_names = {str(r["id"]): r["full_name"] for r in target_rows}

        admin_rows = await conn.fetch(
            """
            SELECT admin_sector_id FROM case_movements
            WHERE case_id = $1 AND is_active = false AND type IN ('creation', 'transfer')
            ORDER BY closed_at DESC LIMIT 1
            """,
            case_id,
        )
        admin_sector_id = (
            str(admin_rows[0]["admin_sector_id"]) if admin_rows and admin_rows[0]["admin_sector_id"] else actor_sector_id
        )

        subtipo = "administrador" if responsible_type == "ADMIN" else "adicional"

        movement_ids: List[str] = []
        types_arr: List[str] = []
        user_ids_arr: List[str] = []
        creator_sector_ids_arr: List[Optional[str]] = []
        admin_sector_ids_arr: List[Optional[str]] = []
        assigned_user_ids_arr: List[str] = []
        assigned_sector_ids_arr: List[Optional[str]] = []
        reasons_arr: List[str] = []

        for target_user_id, target_sector_id in targets:
            target_name = target_names.get(target_user_id, "Usuario")
            if movement_type == MOVEMENT_TYPE_RESPONSIBLE_ADD:
                descriptive_reason = f"{actor_name} asignó a {target_name} como responsable {subtipo}"
            elif movement_type == MOVEMENT_TYPE_RESPONSIBLE_REMOVE:
                descriptive_reason = f"{actor_name} removió a {target_name} como responsable {subtipo}"
            else:
                descriptive_reason = reason or "Cambio de responsable"

            movement_ids.append(str(uuid.uuid4()))
            types_arr.append(movement_type)
            user_ids_arr.append(actor_user_id)
            creator_sector_ids_arr.append(actor_sector_id)
            admin_sector_ids_arr.append(admin_sector_id)
            assigned_user_ids_arr.append(target_user_id)
            assigned_sector_ids_arr.append(target_sector_id)
            reasons_arr.append(descriptive_reason)

        insert_sql = """
            INSERT INTO case_movements (
                id, case_id, type, user_id, creator_sector_id,
                admin_sector_id, assigned_user_id, assigned_sector_id,
                reason, is_active, closed_at, closing_reason
            )
            SELECT
                m.id, $1, m.type::public.movement_type, m.user_id, m.creator_sector_id,
                m.admin_sector_id, m.assigned_user_id, m.assigned_sector_id,
                m.reason, false, NOW(), m.reason
            FROM UNNEST(
                $2::uuid[], $3::text[], $4::uuid[], $5::uuid[],
                $6::uuid[], $7::uuid[], $8::uuid[], $9::text[]
            ) AS m(id, type, user_id, creator_sector_id, admin_sector_id,
                   assigned_user_id, assigned_sector_id, reason)
        """
        await execute_many(
            conn,
            insert_sql,
            case_id, movement_ids, types_arr, user_ids_arr, creator_sector_ids_arr,
            admin_sector_ids_arr, assigned_user_ids_arr, assigned_sector_ids_arr, reasons_arr,
        )
    except Exception as exc:
        logger.warning(f"Error creating bulk responsible movements for case {case_id}: {exc}")
