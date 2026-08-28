import uuid
from typing import Optional

from config.constants import MOVEMENT_TYPE_CITIZEN_SHARE, MOVEMENT_TYPE_CITIZEN_UNSHARE
from shared.exceptions import ConflictError, NotFoundError
from shared.logging import get_logger
from database import fetch_all, execute
from services.cases.citizen_shares_queries import (
    citizen_exists_query,
    get_active_share_query,
    get_case_admin_sector_query,
    get_case_creator_citizen_query,
    get_citizen_name_and_country_id_query,
    get_user_sector_and_name_query,
    insert_citizen_share_query,
    unshare_query,
    list_shares_of_case_query,
    list_cases_shared_with_citizen_query,
)
from services.cases.permissions import can_user_edit_case

logger = get_logger(__name__)


async def share_case_with_citizen(
    case_id: str,
    citizen_id: str,
    shared_by: Optional[str],
    *,
    schema_name: str,
    conn=None,
) -> dict:
    if conn is not None:
        citizen_rows = await conn.fetch(citizen_exists_query(), citizen_id)
    else:
        citizen_rows = await fetch_all(citizen_exists_query(), citizen_id, schema_name=schema_name)
    if not citizen_rows:
        raise NotFoundError("Ciudadano no encontrado")
    if citizen_rows[0]["estado"] == "bloqueado":
        raise ConflictError("No se puede compartir un expediente con un ciudadano bloqueado")

    if conn is not None:
        existing = await conn.fetch(get_active_share_query(), case_id, citizen_id)
    else:
        existing = await fetch_all(get_active_share_query(), case_id, citizen_id, schema_name=schema_name)
    if existing:
        logger.info(f"[CitizenShares] share ya activo case={case_id} citizen={citizen_id} (no-op)")
        return {
            "id": str(existing[0]["id"]),
            "case_id": case_id,
            "citizen_id": citizen_id,
            "already_shared": True,
        }

    new_id = str(uuid.uuid4())
    if conn is not None:
        await conn.execute(insert_citizen_share_query(), new_id, case_id, citizen_id, shared_by)
    else:
        await execute(
            insert_citizen_share_query(),
            new_id, case_id, citizen_id, shared_by,
            schema_name=schema_name,
            user_id=shared_by,
            auth_source="jwt" if shared_by else "tad",
        )

    await _record_citizen_share_movement(
        case_id, citizen_id, MOVEMENT_TYPE_CITIZEN_SHARE, actor_user_id=shared_by,
        schema_name=schema_name, conn=conn,
    )

    logger.info(f"[CitizenShares] case={case_id} citizen={citizen_id} shared_by={shared_by or 'auto(TAD)'}")
    return {"id": new_id, "case_id": case_id, "citizen_id": citizen_id, "already_shared": False}


async def unshare_case_from_citizen(
    case_id: str,
    citizen_id: str,
    removed_by: str,
    *,
    schema_name: str,
) -> None:
    creator_rows = await fetch_all(get_case_creator_citizen_query(), case_id, schema_name=schema_name)
    initiator_ids = set()
    if creator_rows:
        for column in ("created_by_citizen", "initiator_citizen_id"):
            value = creator_rows[0][column]
            if value:
                initiator_ids.add(str(value))
    if citizen_id in initiator_ids:
        raise ConflictError("No se puede quitar al ciudadano que inició el expediente")

    rows = await fetch_all(unshare_query(), removed_by, case_id, citizen_id, schema_name=schema_name)
    if not rows:
        raise NotFoundError("El expediente no esta compartido (activamente) con este ciudadano")

    await _record_citizen_share_movement(
        case_id, citizen_id, MOVEMENT_TYPE_CITIZEN_UNSHARE, actor_user_id=removed_by,
        schema_name=schema_name,
    )

    logger.info(f"[CitizenShares] case={case_id} citizen={citizen_id} unshared by={removed_by}")


