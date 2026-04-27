"""
Servicio CRUD para legajos (records).
"""

import json
from datetime import datetime
from typing import Optional
from shared.logging import get_logger
from database import execute_query, execute_transaction
from shared.exceptions import ValidationError, NotFoundError, AuthorizationError
from services.rlm.queries import (
    get_records_list_query,
    get_records_count_query,
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

# Advisory lock ID para numeración de legajos (diferente del 888888 de documentos)
RLM_NUMBERING_LOCK_ID = 777777


def create_record(
    registry_code: str,
    data: dict,
    display_name: str,
    user_id: str,
    *,
    schema_name: str
) -> dict:
    """
    Crea un nuevo legajo.

    Args:
        registry_code: Código del registro (ARQ, LUM, ORD)
        data: Campos enriquecidos
        user_id: UUID del usuario creador
        schema_name: Schema del tenant

    Returns:
        Dict con datos del legajo creado

    Raises:
        ValidationError: Si los datos no son válidos
        NotFoundError: Si el registro no existe
        AuthorizationError: Si no tiene permisos
    """
    # 1. Obtener el registro
    registry = get_registry_by_code(registry_code, schema_name=schema_name)

    # 2. Verificar permiso can_create
    if not check_permission(registry["id"], user_id, "can_create", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para crear legajos en este registro")

    # 3. Obtener info del usuario
    user_info = execute_query(
        get_user_sector_info_query(),
        (user_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    if not user_info:
        raise ValidationError(f"Usuario {user_id} no encontrado")

    # 4. Validar datos contra schema (skip_required en creación — campos se completan después)
    validated_data = validate_record_data(data, registry.get("data_schema") or {}, skip_required=True, schema_name=schema_name)

    # 5. Calcular next_expiration
    next_exp = calculate_next_expiration(validated_data, registry.get("data_schema") or {})

    # 6. Generar número + insertar en una sola transacción atómica
    states = registry.get("states", [])
    default_state = states[0] if states else "Activo"

    with execute_transaction(schema_name=schema_name, user_id=user_id) as (conn, cursor):
        # 6a. Obtener acrónimo del municipio
        cursor.execute(
            "SELECT acronym FROM public.municipalities WHERE schema_name = %s",
            (schema_name,)
        )
        muni_result = cursor.fetchone()
        muni_acronym = muni_result["acronym"] if muni_result else "UNK"

        # 6b. Advisory lock (se libera al commit/rollback)
        cursor.execute("SET LOCAL lock_timeout = '10s'")
        cursor.execute(f"SELECT pg_advisory_xact_lock({RLM_NUMBERING_LOCK_ID})")

        # 6c. Obtener siguiente secuencia
        year = datetime.now().year
        pattern = f"RLM-{year}-%"
        cursor.execute(get_next_record_sequence_query(), (pattern,))
        seq_result = cursor.fetchone()
        next_seq = seq_result["next_sequence"] if seq_result else 1

        record_number = f"RLM-{year}-{next_seq:08d}-{muni_acronym}-{registry_code.upper()}"

        # 6d. Insertar el legajo (misma transacción)
        cursor.execute(
            insert_record_query(),
            (
                registry["id"],
                record_number,
                default_state,
                json.dumps(validated_data),
                user_id,
                user_info.get("sector_id"),
                next_exp,
                display_name,
            )
        )
        result = cursor.fetchone()
        record_id = result["id"]

    # 7. Registrar en historial (fuera de la transacción principal)
    record_action(
        record_id=record_id,
        action="created",
        user_id=user_id,
        sector_id=user_info.get("sector_id"),
        after_value={"state": default_state, "data": validated_data},
        schema_name=schema_name
    )

    logger.info(f"Record created: {record_number} by user {user_id[:8]}")

    # NOTA: IFRLM inicial se genera desde el endpoint (async) despues de create_record().
    # Ya no se llama desde aqui porque generate_ifrlm() es async.

    return {
        "id": record_id,
        "record_number": record_number,
        "display_name": display_name,
        "state": default_state,
        "registry_code": registry_code,
        "registry_name": registry["name"],
        "created_at": str(result["created_at"]),
    }


def get_record(record_id: str, user_id: str, *, schema_name: str) -> dict:
    """
    Obtiene el detalle completo de un legajo.

    Args:
        record_id: UUID del legajo
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Returns:
        Dict con detalle del legajo + permisos

    Raises:
        NotFoundError: Si el legajo no existe
    """
    result = execute_query(
        get_record_detail_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )

    if not result:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    # Obtener permisos del usuario
    perms = get_user_permissions(result["registry_family_id"], user_id, schema_name=schema_name)

    # Verificar can_view antes de devolver datos
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


def list_records(
    user_id: str,
    *,
    schema_name: str,
    registry_code: Optional[str] = None,
    state: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """
    Lista legajos con filtros y paginación.

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant
        registry_code: Filtrar por código de registro
        state: Filtrar por estado
        search: Búsqueda por número o datos
        page: Página actual
        page_size: Items por página

    Returns:
        Dict con lista paginada de legajos
    """
    # Filtrar por permisos: solo registros donde el usuario tiene can_view
    permissions_map = get_bulk_permissions(user_id, schema_name=schema_name)
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

    placeholders = ", ".join(["%s"] * len(viewable_family_ids))
    where_clauses = [f"r.registry_family_id IN ({placeholders})"]
    params = list(viewable_family_ids)

    if registry_code:
        where_clauses.append("rf.code = %s")
        params.append(registry_code.upper())

    if state:
        where_clauses.append("r.state = %s")
        params.append(state)

    if search and len(search) >= 2:
        where_clauses.append(
            "(r.record_number ILIKE %s OR r.display_name ILIKE %s OR r.data::text ILIKE %s)"
        )
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        search_pattern = f"%{escaped}%"
        params.extend([search_pattern, search_pattern, search_pattern])

    # Count query
    count_result = execute_query(
        get_records_count_query(where_clauses),
        tuple(params) if params else None,
        schema_name=schema_name,
        fetch_one=True
    )
    total = count_result["total"] if count_result else 0

    # Paginated list query
    offset = (page - 1) * page_size
    list_params = params + [page_size, offset]

    results = execute_query(
        get_records_list_query(where_clauses),
        tuple(list_params),
        schema_name=schema_name
    )

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

    total_pages = max(1, -(-total // page_size))  # ceil division

    logger.info(f"Listed {len(records)} records (page {page}/{total_pages})")

    return {
        "records": records,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def update_record(
    record_id: str,
    user_id: str,
    *,
    schema_name: str,
    new_state: Optional[str] = None,
    new_display_name: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    """
    Actualiza el estado y/o nombre de un legajo.

    Args:
        record_id: UUID del legajo
        user_id: UUID del usuario
        schema_name: Schema del tenant
        new_state: Nuevo estado (opcional)
        new_display_name: Nuevo nombre (opcional)
        reason: Motivo del cambio

    Returns:
        Dict con datos actualizados

    Raises:
        NotFoundError, AuthorizationError, ValidationError
    """
    # Obtener legajo actual
    record = execute_query(
        get_record_detail_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )

    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    # Verificar permiso
    if not check_permission(record["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para actualizar este legajo")

    # Validar estado si viene
    if new_state is not None:
        allowed = record.get("states", [])
        if allowed and new_state not in allowed:
            raise ValidationError(f"Estado '{new_state}' no es válido. Estados permitidos: {allowed}")

    old_state = record["state"]
    old_display_name = record.get("display_name")

    # Obtener info del usuario antes de la transacción
    user_info = execute_query(
        get_user_sector_info_query(),
        (user_id,),
        schema_name=schema_name,
        fetch_one=True
    )

    # Construir query y params dinámicos
    update_state = new_state is not None
    update_display_name = new_display_name is not None
    query_sql = update_record_query(update_state=update_state, update_display_name=update_display_name)

    params = []
    if update_state:
        params.append(new_state)
    if update_display_name:
        params.append(new_display_name)
    params.append(record_id)

    # Actualizar + registrar historial en una sola transacción atómica
    with execute_transaction(schema_name=schema_name, user_id=user_id) as (conn, cursor):
        # 1. Actualizar registro
        cursor.execute(query_sql, tuple(params))
        result = cursor.fetchone()

        # 2. Registrar en historial (misma transacción)
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

        before_json = json.dumps(before_data)
        after_json = json.dumps(after_data)
        cursor.execute(
            insert_history_query(),
            (
                record_id,
                action,
                None,  # field_name
                before_json,
                after_json,
                user_id,
                user_info.get("sector_id") if user_info else None,
            )
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


# Nota: _generate_record_number() fue eliminado en Fase 2.1.
# La generación de número ahora está integrada dentro de create_record()
# usando execute_transaction() para garantizar atomicidad.


def autocomplete_records(
    user_id: str,
    query: str,
    *,
    schema_name: str,
    limit: int = 10,
) -> dict:
    """Autocomplete de legajos por número."""
    from services.rlm.permissions import _get_user_sector_ids

    sector_ids = _get_user_sector_ids(user_id, schema_name=schema_name)

    if not sector_ids:
        return {"records": [], "total": 0, "query": query}

    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    search_pattern = f"%{escaped}%"

    sql = autocomplete_records_query(sector_ids)
    params = list(sector_ids) + [search_pattern, search_pattern, limit]

    results = execute_query(sql, tuple(params), schema_name=schema_name)

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
