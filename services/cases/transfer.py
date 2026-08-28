
from typing import List, Dict, Optional, Any
from shared.logging import get_logger
from database import fetch_all, execute
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


async def transfer_case(
    case_id: str,
    target_sector_id: str,
    reason: str,
    user_id: str,
    transfer_ownership: bool = True,
    assigned_user_id: Optional[str] = None,
    supporting_document_id: Optional[str] = None,
    *,
    schema_name: str,
    auth_source: str = "jwt"
) -> Dict[str, Any]:
    from services.case_service import CaseService
    from services.case_queries import (
        get_user_with_sector_query, get_case_with_target_sector_query,
        get_admin_sector_query, get_target_sector_query,
        get_assigned_user_query, update_case_ownership_query,
        close_movement_query
    )

    if not transfer_ownership:
        raise ValueError(
            "transfer_case ya no acepta transfer_ownership=False. "
            "Usá services.cases.tasks.ensure_assignment_and_create_task."
        )

    try:
        logger.info(f"Starting transfer for case {case_id} to sector {target_sector_id}, ownership={transfer_ownership}")

        user_result = await fetch_all(get_user_with_sector_query(), user_id, schema_name=schema_name)

        if not user_result:
            logger.error(f"User not found: {user_id}")
            raise NotFoundError(TRANSFER_USER_NOT_FOUND)

        user_data = user_result[0]
        user_sector_id = user_data['sector_id']

        user_editable_sectors = await CaseService.get_user_editable_sector_ids(user_id, schema_name=schema_name)
        logger.debug(f"User {user_id} editable sectors: {user_editable_sectors}")

        case_result = await fetch_all(
            get_case_with_target_sector_query(),
            target_sector_id, case_id,
            schema_name=schema_name
        )

        if not case_result:
            logger.error(f"Case or target sector not found: case={case_id}, target={target_sector_id}")
            raise NotFoundError(TRANSFER_CASE_NOT_FOUND)

        case_data = case_result[0]

        if transfer_ownership:
            admin_result = await fetch_all(get_admin_sector_query(), case_id, schema_name=schema_name)

            if not admin_result:
                logger.error(f"Admin sector not found for case: {case_id}")
                raise BusinessLogicError(TRANSFER_ADMIN_SECTOR_NOT_FOUND)

            admin_sector_id = str(admin_result[0]['admin_sector_id'])

            if admin_sector_id not in user_editable_sectors:
                logger.warning(f"Permission denied: admin sector {admin_sector_id} not in user editable sectors {user_editable_sectors}")
                raise AuthorizationError(TRANSFER_PERMISSION_DENIED)

        target_result = await fetch_all(get_target_sector_query(), target_sector_id, schema_name=schema_name)

        if not target_result:
            logger.error(f"Target sector not found or inactive: {target_sector_id}")
            raise ValidationError(TRANSFER_TARGET_SECTOR_NOT_FOUND)

        target_sector_data = target_result[0]

        assigned_user_name = None
        if assigned_user_id:
            assigned_result = await fetch_all(
                get_assigned_user_query(),
                assigned_user_id, target_sector_id,
                schema_name=schema_name
            )

            if not assigned_result:
                logger.error(f"Assigned user {assigned_user_id} not in target sector {target_sector_id}")
                raise ValidationError(TRANSFER_ASSIGNED_USER_INVALID)

            assigned_user_name = assigned_result[0]['full_name']

        movement_type = MOVEMENT_TYPES["TRANSFER"] if transfer_ownership else MOVEMENT_TYPES["ASSIGNMENT"]

        logger.info(f"Creating movement: type={movement_type}, user={user_id}, target_sector={target_sector_id}")
        movement_id = await CaseService.create_movement(
            case_id=case_id,
            movement_type=movement_type,
            user_id=user_id,
            creator_sector_id=str(user_sector_id),
            admin_sector_id=target_sector_id,
            assigned_sector_id=target_sector_id,
            assigned_user_id=assigned_user_id,
            reason=reason.strip(),
            supporting_document_id=supporting_document_id,
            schema_name=schema_name,
            auth_source=auth_source
        )

        if transfer_ownership:
            await execute(close_movement_query(), TRANSFER_CLOSING_REASON, user_id, movement_id, schema_name=schema_name)
            await execute(
                update_case_ownership_query(),
                target_sector_id, case_data['target_department_id'], case_id,
                schema_name=schema_name
            )
            logger.info(f"Transfer completed: movement closed and ownership updated")

            try:
                from services.cases.responsibles import (
                    add_responsible as _add_responsible,
                    _deactivate_all_admins,
                )
                await _deactivate_all_admins(
                    case_id=case_id,
                    removed_by=user_id,
                    reason=reason.strip(),
                    schema_name=schema_name,
                )
                if assigned_user_id:
                    await _add_responsible(
                        case_id=case_id,
                        user_id=assigned_user_id,
                        responsible_type="ADMIN",
                        sector_id=target_sector_id,
                        added_by=user_id,
                        movement_reason=reason.strip(),
                        schema_name=schema_name,
                    )
            except Exception as resp_err:
                logger.warning(f"Error gestionando responsable ADMIN en transferencia: {resp_err}")

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


