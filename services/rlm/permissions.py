
from shared.logging import get_logger
from database import fetch_all, fetch_one
from shared.exceptions import NotFoundError, AuthorizationError
from services.rlm.queries import get_sector_permissions_query, get_all_permissions_for_sectors_query

logger = get_logger(__name__)


async def _get_user_sector_ids(user_id: str, *, schema_name: str) -> list:
    from services.case_queries import get_user_sectors_with_permissions_query

    result = await fetch_all(
        get_user_sectors_with_permissions_query(),
        user_id,
        schema_name=schema_name,
    )

    return [str(row['sector_id']) for row in (result or [])]


async def get_user_permissions(registry_family_id: str, user_id: str, *, schema_name: str) -> dict:
    default_perms = {
        "can_create": False,
        "can_edit": False,
        "can_view": False,
        "can_verify": False,
    }

    try:
        sector_ids = await _get_user_sector_ids(user_id, schema_name=schema_name)

        if not sector_ids:
            logger.debug(f"No sectors found for user {user_id[:8]}")
            return default_perms

        merged_perms = dict(default_perms)
        for sector_id in sector_ids:
            result = await fetch_one(
                get_sector_permissions_query(),
                registry_family_id,
                sector_id,
                schema_name=schema_name,
            )
            if result:
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


async def get_bulk_permissions(user_id: str, *, schema_name: str) -> dict:
    default_perms = {
        "can_create": False,
        "can_edit": False,
        "can_view": False,
        "can_verify": False,
    }

    try:
        sector_ids = await _get_user_sector_ids(user_id, schema_name=schema_name)

        if not sector_ids:
            logger.debug(f"No sectors found for user {user_id[:8]}")
            return {}

        query, params = get_all_permissions_for_sectors_query(sector_ids)
        results = await fetch_all(query, *params, schema_name=schema_name)

        if not results:
            return {}

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


async def check_permission(registry_family_id: str, user_id: str, permission: str, *, schema_name: str) -> bool:
    perms = await get_user_permissions(registry_family_id, user_id, schema_name=schema_name)
    return perms.get(permission, False)


async def verify_record_view_permission(record_id: str, user_id: str, *, schema_name: str) -> None:
    from services.rlm.queries import get_record_family_query

    record = await fetch_one(
        get_record_family_query(),
        record_id,
        schema_name=schema_name,
    )
    if not record:
        raise NotFoundError(f"Legajo '{record_id}' no encontrado")

    if not await check_permission(record["registry_family_id"], user_id, "can_view", schema_name=schema_name):
        raise AuthorizationError("No tiene permiso para ver este legajo")
