"""
Servicio para relaciones entre legajos.
"""

from shared.logging import get_logger
from database import execute_query, execute_update, execute_single_update
from shared.exceptions import NotFoundError, AuthorizationError, ValidationError
from services.rlm.queries import (
    get_record_detail_query,
    get_record_relations_query,
    get_record_relations_count_query,
    insert_relation_query,
    delete_relation_query,
    get_user_sector_info_query,
)
from services.rlm.permissions import check_permission
from services.rlm.history import record_action

logger = get_logger(__name__)


def get_relations(
    record_id: str,
    user_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Lista relaciones de un legajo con otros legajos con paginacion."""
    from services.rlm.permissions import verify_record_view_permission
    verify_record_view_permission(record_id, user_id, schema_name=schema_name)

    # Count
    count_result = execute_query(
        get_record_relations_count_query(),
        (record_id, record_id),
        schema_name=schema_name,
        fetch_one=True
    )
    total = count_result["total"] if count_result else 0

    # List
    offset = (page - 1) * page_size
    results = execute_query(
        get_record_relations_query(),
        (record_id, record_id, page_size, offset),
        schema_name=schema_name
    )

    relations = [
        {
            "relation_id": row["relation_id"],
            "relation_type": row["relation_type"],
            "notes": row["notes"],
            "created_at": str(row["created_at"]),
            "related_record": {
                "id": row["related_record_id"],
                "record_number": row["related_record_number"],
                "display_name": row["related_display_name"],
                "state": row["related_record_state"],
                "registry_code": row["related_registry_code"],
                "registry_name": row["related_registry_name"],
                "resume": row.get("related_resume", ""),
            },
        }
        for row in (results or [])
    ]

    total_pages = max(1, -(-total // page_size))

    return {
        "relations": relations,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def create_relation(
    record_id: str,
    target_record_id: str,
    relation_type: str,
    user_id: str,
    notes: str = None,
    *,
    schema_name: str
) -> dict:
    """Crea una relación entre dos legajos."""
    if record_id == target_record_id:
        raise ValidationError("No se puede crear una relación de un legajo consigo mismo")

    # Verificar que ambos legajos existen
    source = execute_query(
        get_record_detail_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    if not source:
        raise NotFoundError(f"Legajo origen con ID '{record_id}' no encontrado")

    target = execute_query(
        get_record_detail_query(),
        (target_record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    if not target:
        raise NotFoundError(f"Legajo destino con ID '{target_record_id}' no encontrado")

    # Verificar permisos
    if not check_permission(source["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para crear relaciones en este legajo")

    execute_update(
        insert_relation_query(),
        (record_id, target_record_id, relation_type, notes, user_id),
        schema_name=schema_name
    )

    user_info = execute_query(
        get_user_sector_info_query(),
        (user_id,),
        schema_name=schema_name,
        fetch_one=True
    )

    record_action(
        record_id=record_id,
        action="relation_created",
        user_id=user_id,
        sector_id=user_info.get("sector_id") if user_info else None,
        after_value={"target_record_id": target_record_id, "relation_type": relation_type},
        schema_name=schema_name
    )

    logger.info(f"Relation '{relation_type}' created: {record_id[:8]} -> {target_record_id[:8]}")
    return {
        "source_record_id": record_id,
        "target_record_id": target_record_id,
        "relation_type": relation_type,
    }


def delete_relation(record_id: str, relation_id: str, user_id: str, *, schema_name: str) -> dict:
    """Elimina una relación entre legajos."""
    source = execute_query(
        get_record_detail_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    if not source:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    if not check_permission(source["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para eliminar relaciones de este legajo")

    # Fetch relation info BEFORE deleting for history
    rel_info = execute_query(
        """SELECT rr.relation_type,
                  CASE WHEN rr.source_record_id = %s THEN r2.record_number ELSE r1.record_number END as related_number,
                  CASE WHEN rr.source_record_id = %s THEN r2.display_name ELSE r1.display_name END as related_name
           FROM record_relations rr
           JOIN records r1 ON rr.source_record_id = r1.id
           JOIN records r2 ON rr.target_record_id = r2.id
           WHERE rr.id = %s""",
        (record_id, record_id, relation_id),
        schema_name=schema_name,
        fetch_one=True
    )

    result = execute_single_update(
        delete_relation_query(),
        (relation_id, record_id, record_id),
        returning=True,
        schema_name=schema_name
    )

    if result["rows_affected"] == 0:
        raise NotFoundError(f"Relacion con ID '{relation_id}' no encontrada en legajo '{record_id}'")

    record_action(
        record_id=record_id,
        action="relation_deleted",
        user_id=user_id,
        after_value={
            "relation_id": relation_id,
            "record_number": rel_info.get("related_number", "") if rel_info else "",
            "display_name": rel_info.get("related_name", "") if rel_info else "",
            "relation_type": rel_info.get("relation_type", "") if rel_info else "",
        },
        schema_name=schema_name
    )

    logger.info(f"Relation {relation_id} removed from record {record_id[:8]}")
    return {"removed": True}
