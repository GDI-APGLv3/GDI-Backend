"""Validaciones de dominio para Cases"""

from shared.logging import get_logger
from typing import Dict, Any
from shared.exceptions import ValidationError, NotFoundError
from services.case_queries import (
    get_user_validation_query,
    get_template_validation_query,
    get_department_permissions_query,
    get_sector_validation_query
)

logger = get_logger(__name__)


def validate_and_get_user(connection, user_id: str) -> Dict[str, Any]:
    """Valida que el usuario existe y retorna sus datos completos."""
    cursor = connection.cursor()
    cursor.execute(get_user_validation_query(), (user_id,))
    result = cursor.fetchone()

    if not result:
        logger.info(f"Usuario {user_id[:8]}... no encontrado")
        raise NotFoundError(f"Usuario {user_id} no encontrado en el sistema")

    user_data = {
        'user_id': str(result['user_id']),
        'full_name': result['full_name'],
        'sector_id': result['sector_id'],
        'department_id': result['department_id']
    }

    logger.info(f"Usuario validado: {user_data['full_name']}")
    return user_data


def validate_and_get_template(connection, template_id: str) -> Dict[str, Any]:
    """Valida que el template existe, está activo y retorna sus datos."""
    cursor = connection.cursor()
    cursor.execute(get_template_validation_query(), (template_id,))
    result = cursor.fetchone()

    if not result:
        logger.info(f"Template {template_id[:8]}... no encontrado")
        raise NotFoundError(f"Plantilla de expediente {template_id} no encontrada")

    if not result.get('is_active'):
        logger.info(f"⚠️ Usando template INACTIVO: {result['type_name']} ({result['acronym']})")

    template_data = {
        'id': result['id'],
        'filing_department_id': str(result['filing_department_id']),
        'type_name': result['type_name'],
        'acronym': result['acronym']
    }

    logger.info(f"Template validado: {template_data['type_name']} ({template_data['acronym']})")
    return template_data


def validate_department_permissions(
    connection,
    user_id: str,
    department_id: str
) -> None:
    """Valida que el usuario tenga permisos en el departamento especificado."""
    cursor = connection.cursor()
    cursor.execute(get_department_permissions_query(), (user_id, department_id, department_id))
    result = cursor.fetchone()

    if not result:
        logger.info(f"Usuario {user_id[:8]}... no tiene permisos en departamento {department_id[:8]}...")
        raise ValidationError(
            f"No tiene permisos para crear expedientes en este departamento"
        )

    logger.info(f"Permisos de departamento validados")


def validate_owner_sector_belongs_to_department(
    connection,
    owner_sector_id: str,
    filing_department_id: str
) -> None:
    """Valida que el sector propietario pertenezca al departamento correcto."""
    cursor = connection.cursor()
    cursor.execute(get_sector_validation_query(), (owner_sector_id,))
    result = cursor.fetchone()

    if not result:
        logger.info(f"Sector {owner_sector_id[:8]}... no encontrado o inactivo")
        raise NotFoundError(f"Sector {owner_sector_id} no encontrado o inactivo")

    sector_department_id = str(result['department_id'])

    if sector_department_id != filing_department_id:
        logger.info(f"Sector pertenece a departamento incorrecto")
        raise ValidationError(
            "El sector propietario debe pertenecer al mismo departamento que la plantilla"
        )

    logger.info(f"Sector propietario validado")
