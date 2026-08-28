
from typing import Dict, Any, List, Optional
import asyncio

from database import fetch_all
from shared.exceptions import BusinessLogicError, NotFoundError, ValidationError, reraise_if_transient
from shared.logging import get_logger

logger = get_logger(__name__)


from services.shared.sector_utils import get_user_sector_ids

_get_user_sector_ids = get_user_sector_ids


async def get_user_editable_sector_ids(user_id: str, *, schema_name: str) -> List[str]:
    from services.case_queries import get_user_sectors_with_permissions_query

    result = await fetch_all(
        get_user_sectors_with_permissions_query(),
        user_id,
        schema_name=schema_name
    )

    return [str(row['sector_id']) for row in result if row['can_edit']]


async def get_user_viewable_sector_ids(user_id: str, *, schema_name: str, conn=None) -> List[str]:
    from services.case_queries import get_user_sectors_with_permissions_query

    query = get_user_sectors_with_permissions_query()
    if conn is not None:
        result = await conn.fetch(query, user_id)
    else:
        result = await fetch_all(query, user_id, schema_name=schema_name)

    return [str(row['sector_id']) for row in result if row['can_view']]


async def get_user_case_permissions(case_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    from services.case_queries import get_case_permissions_data_query
    from config.constants import (
        PERMISSIONS_ERROR, CASE_NOT_FOUND_ERROR,
        OWNERSHIP_LEVEL_OWNER, OWNERSHIP_LEVEL_CREATOR,
        OWNERSHIP_LEVEL_PARTICIPANT,
        CASE_STATUS_ACTIVE
    )

    try:
        logger.info(f"Calculating permissions for user {user_id} on case {case_id}")

        user_editable_sectors = await get_user_editable_sector_ids(user_id, schema_name=schema_name)
        logger.debug(f"User {user_id} editable sectors: {user_editable_sectors}")

        if not user_editable_sectors:
            is_admin = False
            is_assigned = False
            case_result = await fetch_all(get_case_permissions_data_query(), case_id, schema_name=schema_name)
        else:
            permission_check = """
                SELECT
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = $1
                        AND cm.assigned_sector_id = ANY($2::uuid[])
                        AND cm.is_active = true
                    ) as is_assigned,
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = $3
                        AND cm.type = 'transfer'
                        AND cm.is_active = false
                        AND cm.admin_sector_id = ANY($4::uuid[])
                        AND cm.closed_at = (
                            SELECT MAX(cm2.closed_at)
                            FROM case_movements cm2
                            WHERE cm2.case_id = $5
                            AND cm2.type = 'transfer'
                            AND cm2.is_active = false
                        )
                    ) as is_admin_by_transfer,
                    (
                        EXISTS (
                            SELECT 1 FROM case_movements cm
                            WHERE cm.case_id = $6
                            AND cm.type = 'creation'
                            AND cm.admin_sector_id = ANY($7::uuid[])
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM case_movements cm
                            WHERE cm.case_id = $8
                            AND cm.type = 'transfer'
                        )
                    ) as is_admin_by_creation
            """
            perm_result, case_result = await asyncio.gather(
                fetch_all(
                    permission_check,
                    case_id, user_editable_sectors,
                    case_id, user_editable_sectors, case_id,
                    case_id, user_editable_sectors, case_id,
                    schema_name=schema_name
                ),
                fetch_all(get_case_permissions_data_query(), case_id, schema_name=schema_name),
            )

            if perm_result:
                is_assigned = perm_result[0]['is_assigned']
                is_admin = perm_result[0]['is_admin_by_transfer'] or perm_result[0]['is_admin_by_creation']
            else:
                is_admin = False
                is_assigned = False

        if not case_result:
            logger.error(f"Case not found: {case_id}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        case_data = case_result[0]
        is_active = case_data['status'] == CASE_STATUS_ACTIVE
        is_creator = str(case_data['created_by_user_id']) == str(user_id)

        if is_admin:
            ownership_level = OWNERSHIP_LEVEL_OWNER
        elif is_assigned:
            ownership_level = OWNERSHIP_LEVEL_PARTICIPANT
        elif is_creator:
            ownership_level = OWNERSHIP_LEVEL_CREATOR
        else:
            ownership_level = OWNERSHIP_LEVEL_PARTICIPANT

        can_edit_in_case = is_admin or is_assigned

        permissions = {
            "can_view": True,
            "can_transfer": is_admin and is_active,
            "can_assign": can_edit_in_case and is_active,
            "can_archive": is_admin and is_active,
            "can_link_documents": can_edit_in_case and is_active,
            "can_create_movements": can_edit_in_case and is_active,
            "can_subsanar": is_admin and is_active,
            "ownership_level": ownership_level
        }

        logger.info(f"Permissions calculated for user {user_id} on case {case_id}: is_admin={is_admin}, is_assigned={is_assigned}, ownership={ownership_level}")

        return permissions

    except (NotFoundError, ValidationError, BusinessLogicError):
        raise
    except Exception as e:
        logger.error(f"Error calculating permissions: {str(e)}")
        raise BusinessLogicError(PERMISSIONS_ERROR)


async def can_user_view_case(case_id: str, user_id: str, *, schema_name: str, conn=None) -> bool:
    from asyncpg.exceptions import UndefinedColumnError

    try:
        from services.case_queries import get_user_sectors_for_case_query
        USER_SECTORS_SUBQUERY = get_user_sectors_for_case_query()

        query = f"""
            WITH user_sectors AS MATERIALIZED (
                {USER_SECTORS_SUBQUERY}
            )
            SELECT EXISTS(
                -- Caso 1: Flag global + expediente existe — SOLO si NO reservado
                SELECT 1 FROM cases c
                JOIN case_templates ct ON ct.id = c.case_template_id
                JOIN users u ON u.id = $1
                WHERE c.id = $2 AND ct.is_reserved = false
                AND u.can_global_search_cases = true

                UNION ALL

                -- Caso 2: Es el creador — SOLO si NO reservado
                SELECT 1 FROM cases c
                JOIN case_templates ct ON ct.id = c.case_template_id
                WHERE c.id = $3 AND ct.is_reserved = false AND c.created_by_user_id = $4

                UNION ALL

                -- Caso 3a: Sector asignado activo — SOLO si NO reservado
                SELECT 1 FROM cases c
                JOIN case_templates ct ON ct.id = c.case_template_id
                JOIN case_movements cm ON cm.case_id = c.id
                JOIN user_sectors
                    ON cm.assigned_sector_id = user_sectors.sector_id
                WHERE c.id = $5 AND ct.is_reserved = false AND cm.is_active = true

                UNION ALL

                -- Caso 3b: Admin por ultima transferencia cerrada — SOLO si NO reservado
                SELECT 1 FROM cases c
                JOIN case_templates ct ON ct.id = c.case_template_id
                JOIN case_movements cm ON cm.case_id = c.id
                JOIN user_sectors
                    ON cm.admin_sector_id = user_sectors.sector_id
                WHERE c.id = $6 AND ct.is_reserved = false
                AND cm.type = 'transfer'
                AND cm.is_active = false
                AND cm.closed_at = (
                    SELECT MAX(cm2.closed_at)
                    FROM case_movements cm2
                    WHERE cm2.case_id = c.id
                    AND cm2.type = 'transfer'
                    AND cm2.is_active = false
                )

                UNION ALL

                -- Caso 3c: Admin por creacion (solo si no hay transfers) — SOLO si NO reservado
                SELECT 1 FROM cases c
                JOIN case_templates ct ON ct.id = c.case_template_id
                JOIN case_movements cm ON cm.case_id = c.id
                JOIN user_sectors
                    ON cm.admin_sector_id = user_sectors.sector_id
                WHERE c.id = $7 AND ct.is_reserved = false
                AND cm.type = 'creation'
                AND NOT EXISTS (
                    SELECT 1 FROM case_movements cm2
                    WHERE cm2.case_id = c.id
                    AND cm2.type = 'transfer'
                )

                UNION ALL

                -- Caso R1 (NUEVO): responsable por persona en case_responsibles
                SELECT 1 FROM cases c
                JOIN case_templates ct ON ct.id = c.case_template_id
                JOIN case_responsibles cr ON cr.case_id = c.id
                WHERE c.id = $8 AND ct.is_reserved = true
                AND cr.user_id = $9 AND cr.is_active = true

                UNION ALL

                -- Caso R2 (NUEVO): titular directo del departamento del sector administrador actual
                SELECT 1 FROM cases c
                JOIN case_templates ct ON ct.id = c.case_template_id
                JOIN case_movements cm ON cm.case_id = c.id
                JOIN sectors s ON s.id = cm.admin_sector_id
                JOIN departments d ON d.id = s.department_id
                WHERE c.id = $10 AND ct.is_reserved = true
                AND cm.is_active = false AND cm.type IN ('creation', 'transfer')
                AND cm.closed_at = (
                    SELECT MAX(cm2.closed_at) FROM case_movements cm2
                    WHERE cm2.case_id = c.id AND cm2.type IN ('creation', 'transfer') AND cm2.is_active = false
                )
                AND d.head_user_id = $11

                UNION ALL

                -- Caso R3 (NUEVO): titular directo del departamento de cada sector asignado activo
                SELECT 1 FROM cases c
                JOIN case_templates ct ON ct.id = c.case_template_id
                JOIN case_movements cm ON cm.case_id = c.id
                JOIN sectors s ON s.id = cm.assigned_sector_id
                JOIN departments d ON d.id = s.department_id
                WHERE c.id = $12 AND ct.is_reserved = true
                AND cm.is_active = true AND cm.assigned_sector_id IS NOT NULL
                AND d.head_user_id = $13

                UNION ALL

                -- Caso R4 (NUEVO, GDI-069 fix 07/07): actuante con tarea de
                -- asignacion ABIERTA en el expediente (case_assignment_tasks)
                SELECT 1 FROM cases c
                JOIN case_templates ct ON ct.id = c.case_template_id
                JOIN case_assignment_tasks cat ON cat.case_id = c.id
                WHERE c.id = $14 AND ct.is_reserved = true
                AND cat.assigned_user_id = $15 AND cat.status = 'open'
            ) as has_access
        """
        query_params = (
            user_id,
            case_id,
            case_id,
            user_id,
            case_id,
            case_id,
            case_id,
            case_id,
            user_id,
            case_id,
            user_id,
            case_id,
            user_id,
            case_id,
            user_id,
        )
        if conn is not None:
            result = await conn.fetch(query, *query_params)
        else:
            result = await fetch_all(query, *query_params, schema_name=schema_name)
        return result and result[0].get('has_access', False)

    except UndefinedColumnError as e:
        logger.error(
            f"Columna faltante evaluando permisos de expediente {case_id[:8]} "
            f"(user {user_id[:8]}): {str(e)}. Verificar migracion 082."
        )
        raise
    except Exception as e:
        reraise_if_transient(e, context=f"permisos de vista del expediente {case_id[:8]}")
        logger.error(f"Error checking case view permissions for user {user_id[:8]} on case {case_id[:8]}: {str(e)}")
        return False


async def can_user_edit_case(case_id: str, user_id: str, *, schema_name: str) -> bool:
    try:
        perms = await get_user_case_permissions(case_id, user_id, schema_name=schema_name)
        return perms.get("can_link_documents", False)
    except NotFoundError:
        return False
    except Exception as e:
        reraise_if_transient(e, context=f"permisos de edicion del expediente {case_id[:8]}")
        logger.error(f"Error checking edit permissions for user {user_id[:8]} on case {case_id[:8]}: {str(e)}")
        return False


def calculate_access_reason(
    user_sector_ids: List[str],
    admin_sector_id: str,
    assigned_sector_ids: List[str]
) -> str:
    from config.constants import ACCESS_REASON_ADMIN, ACCESS_REASON_ASSIGNED

    if admin_sector_id in user_sector_ids:
        return ACCESS_REASON_ADMIN
    for sector_id in assigned_sector_ids:
        if sector_id in user_sector_ids:
            return ACCESS_REASON_ASSIGNED
    return ACCESS_REASON_ASSIGNED


_calculate_access_reason = calculate_access_reason
