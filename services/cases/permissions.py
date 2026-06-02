"""
Módulo de permisos de expedientes.
Contiene funciones para verificar y calcular permisos de usuarios sobre expedientes.
"""

from typing import Dict, Any, List, Optional

from database import fetch_all
from shared.exceptions import BusinessLogicError, NotFoundError, ValidationError
from shared.logging import get_logger

logger = get_logger(__name__)


# Importar función centralizada desde sector_utils
from services.shared.sector_utils import get_user_sector_ids

# Mantener alias local para backward compatibility
_get_user_sector_ids = get_user_sector_ids


async def get_user_editable_sector_ids(user_id: str, *, schema_name: str) -> List[str]:
    """
    Obtiene lista de sector_ids donde el usuario puede EDITAR (can_edit=true).

    - Sector principal: siempre puede editar
    - Sectores adicionales: solo si can_edit = true en user_sector_permissions
    """
    from services.case_queries import get_user_sectors_with_permissions_query

    result = await fetch_all(
        get_user_sectors_with_permissions_query(),
        user_id,
        schema_name=schema_name
    )

    return [str(row['sector_id']) for row in result if row['can_edit']]


async def get_user_viewable_sector_ids(user_id: str, *, schema_name: str) -> List[str]:
    """
    Obtiene lista de sector_ids donde el usuario puede VER (can_view=true).

    - Sector principal: siempre puede ver
    - Sectores adicionales: solo si can_view = true en user_sector_permissions
    """
    from services.case_queries import get_user_sectors_with_permissions_query

    result = await fetch_all(
        get_user_sectors_with_permissions_query(),
        user_id,
        schema_name=schema_name
    )

    return [str(row['sector_id']) for row in result if row['can_view']]


