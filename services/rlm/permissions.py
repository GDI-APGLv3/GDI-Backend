"""
Verificación de permisos para el módulo RLM.

Busca permisos en el sector principal del usuario Y en sus sectores adicionales
(tabla user_sector_permissions), replicando la lógica de expedientes.
"""

from shared.logging import get_logger
from database import execute_query
from shared.exceptions import NotFoundError, AuthorizationError
from services.rlm.queries import get_sector_permissions_query, get_all_permissions_for_sectors_query

logger = get_logger(__name__)


def _get_user_sector_ids(user_id: str, *, schema_name: str) -> list:
    """
    Obtiene todos los sector_ids del usuario: principal + adicionales.

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Returns:
        Lista de sector_ids (strings)
    """
    from services.case_queries import get_user_sectors_with_permissions_query

    result = execute_query(
        get_user_sectors_with_permissions_query(),
        (user_id, user_id),
        schema_name=schema_name
    )

    return [str(row['sector_id']) for row in (result or [])]


def get_user_permissions(registry_family_id: str, user_id: str, *, schema_name: str) -> dict:
    """
    Obtiene los permisos de un usuario sobre un registro.

    Busca permisos en TODOS los sectores del usuario (principal + adicionales).
    Si CUALQUIERA de los sectores tiene un permiso, se considera autorizado.

    Args:
        registry_family_id: UUID del registro
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Returns:
        Dict con permisos: can_create, can_edit, can_view, can_verify
    """
    default_perms = {
        "can_create": False,
        "can_edit": False,
        "can_view": False,
        "can_verify": False,
    }

    try:
        # Obtener todos los sectores del usuario
        sector_ids = _get_user_sector_ids(user_id, schema_name=schema_name)

        if not sector_ids:
            logger.debug(f"No sectors found for user {user_id[:8]}")
            return default_perms

        # Buscar permisos de cada sector sobre este registro
        merged_perms = dict(default_perms)
        for sector_id in sector_ids:
            result = execute_query(
                get_sector_permissions_query(),
                (registry_family_id, sector_id),
                schema_name=schema_name,
                fetch_one=True
            )
            if result:
                # OR: si CUALQUIER sector tiene el permiso, se activa
                if result.get("can_create"):
                    merged_perms["can_create"] = True
                if result.get("can_edit"):
                    merged_perms["can_edit"] = True
                if result.get("can_view"):
                    merged_perms["can_view"] = True
                if result.get("can_verify"):
                    merged_perms["can_verify"] = True

        if all(not v for v in merged_perms.values()):
            logger.debug(f"No permissions found for user {user_id[:8]} on registry {registry_family_id[:8]}")

        return merged_perms

    except Exception as e:
        logger.error(f"Error getting permissions: {e}")
        raise


def get_bulk_permissions(user_id: str, *, schema_name: str) -> dict:
    """
    Obtiene los permisos del usuario sobre TODAS las registry_families en 2 queries.

    Query 1: obtener sector_ids del usuario (reutiliza _get_user_sector_ids)
    Query 2: obtener TODOS los permisos de esos sectores en un solo SELECT

    Luego agrupa por registry_family_id con lógica OR (si cualquier sector
    tiene el permiso, se autoriza).

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Returns:
        Dict {registry_family_id: {can_create, can_edit, can_view, can_verify}}
    """
    default_perms = {
        "can_create": False,
        "can_edit": False,
        "can_view": False,
        "can_verify": False,
    }

    try:
        # Query 1: obtener todos los sectores del usuario
        sector_ids = _get_user_sector_ids(user_id, schema_name=schema_name)

        if not sector_ids:
            logger.debug(f"No sectors found for user {user_id[:8]}")
            return {}

        # Query 2: obtener TODOS los permisos de esos sectores sobre TODAS las familias
        query, params = get_all_permissions_for_sectors_query(sector_ids)
        results = execute_query(query, params, schema_name=schema_name)

        if not results:
            return {}

        # Agrupar por registry_family_id con lógica OR
        permissions_map: dict = {}
        for row in results:
            family_id = str(row["registry_family_id"])
            if family_id not in permissions_map:
                permissions_map[family_id] = dict(default_perms)

            perms = permissions_map[family_id]
            if row.get("can_create"):
                perms["can_create"] = True
            if row.get("can_edit"):
                perms["can_edit"] = True
            if row.get("can_view"):
                perms["can_view"] = True
            if row.get("can_verify"):
                perms["can_verify"] = True

        return permissions_map

    except Exception as e:
        logger.error(f"Error getting bulk permissions: {e}")
        raise


def check_permission(registry_family_id: str, user_id: str, permission: str, *, schema_name: str) -> bool:
    """
    Verifica si un usuario tiene un permiso específico.

    Busca en sector principal + sectores adicionales del usuario.

    Args:
        registry_family_id: UUID del registro
        user_id: UUID del usuario
        permission: Nombre del permiso (can_create, can_edit, etc.)
        schema_name: Schema del tenant

    Returns:
        True si tiene el permiso
    """
    perms = get_user_permissions(registry_family_id, user_id, schema_name=schema_name)
    return perms.get(permission, False)


def verify_record_view_permission(record_id: str, user_id: str, *, schema_name: str) -> None:
    """
    Verifica can_view sobre un record. Raise AuthorizationError si no tiene.

    Args:
        record_id: UUID del legajo
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Raises:
        NotFoundError: Si el legajo no existe
        AuthorizationError: Si no tiene permiso can_view
    """
    from services.rlm.queries import get_record_family_query

    record = execute_query(
        get_record_family_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    if not record:
        raise NotFoundError(f"Legajo '{record_id}' no encontrado")

    if not check_permission(record["registry_family_id"], user_id, "can_view", schema_name=schema_name):
        raise AuthorizationError("No tiene permiso para ver este legajo")
