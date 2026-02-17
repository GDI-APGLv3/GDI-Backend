"""
Módulo de transferencia para expedientes.
Funciones para transferir y cerrar asignaciones de expedientes.
"""

from typing import List, Dict, Optional, Any
from shared.logging import get_logger
from database import execute_query, execute_update
from config.constants import (
    MOVEMENT_TYPES,
    TRANSFER_USER_NOT_FOUND,
    TRANSFER_CASE_NOT_FOUND,
    TRANSFER_PERMISSION_DENIED,
    TRANSFER_ADMIN_SECTOR_NOT_FOUND,
    TRANSFER_TARGET_SECTOR_NOT_FOUND,
    TRANSFER_ASSIGNED_USER_INVALID,
    TRANSFER_ERROR,
    TRANSFER_CLOSING_REASON,
    TRANSFER_DUPLICATE_ASSIGNMENT,
    CLOSE_ASSIGNMENT_USER_NO_SECTORS,
    CLOSE_ASSIGNMENT_MOVEMENT_NOT_FOUND,
    CLOSE_ASSIGNMENT_WRONG_TYPE,
    CLOSE_ASSIGNMENT_ALREADY_CLOSED,
    CLOSE_ASSIGNMENT_PERMISSION_DENIED,
    CLOSE_ASSIGNMENT_ERROR,
    MOVEMENT_TYPE_ASSIGNMENT,
    AVAILABLE_SECTORS_ERROR,
    AVAILABLE_SECTORS_NO_ACCESS,
    SECTOR_USERS_ERROR
)
from shared.exceptions import (
    NotFoundError,
    ValidationError,
    AuthorizationError,
    BusinessLogicError
)

logger = get_logger(__name__)