async def close_assignment(
    case_id: str,
    movement_id: str,
    reason: str,
    user_id: str,
    *,
    schema_name: str,
    auth_source: str = "jwt"
) -> Dict[str, Any]:
    from services.case_service import CaseService
    from services.case_queries import (
        get_movement_for_closing_query,
        get_admin_sector_query, close_movement_query
    )

    try:
        logger.info(f"Closing assignment: case={case_id}, movement={movement_id}, user={user_id}")

        user_sector_ids = await CaseService.get_user_editable_sector_ids(user_id, schema_name=schema_name)
        logger.debug(f"User {user_id} editable sectors for close_assignment: {user_sector_ids}")

        if not user_sector_ids:
            logger.error(f"User {user_id} has no editable sectors")
            raise AuthorizationError(CLOSE_ASSIGNMENT_USER_NO_SECTORS)

        movement_result = await fetch_all(
            get_movement_for_closing_query(),
            movement_id, case_id,
            schema_name=schema_name
        )

        if not movement_result:
            logger.error(f"Movement not found: id={movement_id}, case={case_id}")
            raise NotFoundError(CLOSE_ASSIGNMENT_MOVEMENT_NOT_FOUND)

        movement_data = movement_result[0]

        if movement_data['type'] != MOVEMENT_TYPE_ASSIGNMENT:
            logger.error(f"Wrong movement type: {movement_data['type']} (expected assignment)")
            raise ValidationError(f"{CLOSE_ASSIGNMENT_WRONG_TYPE}. Este movimiento es tipo '{movement_data['type']}'")

        if movement_data['closed_at'] is not None:
            logger.error(f"Movement already closed at: {movement_data['closed_at']}")
            raise BusinessLogicError(f"{CLOSE_ASSIGNMENT_ALREADY_CLOSED} el {movement_data['closed_at']}")

        admin_result = await fetch_all(get_admin_sector_query(), case_id, schema_name=schema_name)

        if not admin_result:
            logger.error(f"Admin sector not found for case: {case_id}")
            raise BusinessLogicError(TRANSFER_ADMIN_SECTOR_NOT_FOUND)

        admin_sector_id = str(admin_result[0]['admin_sector_id'])
        assigned_sector_id = str(movement_data['assigned_sector_id']) if movement_data['assigned_sector_id'] else None

        has_permission = (
            admin_sector_id in user_sector_ids or
            (assigned_sector_id and assigned_sector_id in user_sector_ids)
        )

        if not has_permission:
            logger.warning(f"Permission denied: user sectors {user_sector_ids} not in [admin={admin_sector_id}, assigned={assigned_sector_id}]")
            raise AuthorizationError(CLOSE_ASSIGNMENT_PERMISSION_DENIED)

        from database import transaction as db_transaction
        from services.cases.tasks import _record_assignment_close
        async with db_transaction(schema_name=schema_name, user_id=user_id, auth_source=auth_source) as conn:
            await conn.execute(
                close_movement_query(),
                reason.strip(), user_id, movement_id,
            )
            await conn.execute(
                """
                UPDATE case_assignment_tasks
                SET status = 'closed',
                    closed_at = NOW(),
                    closed_by = $1,
                    closing_reason = $2
                WHERE assignment_id = $3
                  AND status = 'open'
                """,
                user_id,
                reason.strip(),
                movement_id,
            )
            await _record_assignment_close(
                conn,
                case_id=case_id,
                assignment_id=movement_id,
                assigned_sector_id=assigned_sector_id,
                user_id=user_id,
                reason=reason.strip(),
                schema_name=schema_name,
            )

        logger.info(f"Assignment closed successfully: movement_id={movement_id}")

        return {
            "movement_id": movement_id,
            "case_id": case_id,
            "movement_type": movement_data['type'],
            "closing_reason": reason.strip(),
            "had_supporting_document": movement_data['supporting_document_id'] is not None,
            "assigned_sector_id": assigned_sector_id,
            "admin_sector_id": admin_sector_id
        }

    except (NotFoundError, ValidationError, AuthorizationError, BusinessLogicError):
        raise
    except Exception as e:
        logger.error(f"Error in close_assignment: {str(e)}")
        raise BusinessLogicError(CLOSE_ASSIGNMENT_ERROR)


async def get_available_sectors_for_transfer(case_id: str, user_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    from services.case_service import CaseService
    from services.case_queries import get_available_sectors_for_transfer_query

    try:
        logger.info(f"Fetching available sectors for case {case_id}")

        if not await CaseService.can_user_view_case(case_id, user_id, schema_name=schema_name):
            logger.warning(f"User {user_id} cannot access case {case_id}")
            raise AuthorizationError(AVAILABLE_SECTORS_NO_ACCESS)

        sectors_result = await fetch_all(get_available_sectors_for_transfer_query(), case_id, case_id, schema_name=schema_name)

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


async def get_sector_users(sector_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    from services.case_queries import get_sector_users_query

    try:
        logger.info(f"Fetching users for sector {sector_id}")

        users_result = await fetch_all(get_sector_users_query(), sector_id, schema_name=schema_name)

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