async def list_shares_of_case(case_id: str, *, schema_name: str) -> list:
    rows = await fetch_all(list_shares_of_case_query(), case_id, schema_name=schema_name)
    return [
        {
            "id": str(row["id"]),
            "citizen_id": str(row["citizen_id"]),
            "full_name": row["full_name"],
            "country_id": row["country_id"],
            "citizen_estado": row["citizen_estado"],
            "shared_by": str(row["shared_by"]) if row["shared_by"] else None,
            "shared_at": row["shared_at"].isoformat() if row["shared_at"] else None,
        }
        for row in rows
    ]


async def list_cases_shared_with_citizen(citizen_id: str, *, schema_name: str) -> list:
    rows = await fetch_all(list_cases_shared_with_citizen_query(), citizen_id, schema_name=schema_name)
    return [
        {
            "case_id": str(row["case_id"]),
            "case_number": row["case_number"],
            "reference": row["reference"],
            "status": row["status"],
            "template_name": row["template_name"],
            "template_acronym": row["template_acronym"],
            "shared_at": row["shared_at"].isoformat() if row["shared_at"] else None,
        }
        for row in rows
    ]


async def can_citizen_access_case(case_id: str, citizen_id: str, *, schema_name: str) -> bool:
    rows = await fetch_all(get_active_share_query(), case_id, citizen_id, schema_name=schema_name)
    return bool(rows)


async def can_user_manage_citizen_shares(case_id: str, user_id: str, *, schema_name: str) -> bool:
    return await can_user_edit_case(case_id, user_id, schema_name=schema_name)


async def _record_citizen_share_movement(
    case_id: str,
    citizen_id: str,
    movement_type: str,
    *,
    actor_user_id: Optional[str],
    schema_name: str,
    conn=None,
) -> None:
    try:
        async def _q(query: str, *args):
            if conn is not None:
                return await conn.fetch(query, *args)
            return await fetch_all(query, *args, schema_name=schema_name)

        citizen_rows = await _q(get_citizen_name_and_country_id_query(), citizen_id)
        citizen_name = citizen_rows[0]["full_name"] if citizen_rows else "Ciudadano"
        citizen_country_id = citizen_rows[0]["country_id"] if citizen_rows else ""

        admin_rows = await _q(get_case_admin_sector_query(), case_id)
        admin_sector_id = (
            str(admin_rows[0]["admin_sector_id"])
            if admin_rows and admin_rows[0]["admin_sector_id"] else None
        )

        if actor_user_id is not None:
            actor_rows = await _q(get_user_sector_and_name_query(), actor_user_id)
            actor_sector_id = (
                str(actor_rows[0]["sector_id"])
                if actor_rows and actor_rows[0]["sector_id"] else admin_sector_id
            )
            actor_name = actor_rows[0]["full_name"] if actor_rows else "Usuario"
            movement_user_id, movement_citizen_actor_id = actor_user_id, None
            auth_source = "jwt"
        else:
            actor_sector_id = admin_sector_id
            actor_name = citizen_name
            movement_user_id, movement_citizen_actor_id = None, citizen_id
            auth_source = "tad"

        if movement_type == MOVEMENT_TYPE_CITIZEN_SHARE:
            if actor_user_id is None:
                reason = (
                    f"El expediente se compartió automáticamente con el ciudadano "
                    f"{citizen_name} ({citizen_country_id}) por ser quien lo inició"
                )
            else:
                reason = f"{actor_name} compartió el expediente con el ciudadano {citizen_name} ({citizen_country_id})"
        else:
            reason = f"{actor_name} quitó al ciudadano {citizen_name} ({citizen_country_id}) del expediente"

        movement_id = str(uuid.uuid4())
        insert_sql = """
            INSERT INTO case_movements (
                id, case_id, type, user_id, citizen_id,
                creator_sector_id, admin_sector_id,
                reason, is_active, closed_at, closing_reason
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, false, NOW(), $8
            )
        """
        params = (
            movement_id, case_id, movement_type, movement_user_id, movement_citizen_actor_id,
            actor_sector_id, admin_sector_id or actor_sector_id, reason,
        )
        if conn is not None:
            await conn.execute(insert_sql, *params)
        else:
            await execute(
                insert_sql, *params,
                schema_name=schema_name,
                user_id=movement_user_id or movement_citizen_actor_id,
                auth_source=auth_source,
            )
    except Exception as exc:
        logger.warning(f"Error creating citizen share movement for case {case_id}: {exc}")