def transfer_case(
    case_id: str,
    target_sector_id: str,
    reason: str,
    user_id: str,
    transfer_ownership: bool = True,
    assigned_user_id: Optional[str] = None,
    supporting_document_id: Optional[str] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """
    Transferir expediente a otro sector o asignar tarea.

    Args:
        case_id: ID del expediente
        target_sector_id: ID del sector destino
        reason: Motivo de la transferencia/asignación
        user_id: ID del usuario que realiza la operación
        transfer_ownership: Si True transfiere propiedad, si False solo asigna tarea
        assigned_user_id: ID del usuario específico asignado (opcional)
        supporting_document_id: ID del documento oficial de soporte (PV creado automáticamente)
        schema_name: Nombre del schema (opcional, para multi-tenant)

    Returns:
        Dict con información del movimiento creado
    """
    from services.case_service import CaseService
    from services.case_queries import (
        get_user_with_sector_query, get_case_with_target_sector_query,
        get_admin_sector_query, get_target_sector_query,
        get_assigned_user_query, update_case_ownership_query,
        close_movement_query, check_duplicate_assignment_query
    )

    try:
        logger.info(f"Starting transfer for case {case_id} to sector {target_sector_id}, ownership={transfer_ownership}")

        # 1. Obtener datos del usuario con su sector
        user_result = execute_query(get_user_with_sector_query(), (user_id,), schema_name=schema_name)

        if not user_result:
            logger.error(f"User not found: {user_id}")
            raise NotFoundError(TRANSFER_USER_NOT_FOUND)

        user_data = user_result[0]
        user_sector_id = user_data['sector_id']

        # 1.1 Obtener TODOS los sectores donde el usuario puede EDITAR
        user_editable_sectors = CaseService.get_user_editable_sector_ids(user_id, schema_name=schema_name)
        logger.debug(f"User {user_id} editable sectors: {user_editable_sectors}")

        # 2. Obtener datos del expediente con sector destino
        case_result = execute_query(
            get_case_with_target_sector_query(),
            (target_sector_id, case_id),
            schema_name=schema_name
        )

        if not case_result:
            logger.error(f"Case or target sector not found: case={case_id}, target={target_sector_id}")
            raise NotFoundError(TRANSFER_CASE_NOT_FOUND)

        case_data = case_result[0]

        # 3. Si es transferencia de propiedad, verificar permisos del sector administrador
        if transfer_ownership:
            admin_result = execute_query(get_admin_sector_query(), (case_id,), schema_name=schema_name)

            if not admin_result:
                logger.error(f"Admin sector not found for case: {case_id}")
                raise BusinessLogicError(TRANSFER_ADMIN_SECTOR_NOT_FOUND)

            admin_sector_id = str(admin_result[0]['admin_sector_id'])

            # Validar que el usuario tenga permiso de EDICION en el sector admin del expediente
            if admin_sector_id not in user_editable_sectors:
                logger.warning(f"Permission denied: admin sector {admin_sector_id} not in user editable sectors {user_editable_sectors}")
                raise AuthorizationError(TRANSFER_PERMISSION_DENIED)

        # 4. Verificar que el sector destino existe y está activo
        target_result = execute_query(get_target_sector_query(), (target_sector_id,), schema_name=schema_name)

        if not target_result:
            logger.error(f"Target sector not found or inactive: {target_sector_id}")
            raise ValidationError(TRANSFER_TARGET_SECTOR_NOT_FOUND)

        target_sector_data = target_result[0]

        # 5. Verificar usuario asignado si se proporciona
        assigned_user_name = None
        if assigned_user_id:
            assigned_result = execute_query(
                get_assigned_user_query(),
                (assigned_user_id, target_sector_id),
                schema_name=schema_name
            )

            if not assigned_result:
                logger.error(f"Assigned user {assigned_user_id} not in target sector {target_sector_id}")
                raise ValidationError(TRANSFER_ASSIGNED_USER_INVALID)

            assigned_user_name = assigned_result[0]['full_name']

        # 6. Determinar tipo de movimiento
        movement_type = MOVEMENT_TYPES["TRANSFER"] if transfer_ownership else MOVEMENT_TYPES["ASSIGNMENT"]

        # 6.5. Si es asignación, verificar que no exista una asignación activa al mismo sector
        if not transfer_ownership:
            duplicate_check = execute_query(
                check_duplicate_assignment_query(),
                (case_id, target_sector_id),
                schema_name=schema_name
            )
            if duplicate_check:
                raise ValidationError(TRANSFER_DUPLICATE_ASSIGNMENT)

        # 7. Crear el movimiento
        logger.info(f"Creating movement: type={movement_type}, user={user_id}, target_sector={target_sector_id}")
        movement_id = CaseService.create_movement(
            case_id=case_id,
            movement_type=movement_type,
            user_id=user_id,
            creator_sector_id=str(user_sector_id),
            admin_sector_id=target_sector_id,
            assigned_sector_id=target_sector_id,
            assigned_user_id=assigned_user_id,
            reason=reason.strip(),
            supporting_document_id=supporting_document_id,
            schema_name=schema_name
        )

        # 8. Si es transferencia de propiedad, cerrar movimiento y actualizar expediente
        if transfer_ownership:
            execute_update(close_movement_query(), (TRANSFER_CLOSING_REASON, movement_id), schema_name=schema_name)
            execute_update(
                update_case_ownership_query(),
                (target_sector_id, case_data['target_department_id'], case_id),
                schema_name=schema_name
            )
            logger.info(f"Transfer completed: movement closed and ownership updated")

        action_type = "transferido" if transfer_ownership else "asignado"

        logger.info(f"Case {action_type} successfully: movement_id={movement_id}")

        return {
            "movement_id": movement_id,
            "case_number": case_data['case_number'],
            "action_type": action_type,
            "target_sector": target_sector_data['acronym'],
            "target_department": target_sector_data['department_name'],
            "transferred_by": user_data['full_name'],
            "assigned_user": assigned_user_name
        }

    except (NotFoundError, ValidationError, AuthorizationError, BusinessLogicError):
        raise
    except Exception as e:
        logger.error(f"Error in transfer_case: {str(e)}")
        raise BusinessLogicError(TRANSFER_ERROR)


def close_assignment(
    case_id: str,
    movement_id: str,
    reason: str,
    user_id: str,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """
    Cerrar asignación de expediente.

    Args:
        case_id: ID del expediente
        movement_id: ID del movimiento a cerrar
        reason: Razón del cierre
        user_id: ID del usuario que cierra
        schema_name: Nombre del schema (opcional, para multi-tenant)

    Returns:
        Dict con información del movimiento cerrado
    """
    from services.case_service import CaseService
    from services.case_queries import (
        get_movement_for_closing_query,
        get_admin_sector_query, close_movement_query
    )

    try:
        logger.info(f"Closing assignment: case={case_id}, movement={movement_id}, user={user_id}")

        # 1. Obtener sectores donde el usuario puede EDITAR (respeta can_edit)
        user_sector_ids = CaseService.get_user_editable_sector_ids(user_id, schema_name=schema_name)
        logger.debug(f"User {user_id} editable sectors for close_assignment: {user_sector_ids}")

        if not user_sector_ids:
            logger.error(f"User {user_id} has no editable sectors")
            raise AuthorizationError(CLOSE_ASSIGNMENT_USER_NO_SECTORS)

        # 2. Verificar que el movimiento existe y pertenece al expediente
        movement_result = execute_query(
            get_movement_for_closing_query(),
            (movement_id, case_id),
            schema_name=schema_name
        )

        if not movement_result:
            logger.error(f"Movement not found: id={movement_id}, case={case_id}")
            raise NotFoundError(CLOSE_ASSIGNMENT_MOVEMENT_NOT_FOUND)

        movement_data = movement_result[0]

        # 3. Verificar que sea tipo assignment
        if movement_data['type'] != MOVEMENT_TYPE_ASSIGNMENT:
            logger.error(f"Wrong movement type: {movement_data['type']} (expected assignment)")
            raise ValidationError(f"{CLOSE_ASSIGNMENT_WRONG_TYPE}. Este movimiento es tipo '{movement_data['type']}'")

        # 4. Verificar que no esté cerrado
        if movement_data['closed_at'] is not None:
            logger.error(f"Movement already closed at: {movement_data['closed_at']}")
            raise BusinessLogicError(f"{CLOSE_ASSIGNMENT_ALREADY_CLOSED} el {movement_data['closed_at']}")

        # 5. Obtener sector administrador
        admin_result = execute_query(get_admin_sector_query(), (case_id,), schema_name=schema_name)

        if not admin_result:
            logger.error(f"Admin sector not found for case: {case_id}")
            raise BusinessLogicError(TRANSFER_ADMIN_SECTOR_NOT_FOUND)

        admin_sector_id = str(admin_result[0]['admin_sector_id'])
        assigned_sector_id = str(movement_data['assigned_sector_id']) if movement_data['assigned_sector_id'] else None

        # 6. Verificar permisos: usuario debe pertenecer al sector admin O al sector asignado
        has_permission = (
            admin_sector_id in user_sector_ids or
            (assigned_sector_id and assigned_sector_id in user_sector_ids)
        )

        if not has_permission:
            logger.warning(f"Permission denied: user sectors {user_sector_ids} not in [admin={admin_sector_id}, assigned={assigned_sector_id}]")
            raise AuthorizationError(CLOSE_ASSIGNMENT_PERMISSION_DENIED)

        # 7. Cerrar el movimiento
        execute_update(close_movement_query(), (reason.strip(), user_id, movement_id), schema_name=schema_name)

        logger.info(f"Assignment closed successfully: movement_id={movement_id}")

        # 8. Registrar cierre en historial del expediente
        try:
            import uuid as uuid_mod
            from config.constants import MOVEMENT_TYPE_ASSIGNMENT_CLOSE

            user_sector_result = execute_query(
                "SELECT sector_id FROM users WHERE id = %s",
                (user_id,), schema_name=schema_name
            )
            closer_sector_id = str(user_sector_result[0]['sector_id']) if user_sector_result else assigned_sector_id or admin_sector_id

            close_history_id = str(uuid_mod.uuid4())
            execute_update("""
                INSERT INTO case_movements (
                    id, case_id, type, user_id,
                    creator_sector_id, admin_sector_id,
                    assigned_sector_id, reason,
                    is_active, closed_at, closing_reason
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    false, NOW(), %s
                )
            """, (
                close_history_id, case_id, MOVEMENT_TYPE_ASSIGNMENT_CLOSE, user_id,
                closer_sector_id, admin_sector_id,
                assigned_sector_id, reason.strip(),
                reason.strip()
            ), schema_name=schema_name)
            logger.info(f"Assignment close history recorded for case {case_id}")
        except Exception as hist_error:
            logger.warning(f"Error recording close history for case {case_id}: {hist_error}")

        return {
            "movement_id": movement_id,
            "case_id": case_id,
            "movement_type": movement_data['type'],
            "closing_reason": reason.strip(),
            "had_supporting_document": movement_data.get('supporting_document_id') is not None,
            "assigned_sector_id": assigned_sector_id,
            "admin_sector_id": admin_sector_id
        }

    except (NotFoundError, ValidationError, AuthorizationError, BusinessLogicError):
        raise
    except Exception as e:
        logger.error(f"Error in close_assignment: {str(e)}")
        raise BusinessLogicError(CLOSE_ASSIGNMENT_ERROR)


def get_available_sectors_for_transfer(case_id: str, user_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    """
    Obtener sectores disponibles para transferencia (mismo municipio).

    Args:
        case_id: ID del expediente
        user_id: ID del usuario (para validar acceso)
        schema_name: Nombre del schema (opcional, para multi-tenant)

    Returns:
        Lista de sectores con información completa
    """
    from services.case_service import CaseService
    from services.case_queries import get_available_sectors_for_transfer_query

    try:
        logger.info(f"Fetching available sectors for case {case_id}")

        # Verificar acceso al expediente
        if not CaseService.can_user_view_case(case_id, user_id, schema_name=schema_name):
            logger.warning(f"User {user_id} cannot access case {case_id}")
            raise AuthorizationError(AVAILABLE_SECTORS_NO_ACCESS)

        # Obtener sectores disponibles
        sectors_result = execute_query(get_available_sectors_for_transfer_query(), (case_id,), schema_name=schema_name)

        sectors = []
        for row in sectors_result:
            sectors.append({
                "sector_id": row['sector_id'],
                "sector_name": row['sector_acronym'],
                "department_name": row['department_name'],
                "department_acronym": row['department_acronym'],
                "user_count": row['user_count'],
                "display_name": f"{row['sector_acronym']} - {row['department_name']}"
            })

        logger.info(f"Found {len(sectors)} available sectors for transfer")

        return sectors

    except AuthorizationError:
        raise
    except Exception as e:
        logger.error(f"Error getting available sectors: {str(e)}")
        raise BusinessLogicError(AVAILABLE_SECTORS_ERROR)


def get_sector_users(sector_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    """
    Obtener usuarios activos de un sector.

    Args:
        sector_id: ID del sector
        schema_name: Nombre del schema (opcional, para multi-tenant)

    Returns:
        Lista de usuarios con user_id y full_name
    """
    from services.case_queries import get_sector_users_query

    try:
        logger.info(f"Fetching users for sector {sector_id}")

        users_result = execute_query(get_sector_users_query(), (sector_id,), schema_name=schema_name)

        users = [
            {
                "user_id": row['user_id'],
                "full_name": row['full_name']
            }
            for row in users_result
        ]

        logger.info(f"Found {len(users)} users in sector {sector_id}")

        return users

    except Exception as e:
        logger.error(f"Error getting sector users: {str(e)}")
        raise BusinessLogicError(SECTOR_USERS_ERROR)
