"""
Módulo de permisos de expedientes.
Contiene funciones para verificar y calcular permisos de usuarios sobre expedientes.
"""

from typing import Dict, Any, List, Optional

from database import execute_query
from shared.exceptions import BusinessLogicError, NotFoundError, ValidationError
from shared.logging import get_logger

logger = get_logger(__name__)


# Importar función centralizada desde sector_utils
from services.shared.sector_utils import get_user_sector_ids

# Mantener alias local para backward compatibility
_get_user_sector_ids = get_user_sector_ids


def get_user_editable_sector_ids(user_id: str, *, schema_name: str) -> List[str]:
    """
    Obtiene lista de sector_ids donde el usuario puede EDITAR (can_edit=true).

    - Sector principal: siempre puede editar
    - Sectores adicionales: solo si can_edit = true en user_sector_permissions

    Args:
        user_id: ID del usuario
        schema_name: Nombre del schema (multi-tenant)

    Returns:
        List[str]: Lista de sector_ids donde el usuario puede editar
    """
    from services.case_queries import get_user_sectors_with_permissions_query

    result = execute_query(
        get_user_sectors_with_permissions_query(),
        (user_id, user_id),
        schema_name=schema_name
    )

    return [str(row['sector_id']) for row in result if row['can_edit']]


def get_user_viewable_sector_ids(user_id: str, *, schema_name: str) -> List[str]:
    """
    Obtiene lista de sector_ids donde el usuario puede VER (can_view=true).

    - Sector principal: siempre puede ver
    - Sectores adicionales: solo si can_view = true en user_sector_permissions

    Args:
        user_id: ID del usuario
        schema_name: Nombre del schema (multi-tenant)

    Returns:
        List[str]: Lista de sector_ids donde el usuario puede ver
    """
    from services.case_queries import get_user_sectors_with_permissions_query

    result = execute_query(
        get_user_sectors_with_permissions_query(),
        (user_id, user_id),
        schema_name=schema_name
    )

    return [str(row['sector_id']) for row in result if row['can_view']]


