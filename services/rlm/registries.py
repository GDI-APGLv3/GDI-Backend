"""
Servicio para operaciones sobre registros (registry_families).
"""

from shared.logging import get_logger
from database import fetch_all, fetch_one
from shared.exceptions import NotFoundError
from services.rlm.queries import (
    get_registries_query,
    get_registry_detail_query,
    get_registry_by_code_query,
)
from services.rlm.permissions import get_user_permissions, get_bulk_permissions

logger = get_logger(__name__)


async def list_registries(user_id: str, *, schema_name: str) -> dict:
    """
    Lista todos los registros disponibles con conteo de legajos.

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Returns:
        Dict con lista de registros
    """
    try:
        results = await fetch_all(
            get_registries_query(),
            schema_name=schema_name,
        )

        # Bulk: obtener permisos de TODAS las familias en 2 queries (no N+1)
        default_perms = {
            "can_create": False,
            "can_edit": False,
            "can_view": False,
            "can_verify": False,
        }
        permissions_map = await get_bulk_permissions(user_id, schema_name=schema_name)

        registries = []
        for row in (results or []):
            family_id = str(row["id"])
            perms = permissions_map.get(family_id, default_perms)
            registries.append({
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "description": row["description"],
                "allowed_states": row["states"],
                "is_active": row["is_active"],
                "record_count": row["record_count"],
                "permissions": perms,
            })

        logger.info(f"Listed {len(registries)} registries for user {user_id[:8]}")
        return {"registries": registries, "total": len(registries)}

    except Exception as e:
        logger.error(f"Error listing registries: {e}")
        raise


async def get_registry_detail(registry_id: str, user_id: str, *, schema_name: str) -> dict:
    """
    Obtiene el detalle de un registro incluyendo su data_schema.

    Args:
        registry_id: UUID del registro
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Returns:
        Dict con detalle del registro

    Raises:
        NotFoundError: Si el registro no existe
    """
    try:
        result = await fetch_one(
            get_registry_detail_query(),
            registry_id,
            schema_name=schema_name,
        )

        if not result:
            raise NotFoundError(f"Registro con ID '{registry_id}' no encontrado")

        perms = await get_user_permissions(registry_id, user_id, schema_name=schema_name)

        return {
            "id": result["id"],
            "code": result["code"],
            "name": result["name"],
            "description": result["description"],
            "data_schema": result["data_schema"],
            "allowed_states": result["states"],
            "record_count": result["record_count"],
            "permissions": perms,
        }

    except NotFoundError:
        raise
    except Exception as e:
        logger.error(f"Error getting registry detail: {e}")
        raise


async def get_registry_by_code(code: str, *, schema_name: str) -> dict:
    """
    Obtiene un registro por su código.

    Args:
        code: Código del registro (ARQ, LUM, ORD)
        schema_name: Schema del tenant

    Returns:
        Dict con datos del registro

    Raises:
        NotFoundError: Si el registro no existe
    """
    result = await fetch_one(
        get_registry_by_code_query(),
        code.upper(),
        schema_name=schema_name,
    )

    if not result:
        raise NotFoundError(f"Registro con código '{code}' no encontrado")

    return dict(result)
