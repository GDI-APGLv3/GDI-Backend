
from datetime import datetime
from typing import Optional
from shared.logging import get_logger
from database import fetch_all, fetch_one, transaction
from shared.exceptions import ValidationError, NotFoundError, AuthorizationError
from services.rlm.queries import (
    get_record_detail_query,
    insert_record_query,
    update_record_query,
    get_user_sector_info_query,
    get_next_record_sequence_query,
    insert_history_query,
    autocomplete_records_query,
)
from services.rlm.registries import get_registry_by_code
from services.rlm.validation import validate_record_data, calculate_next_expiration
from services.rlm.permissions import check_permission, get_user_permissions, get_bulk_permissions
from services.rlm.history import record_action

logger = get_logger(__name__)

RLM_NUMBERING_LOCK_ID = 777777


async def create_record(
    registry_code: str,
    data: dict,
    display_name: str,
    user_id: str,
    *,
    schema_name: str
) -> dict:
    registry = await get_registry_by_code(registry_code, schema_name=schema_name)

    if not await check_permission(registry["id"], user_id, "can_create", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para crear legajos en este registro")

    user_info = await fetch_one(
        get_user_sector_info_query(),
        user_id,
        schema_name=schema_name,
    )
    if not user_info:
        raise ValidationError(f"Usuario {user_id} no encontrado")

    validated_data = await validate_record_data(data, registry.get("data_schema") or {}, skip_required=True, schema_name=schema_name)

    next_exp = calculate_next_expiration(validated_data, registry.get("data_schema") or {})

    states = registry.get("states", [])
    default_state = states[0] if states else "Activo"

    async with transaction(schema_name=schema_name, user_id=user_id) as conn:
        muni_result = await conn.fetchrow(
            "SELECT acronym FROM public.municipalities WHERE schema_name = $1",
            schema_name,
        )
        muni_acronym = muni_result["acronym"] if muni_result else "UNK"

        await conn.execute("SET LOCAL lock_timeout = '10s'")
        await conn.execute(
            f"SELECT pg_advisory_xact_lock({RLM_NUMBERING_LOCK_ID}, hashtext($1))",
            schema_name
        )

        year = datetime.now().year
        pattern = f"RLM-{year}-%"
        seq_result = await conn.fetchrow(get_next_record_sequence_query(), pattern)
        next_seq = seq_result["next_sequence"] if seq_result else 1

        record_number = f"RLM-{year}-{next_seq:08d}-{muni_acronym}-{registry_code.upper()}"

        result = await conn.fetchrow(
            insert_record_query(),
            registry["id"],
            record_number,
            default_state,
            validated_data,
            user_id,
            user_info.get("sector_id"),
            next_exp,
            display_name,
        )
        record_id = result["id"]

    await record_action(
        record_id=record_id,
        action="created",
        user_id=user_id,
        sector_id=user_info.get("sector_id"),
        after_value={"state": default_state, "data": validated_data},
        schema_name=schema_name
    )

    logger.info(f"Record created: {record_number} by user {user_id[:8]}")


    return {
        "id": record_id,
        "record_number": record_number,
        "display_name": display_name,
        "state": default_state,
        "registry_code": registry_code,
        "registry_name": registry["name"],
        "created_at": str(result["created_at"]),
    }


async def get_record(record_id: str, user_id: str, *, schema_name: str) -> dict:
    result = await fetch_one(
        get_record_detail_query(),
        record_id,
        schema_name=schema_name,
    )

    if not result:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    perms = await get_user_permissions(result["registry_family_id"], user_id, schema_name=schema_name)

    if not perms.get("can_view"):
        raise AuthorizationError("No tiene permiso para ver este legajo")

    return {
        "id": result["id"],
        "record_number": result["record_number"],
        "display_name": result["display_name"],
        "state": result["state"],
        "data": result["data"],
        "resume": result.get("resume"),
        "next_expiration": str(result["next_expiration"]) if result["next_expiration"] else None,
        "created_at": str(result["created_at"]),
        "updated_at": str(result["updated_at"]),
        "registry": {
            "id": result["registry_family_id"],
            "code": result["registry_code"],
            "name": result["registry_name"],
            "data_schema": result["data_schema"],
            "allowed_states": result["states"],
        },
        "created_by": {
            "user_id": result["created_by_user_id"],
            "name": result["created_by_name"],
            "sector": result["created_by_sector"],
            "department": result["created_by_department"],
        },
        "permissions": perms,
    }


async def list_records(
    user_id: str,
    *,
    schema_name: str,
    registry_code: Optional[str] = None,
    state: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    permissions_map = await get_bulk_permissions(user_id, schema_name=schema_name)
    viewable_family_ids = [
        fid for fid, perms in permissions_map.items() if perms.get("can_view")
    ]

    if not viewable_family_ids:
        logger.info(f"User {user_id[:8]} has no can_view permissions on any registry")
        return {
            "records": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
        }

    params: list = list(viewable_family_ids)
    param_idx = len(params) + 1

    in_placeholders = ", ".join([f"${i+1}" for i in range(len(viewable_family_ids))])
    where_clauses = [f"r.registry_family_id IN ({in_placeholders})"]

    if registry_code:
        where_clauses.append(f"rf.code = ${param_idx}")
        params.append(registry_code.upper())
        param_idx += 1

    if state:
        where_clauses.append(f"r.state = ${param_idx}")
        params.append(state)
        param_idx += 1

    if search and len(search) >= 2:
        where_clauses.append(
            f"(r.record_number ILIKE ${param_idx} OR r.display_name ILIKE ${param_idx+1} OR r.data::text ILIKE ${param_idx+2})"
        )
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        search_pattern = f"%{escaped}%"
        params.extend([search_pattern, search_pattern, search_pattern])
        param_idx += 3

    where_sql = " AND ".join(where_clauses)
    count_sql = f"""
        SELECT COUNT(*) as total
        FROM records r
        JOIN registry_families rf ON r.registry_family_id = rf.id
        WHERE {where_sql}
    """
    count_result = await fetch_one(count_sql, *params, schema_name=schema_name)
    total = count_result["total"] if count_result else 0

    offset = (page - 1) * page_size
    list_params = params + [page_size, offset]
    limit_ph = f"${param_idx}"
    offset_ph = f"${param_idx + 1}"

    list_sql = f"""
        SELECT
            r.id,
            r.record_number,
            r.display_name,
            r.state,
            r.resume,
            r.next_expiration,
            r.created_at,
            r.updated_at,
            rf.code as registry_code,
            rf.name as registry_name,
            u.full_name as created_by_name,
            s.acronym as created_by_sector,
            d.acronym as created_by_department
        FROM records r
        JOIN registry_families rf ON r.registry_family_id = rf.id
        JOIN users u ON r.created_by_user_id = u.id
        LEFT JOIN sectors s ON r.created_by_sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE {where_sql}
        ORDER BY r.created_at DESC
        LIMIT {limit_ph} OFFSET {offset_ph}
    """

    results = await fetch_all(list_sql, *list_params, schema_name=schema_name)

    records = []
    for row in (results or []):
        records.append({
            "id": row["id"],
            "record_number": row["record_number"],
            "display_name": row["display_name"],
            "state": row["state"],
            "resume": row.get("resume"),
            "next_expiration": str(row["next_expiration"]) if row["next_expiration"] else None,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "registry": {
                "code": row["registry_code"],
                "name": row["registry_name"],
            },
            "created_by": {
                "name": row["created_by_name"],
                "sector": row["created_by_sector"],
                "department": row["created_by_department"],
            },
        })

    total_pages = max(1, -(-total // page_size))

    logger.info(f"Listed {len(records)} records (page {page}/{total_pages})")

    return {
        "records": records,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


async def update_record(
    record_id: str,
    user_id: str,
    *,
    schema_name: str,
    new_state: Optional[str] = None,
    new_display_name: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    record = await fetch_one(
        get_record_detail_query(),
        record_id,
        schema_name=schema_name,
    )

    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    if not await check_permission(record["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para actualizar este legajo")

    if new_state is not None:
        allowed = record.get("states", [])
        if allowed and new_state not in allowed:
            raise ValidationError(f"Estado '{new_state}' no es válido. Estados permitidos: {allowed}")

    old_state = record["state"]
    old_display_name = record.get("display_name")

    user_info = await fetch_one(
        get_user_sector_info_query(),
        user_id,
        schema_name=schema_name,
    )

    update_state = new_state is not None
    update_display_name = new_display_name is not None
    query_sql = update_record_query(update_state=update_state, update_display_name=update_display_name)

    params = []
    if update_state:
        params.append(new_state)
    if update_display_name:
        params.append(new_display_name)
    params.append(record_id)

    async with transaction(schema_name=schema_name, user_id=user_id) as conn:
        result = await conn.fetchrow(query_sql, *params)

        before_data = {}
        after_data = {}
        if update_state:
            before_data["state"] = old_state
            after_data["state"] = new_state
        if update_display_name:
            before_data["display_name"] = old_display_name
            after_data["display_name"] = new_display_name
        if reason:
            after_data["reason"] = reason

        action = "state_changed" if update_state else "display_name_changed"
        if update_state and update_display_name:
            action = "record_updated"

        await conn.execute(
            insert_history_query(),
            record_id,
            action,
            None,
            before_data,
            after_data,
            user_id,
            user_info.get("sector_id") if user_info else None,
        )

    changes = []
    if update_state:
        changes.append(f"state: {old_state} -> {new_state}")
    if update_display_name:
        changes.append(f"display_name: '{old_display_name}' -> '{new_display_name}'")
    logger.info(f"Record {record_id[:8]} updated: {', '.join(changes)}")

    return {
        "id": result["id"],
        "state": result["state"],
        "display_name": result["display_name"],
        "updated_at": str(result["updated_at"]),
    }


async def autocomplete_records(
    user_id: str,
    query: str,
    *,
    schema_name: str,
    limit: int = 10,
) -> dict:
    from services.rlm.permissions import _get_user_sector_ids

    sector_ids = await _get_user_sector_ids(user_id, schema_name=schema_name)

    if not sector_ids:
        return {"records": [], "total": 0, "query": query}

    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    search_pattern = f"%{escaped}%"

    sql = autocomplete_records_query(sector_ids)
    params = list(sector_ids) + [search_pattern, search_pattern, limit]

    results = await fetch_all(sql, *params, schema_name=schema_name)

    records = [
        {
            "record_id": row["record_id"],
            "record_number": row["record_number"],
            "display_name": row["display_name"],
            "family_code": row["family_code"],
            "state": row["state"],
        }
        for row in (results or [])
    ]

    return {"records": records, "total": len(records), "query": query}
