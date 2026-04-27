"""
Módulo de historial de expedientes.
Contiene funciones para obtener y crear movimientos en expedientes.
"""

import uuid
from typing import Dict, Any, List, Optional

from database import execute_query, execute_update
from shared.exceptions import BusinessLogicError, NotFoundError, ValidationError
from shared.logging import get_logger

logger = get_logger(__name__)


def get_case_movements(case_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    """
    Obtiene el historial de movimientos de un expediente.

    Args:
        case_id: ID del expediente
        schema_name: Nombre del schema (multi-tenant)

    Returns:
        Lista de movimientos con información de usuarios y sectores
    """
    from services.case_queries import get_case_movements_query
    from config.constants import MOVEMENTS_ERROR

    try:
        logger.info(f"Fetching movements for case: {case_id}")

        movements_result = execute_query(get_case_movements_query(), (case_id,), schema_name=schema_name)

        if not movements_result:
            logger.info(f"No movements found for case: {case_id}")
            return []

        movements = []
        for row in movements_result:
            # Parsear full_name en name y lastname
            user_info = None
            if row['user_id']:
                full_name = row['user_full_name'] or ""
                name_parts = full_name.split(' ', 1)
                user_info = {
                    "id": str(row['user_id']),
                    "name": name_parts[0] if len(name_parts) > 0 else "",
                    "lastname": name_parts[1] if len(name_parts) > 1 else "",
                    "email": row['user_email'],
                    "profile_picture_url": row['user_profile_picture_url']
                }

            movement = {
                "id": str(row['id']),
                "type": row['movement_type'],
                "reason": row['reason'],
                "created_at": row['created_at'].isoformat() if row['created_at'] else None,
                "is_active": row['is_active'],
                "closed_at": row['closed_at'].isoformat() if row['closed_at'] else None,
                "closing_reason": row['closing_reason'],
                "user": user_info,
                "creator_sector": {
                    "id": str(row['creator_sector_id']),
                    "name": row['creator_sector_name']
                },
                "admin_sector": {
                    "id": str(row['admin_sector_id']),
                    "name": row['admin_sector_name']
                },
                "assigned_sector": {
                    "id": str(row['assigned_sector_id']),
                    "name": row['assigned_sector_name']
                } if row['assigned_sector_id'] else None,
                "supporting_document_id": str(row['supporting_document_id']) if row.get('supporting_document_id') else None,
                "supporting_document_number": row.get('supporting_document_number'),
                "supporting_document_reference": row.get('supporting_document_reference'),
                "supporting_document_resume": row.get('supporting_document_resume')
            }
            movements.append(movement)

        logger.info(f"Found {len(movements)} movements for case: {case_id}")
        return movements

    except Exception as e:
        logger.error(f"Error fetching case movements: {str(e)}")
        raise BusinessLogicError(MOVEMENTS_ERROR)


def create_movement(
    case_id: str,
    movement_type: str,
    user_id: str,
    creator_sector_id: str,
    admin_sector_id: str,
    reason: str,
    assigned_sector_id: Optional[str] = None,
    assigned_user_id: Optional[str] = None,
    supporting_document_id: Optional[str] = None,
    *,
    schema_name: str,
    auth_source: str = "jwt"
) -> str:
    """
    Crear nuevo movimiento en el expediente.

    Args:
        case_id: ID del expediente
        movement_type: Tipo de movimiento (creation, transfer, assignment, etc.)
        user_id: ID del usuario que realiza el movimiento
        creator_sector_id: ID del sector creador
        admin_sector_id: ID del sector administrador
        reason: Razón del movimiento
        assigned_sector_id: ID del sector asignado (opcional)
        assigned_user_id: ID del usuario asignado (opcional)
        supporting_document_id: ID del documento de soporte (opcional)
        schema_name: Nombre del schema (multi-tenant)

    Returns:
        ID del movimiento creado
    """
    try:
        movement_id = str(uuid.uuid4())

        movement_insert = """
            INSERT INTO case_movements (
                id, case_id, type, user_id,
                creator_sector_id, admin_sector_id,
                assigned_sector_id, assigned_user_id,
                reason, is_active, supporting_document_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """

        execute_update(movement_insert, (
            movement_id, case_id, movement_type, user_id,
            creator_sector_id, admin_sector_id,
            assigned_sector_id or None,
            assigned_user_id or None,
            reason, True,
            supporting_document_id or None
        ), schema_name=schema_name, user_id=user_id, auth_source=auth_source)

        logger.info(f"Movement created successfully: {movement_id}")
        return movement_id

    except Exception as e:
        logger.error(f"Error creating movement: {str(e)}")
        raise BusinessLogicError(f"Error creando movimiento: {str(e)}")


def get_case_history(case_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Obtener historial completo del expediente con mensajes formateados.
    Reutiliza get_case_movements() para obtener datos estructurados.

    Args:
        case_id: ID del expediente
        schema_name: Nombre del schema (multi-tenant)

    Returns:
        Dict con case_number y lista de movimientos formateados con mensajes legibles
    """
    from services.case_queries import get_case_number_query
    from config.constants import (
        CASE_HISTORY_ERROR, CASE_NOT_FOUND_ERROR,
        MOVEMENT_TYPE_CREATION, MOVEMENT_TYPE_TRANSFER,
        MOVEMENT_TYPE_ASSIGNMENT, MOVEMENT_TYPE_STATUS_CHANGE,
        MOVEMENT_TYPE_DOCUMENT_LINK, MOVEMENT_TYPE_SUBSANACION,
        MOVEMENT_TYPE_DOCUMENT_PROPOSAL, MOVEMENT_TYPE_DOCUMENT_PROPOSAL_REJECT,
        MOVEMENT_TYPE_ASSIGNMENT_CLOSE
    )

    try:
        logger.info(f"Fetching history for case: {case_id}")

        # Obtener número del expediente
        case_result = execute_query(get_case_number_query(), (case_id,), schema_name=schema_name)

        if not case_result:
            logger.error(f"Case not found: {case_id}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        case_number = case_result[0]['case_number']
        ai_summary = case_result[0].get('ai_summary')
        ai_summary_updated_at = case_result[0].get('ai_summary_updated_at')
        short_ai_summary = case_result[0].get('short_ai_summary')

        # Obtener movimientos estructurados
        movements = get_case_movements(case_id, schema_name=schema_name)

        # Calcular qué propuestas ya fueron resueltas (aceptadas → document_link,
        # o rechazadas → document_proposal_reject). Si una propuesta sobre un
        # supporting_document_id ya tiene resolución, la ocultamos del timeline.
        resolved_proposal_doc_ids = {
            m.get('supporting_document_id')
            for m in movements
            if m.get('type') in (MOVEMENT_TYPE_DOCUMENT_LINK, MOVEMENT_TYPE_DOCUMENT_PROPOSAL_REJECT)
            and m.get('supporting_document_id')
        }

        # Formatear cada movimiento con mensaje legible
        formatted_movements = []
        for mov in movements:
            # Saltar propuestas ya resueltas
            if (
                mov.get('type') == MOVEMENT_TYPE_DOCUMENT_PROPOSAL
                and mov.get('supporting_document_id') in resolved_proposal_doc_ids
            ):
                continue

            # Extraer datos del usuario
            user_data = mov.get('user')
            if user_data and isinstance(user_data, dict):
                user_name = f"{user_data.get('name', '')} {user_data.get('lastname', '')}".strip() or "Usuario desconocido"
            else:
                user_name = "Usuario desconocido"

            movement_type = mov.get('type', '')
            reason = mov.get('reason', '')

            # Extraer sector del creador
            creator_sector_data = mov.get('creator_sector')
            creator_sector_name = creator_sector_data.get('name', '') if isinstance(creator_sector_data, dict) else ''

            # Extraer sector asignado
            assigned_sector_data = mov.get('assigned_sector')
            assigned_sector_name = assigned_sector_data.get('name', '') if isinstance(assigned_sector_data, dict) else ''

            # Variable para almacenar el resume del documento
            document_resume = None

            # Construir mensaje según tipo de movimiento
            if movement_type == MOVEMENT_TYPE_CREATION:
                message = "Creó el expediente"
            elif movement_type in [MOVEMENT_TYPE_TRANSFER, MOVEMENT_TYPE_ASSIGNMENT]:
                action_verb = "Transfirió" if movement_type == MOVEMENT_TYPE_TRANSFER else "Asignó"
                message = f"{action_verb} el expediente a {assigned_sector_name}"
                # Resume ya se muestra en el movimiento document_link asociado
            elif movement_type == MOVEMENT_TYPE_STATUS_CHANGE:
                message = "Cambió el estado del expediente"
            elif movement_type == MOVEMENT_TYPE_DOCUMENT_LINK:
                message = ""
                document_resume = mov.get('supporting_document_resume')
            elif movement_type == MOVEMENT_TYPE_SUBSANACION:
                message = ""
            elif movement_type == MOVEMENT_TYPE_ASSIGNMENT_CLOSE:
                message = f"Cerró la asignación a {assigned_sector_name}"
            elif movement_type in [MOVEMENT_TYPE_DOCUMENT_PROPOSAL, MOVEMENT_TYPE_DOCUMENT_PROPOSAL_REJECT]:
                message = ""
            else:
                message = f"Realizó acción: {movement_type}"

            # Agregar reason al mensaje
            if reason:
                if message:
                    message += f" - {reason}"
                else:
                    message = reason[0].upper() + reason[1:] if len(reason) > 1 else reason.upper()

            # Construir objeto de documento de soporte si existe
            supporting_document = None
            supporting_doc_number = mov.get('supporting_document_number')
            if supporting_doc_number:
                supporting_document = {
                    "official_number": supporting_doc_number,
                    "reference": mov.get('supporting_document_reference', '')
                }

            formatted_movements.append({
                "user": {
                    "name": user_name,
                    "sector_department": creator_sector_name,
                    "photo_url": (user_data.get('profile_picture_url') or '') if user_data else ''
                },
                "created_at": mov.get('created_at'),
                "message": message,
                "type": movement_type,
                "is_active": mov.get('is_active', False),
                "closed_at": mov.get('closed_at'),
                "closing_reason": mov.get('closing_reason'),
                "resume": document_resume,
                "supporting_document": supporting_document
            })

        logger.info(f"Found {len(formatted_movements)} movements in history for case {case_id}")

        return {
            "case_number": case_number,
            "ai_summary": ai_summary,
            "ai_summary_updated_at": ai_summary_updated_at.isoformat() if ai_summary_updated_at else None,
            "short_ai_summary": short_ai_summary,
            "movements": formatted_movements
        }

    except (NotFoundError, ValidationError, BusinessLogicError):
        raise
    except Exception as e:
        logger.error(f"Error fetching case history: {str(e)}")
        raise BusinessLogicError(CASE_HISTORY_ERROR)