async def get_user_case_permissions(case_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Obtener permisos específicos del usuario sobre el expediente.
    """
    from services.case_queries import get_case_permissions_data_query
    from config.constants import (
        PERMISSIONS_ERROR, CASE_NOT_FOUND_ERROR,
        OWNERSHIP_LEVEL_OWNER, OWNERSHIP_LEVEL_CREATOR,
        OWNERSHIP_LEVEL_PARTICIPANT,
        CASE_STATUS_ACTIVE
    )

    try:
        logger.info(f"Calculating permissions for user {user_id} on case {case_id}")

        # 1. Obtener sectores donde el usuario puede EDITAR
        user_editable_sectors = await get_user_editable_sector_ids(user_id, schema_name=schema_name)
        logger.debug(f"User {user_id} editable sectors: {user_editable_sectors}")

        # 2. Verificar is_admin e is_assigned con patron robusto de 3 condiciones
        if not user_editable_sectors:
            is_admin = False
            is_assigned = False
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
            perm_result = await fetch_all(
                permission_check,
                case_id, user_editable_sectors,
                case_id, user_editable_sectors, case_id,
                case_id, user_editable_sectors, case_id,
                schema_name=schema_name
            )

            if perm_result:
                is_assigned = perm_result[0]['is_assigned']
                is_admin = perm_result[0]['is_admin_by_transfer'] or perm_result[0]['is_admin_by_creation']
            else:
                is_admin = False
                is_assigned = False

        # 3. Obtener información del caso
        case_result = await fetch_all(get_case_permissions_data_query(), case_id, schema_name=schema_name)
        if not case_result:
            logger.error(f"Case not found: {case_id}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        case_data = case_result[0]
        is_active = case_data['status'] == CASE_STATUS_ACTIVE
        is_creator = str(case_data['created_by_user_id']) == str(user_id)

        # 6. Determinar nivel de ownership
        if is_admin:
            ownership_level = OWNERSHIP_LEVEL_OWNER
        elif is_assigned:
            ownership_level = OWNERSHIP_LEVEL_PARTICIPANT
        elif is_creator:
            ownership_level = OWNERSHIP_LEVEL_CREATOR
        else:
            ownership_level = OWNERSHIP_LEVEL_PARTICIPANT

        # 7. Calcular permisos específicos
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


async def can_user_view_case(case_id: str, user_id: str, *, schema_name: str) -> bool:
    """
    Verificar si un usuario puede ver un expediente.
    """
    try:
        from services.case_queries import get_user_sectors_for_case_query
        USER_SECTORS_SUBQUERY = get_user_sectors_for_case_query()

        query = f"""
            SELECT EXISTS(
                -- Caso 1: Flag global + expediente existe
                SELECT 1 FROM cases c
                JOIN users u ON u.id = $1
                WHERE c.id = $2
                AND u.can_global_search_cases = true

                UNION ALL

                -- Caso 2: Es el creador
                SELECT 1 FROM cases c
                WHERE c.id = $3 AND c.created_by_user_id = $4

                UNION ALL

                -- Caso 3a: Sector asignado activo
                SELECT 1 FROM case_movements cm
                JOIN ({USER_SECTORS_SUBQUERY}) user_sectors
                    ON cm.assigned_sector_id = user_sectors.sector_id
                WHERE cm.case_id = $5 AND cm.is_active = true

                UNION ALL

                -- Caso 3b: Admin por ultima transferencia cerrada
                SELECT 1 FROM case_movements cm
                JOIN ({USER_SECTORS_SUBQUERY}) user_sectors
                    ON cm.admin_sector_id = user_sectors.sector_id
                WHERE cm.case_id = $6
                AND cm.type = 'transfer'
                AND cm.is_active = false
                AND cm.closed_at = (
                    SELECT MAX(cm2.closed_at)
                    FROM case_movements cm2
                    WHERE cm2.case_id = $7
                    AND cm2.type = 'transfer'
                    AND cm2.is_active = false
                )

                UNION ALL

                -- Caso 3c: Admin por creacion (solo si no hay transfers)
                SELECT 1 FROM case_movements cm
                JOIN ({USER_SECTORS_SUBQUERY}) user_sectors
                    ON cm.admin_sector_id = user_sectors.sector_id
                WHERE cm.case_id = $8
                AND cm.type = 'creation'
                AND NOT EXISTS (
                    SELECT 1 FROM case_movements cm2
                    WHERE cm2.case_id = $9
                    AND cm2.type = 'transfer'
                )
            ) as has_access
        """
        # asyncpg: $N son índices únicos. La query tiene $1..$9:
        # $1=user_id, $2=case_id(c1), $3=case_id(c2), $4=user_id(c2),
        # $5=case_id(3a), $6=case_id(3b), $7=case_id(3b-max),
        # $8=case_id(3c), $9=case_id(3c-notexists)
        # Los subqueries de sectores referencian $1 (user_id outer)
        result = await fetch_all(
            query,
            user_id,    # $1 — user_id (Caso 1 + subquery sectores)
            case_id,    # $2 — case_id (Caso 1)
            case_id,    # $3 — case_id (Caso 2)
            user_id,    # $4 — user_id (Caso 2 created_by)
            case_id,    # $5 — case_id (Caso 3a)
            case_id,    # $6 — case_id (Caso 3b)
            case_id,    # $7 — case_id (Caso 3b MAX)
            case_id,    # $8 — case_id (Caso 3c)
            case_id,    # $9 — case_id (Caso 3c NOT EXISTS)
            schema_name=schema_name
        )
        return result and result[0].get('has_access', False)

    except Exception as e:
        logger.error(f"Error checking case view permissions for user {user_id[:8]} on case {case_id[:8]}: {str(e)}")
        return False


async def can_user_edit_case(case_id: str, user_id: str, *, schema_name: str) -> bool:
    """
    Verifica si un usuario puede editar un expediente.
    """
    try:
        perms = await get_user_case_permissions(case_id, user_id, schema_name=schema_name)
        return perms.get("can_link_documents", False)
    except NotFoundError:
        return False
    except Exception as e:
        logger.error(f"Error checking edit permissions for user {user_id[:8]} on case {case_id[:8]}: {str(e)}")
        return False


def calculate_access_reason(
    user_sector_ids: List[str],
    admin_sector_id: str,
    assigned_sector_ids: List[str]
) -> str:
    """
    Calcula la razón de acceso del usuario al expediente.
    """
    from config.constants import ACCESS_REASON_ADMIN, ACCESS_REASON_ASSIGNED

    if admin_sector_id in user_sector_ids:
        return ACCESS_REASON_ADMIN
    for sector_id in assigned_sector_ids:
        if sector_id in user_sector_ids:
            return ACCESS_REASON_ASSIGNED
    return ACCESS_REASON_ASSIGNED


# Alias para backward compatibility
_calculate_access_reason = calculate_access_reason