def get_user_case_permissions(case_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Obtener permisos específicos del usuario sobre el expediente.

    Usa can_edit de user_sector_permissions para determinar permisos de edición.
    Verifica contra admin_sector (via case_movements) y sectores asignados.

    Args:
        case_id: ID del expediente
        user_id: ID del usuario
        schema_name: Nombre del schema (multi-tenant)

    Returns:
        Dict con permisos booleanos y nivel de ownership
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
        user_editable_sectors = get_user_editable_sector_ids(user_id, schema_name=schema_name)
        logger.debug(f"User {user_id} editable sectors: {user_editable_sectors}")

        # 2. Verificar is_admin e is_assigned con patron robusto de 3 condiciones
        if not user_editable_sectors:
            is_admin = False
            is_assigned = False
        else:
            sector_placeholders = ",".join(["%s"] * len(user_editable_sectors))

            permission_check = f"""
                SELECT
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = %s
                        AND cm.assigned_sector_id IN ({sector_placeholders})
                        AND cm.is_active = true
                    ) as is_assigned,
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = %s
                        AND cm.type = 'transfer'
                        AND cm.is_active = false
                        AND cm.admin_sector_id IN ({sector_placeholders})
                        AND cm.closed_at = (
                            SELECT MAX(cm2.closed_at)
                            FROM case_movements cm2
                            WHERE cm2.case_id = %s
                            AND cm2.type = 'transfer'
                            AND cm2.is_active = false
                        )
                    ) as is_admin_by_transfer,
                    (
                        EXISTS (
                            SELECT 1 FROM case_movements cm
                            WHERE cm.case_id = %s
                            AND cm.type = 'creation'
                            AND cm.admin_sector_id IN ({sector_placeholders})
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM case_movements cm
                            WHERE cm.case_id = %s
                            AND cm.type = 'transfer'
                        )
                    ) as is_admin_by_creation
            """
            params = (
                case_id, *user_editable_sectors,
                case_id, *user_editable_sectors, case_id,
                case_id, *user_editable_sectors, case_id,
            )
            perm_result = execute_query(permission_check, params, schema_name=schema_name)

            if perm_result:
                is_assigned = perm_result[0]['is_assigned']
                is_admin = perm_result[0]['is_admin_by_transfer'] or perm_result[0]['is_admin_by_creation']
            else:
                is_admin = False
                is_assigned = False

        # 3. Obtener información del caso
        case_result = execute_query(get_case_permissions_data_query(), (case_id,), schema_name=schema_name)
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


def can_user_view_case(case_id: str, user_id: str, *, schema_name: str) -> bool:
    """
    Verificar si un usuario puede ver un expediente.

    Condiciones (al menos una debe cumplirse):
    1. Usuario tiene flag can_global_search_cases=True
    2. Usuario es creador del expediente
    3. Sector del usuario es admin o esta asignado al expediente

    Optimizado: una sola query a la BD en vez de 3-4 separadas.

    Args:
        case_id: ID del expediente
        user_id: ID del usuario
        schema_name: Nombre del schema (multi-tenant)

    Returns:
        bool: True si el usuario puede ver el expediente
    """
    try:
        # Reusar la query existente de case_queries.py:249-273
        from services.case_queries import get_user_sectors_for_case_query
        USER_SECTORS_SUBQUERY = get_user_sectors_for_case_query()

        query = f"""
            SELECT EXISTS(
                -- Caso 1: Flag global + expediente existe
                SELECT 1 FROM cases c
                JOIN users u ON u.id = %s
                WHERE c.id = %s
                AND u.can_global_search_cases = true

                UNION ALL

                -- Caso 2: Es el creador
                SELECT 1 FROM cases c
                WHERE c.id = %s AND c.created_by_user_id = %s

                UNION ALL

                -- Caso 3a: Sector asignado activo
                SELECT 1 FROM case_movements cm
                JOIN ({USER_SECTORS_SUBQUERY}) user_sectors
                    ON cm.assigned_sector_id = user_sectors.sector_id
                WHERE cm.case_id = %s AND cm.is_active = true

                UNION ALL

                -- Caso 3b: Admin por ultima transferencia cerrada
                SELECT 1 FROM case_movements cm
                JOIN ({USER_SECTORS_SUBQUERY}) user_sectors
                    ON cm.admin_sector_id = user_sectors.sector_id
                WHERE cm.case_id = %s
                AND cm.type = 'transfer'
                AND cm.is_active = false
                AND cm.closed_at = (
                    SELECT MAX(cm2.closed_at)
                    FROM case_movements cm2
                    WHERE cm2.case_id = %s
                    AND cm2.type = 'transfer'
                    AND cm2.is_active = false
                )

                UNION ALL

                -- Caso 3c: Admin por creacion (solo si no hay transfers)
                SELECT 1 FROM case_movements cm
                JOIN ({USER_SECTORS_SUBQUERY}) user_sectors
                    ON cm.admin_sector_id = user_sectors.sector_id
                WHERE cm.case_id = %s
                AND cm.type = 'creation'
                AND NOT EXISTS (
                    SELECT 1 FROM case_movements cm2
                    WHERE cm2.case_id = %s
                    AND cm2.type = 'transfer'
                )
            ) as has_access
        """
        # Parametros: 15 placeholders
        # Caso 1: user_id, case_id (2)
        # Caso 2: case_id, user_id (2)
        # Caso 3a: user_id, user_id (subquery sectores), case_id (3)
        # Caso 3b: user_id, user_id (subquery sectores), case_id, case_id (4)
        # Caso 3c: user_id, user_id (subquery sectores), case_id, case_id (4)
        # Total: 2 + 2 + 3 + 4 + 4 = 15
        params = (
            user_id, case_id,                       # Caso 1: flag global
            case_id, user_id,                       # Caso 2: creador
            user_id, user_id, case_id,              # Caso 3a: sector asignado
            user_id, user_id, case_id, case_id,     # Caso 3b: admin transfer
            user_id, user_id, case_id, case_id,     # Caso 3c: admin creation
        )
        result = execute_query(query, params, schema_name=schema_name)
        return result and result[0].get('has_access', False)

    except Exception as e:
        logger.error(f"Error checking case view permissions for user {user_id[:8]} on case {case_id[:8]}: {str(e)}")
        return False


def calculate_access_reason(
    user_sector_ids: List[str],
    admin_sector_id: str,
    assigned_sector_ids: List[str]
) -> str:
    """
    Calcula la razón de acceso del usuario al expediente.

    Args:
        user_sector_ids: Lista de sector_ids del usuario
        admin_sector_id: ID del sector administrador del expediente
        assigned_sector_ids: Lista de sector_ids asignados al expediente

    Returns:
        str: Razón de acceso ('admin_sector', 'assigned', etc.)
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
