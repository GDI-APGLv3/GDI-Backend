"""Validaciones de dominio para Cases"""

from shared.logging import get_logger
from typing import Dict, Any
from shared.exceptions import ValidationError, NotFoundError

logger = get_logger(__name__)


async def validate_and_get_user(connection, user_id: str) -> Dict[str, Any]:
    """Valida que el usuario existe y retorna sus datos completos."""
    result = await connection.fetchrow(
        """
        SELECT
            u.id as user_id,
            u.full_name,
            u.sector_id,
            s.department_id
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1 AND u.estado = 1
        """,
        user_id
    )

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


async def validate_and_get_template(connection, template_id: str) -> Dict[str, Any]:
    """Valida que el template existe, está activo y retorna sus datos."""
    result = await connection.fetchrow(
        """
        SELECT
            ct.id,
            ct.filing_department_id,
            ct.type_name,
            ct.acronym,
            ct.is_active
        FROM case_templates ct
        WHERE ct.id = $1
        """,
        template_id
    )

    if not result:
        logger.info(f"Template {template_id[:8]}... no encontrado")
        raise ValidationError("Plantilla de expediente no encontrada. Verificá que el ID sea correcto.")

    if not result['is_active']:
        logger.info(f"Usando template INACTIVO: {result['type_name']} ({result['acronym']})")

    template_data = {
        'id': result['id'],
        'filing_department_id': str(result['filing_department_id']),
        'type_name': result['type_name'],
        'acronym': result['acronym']
    }

    logger.info(f"Template validado: {template_data['type_name']} ({template_data['acronym']})")
    return template_data


async def validate_department_permissions(
    connection,
    user_id: str,
    department_id: str
) -> None:
    """Valida que el usuario tenga permisos en el departamento especificado."""
    result = await connection.fetchrow(
        """
        SELECT 1
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1
        AND (
            s.department_id = $2
            OR EXISTS (
                SELECT 1 FROM user_departments
                WHERE user_id = u.id AND department_id = $3
            )
        )
        """,
        user_id, department_id, department_id
    )

    if not result:
        logger.info(f"Usuario {user_id[:8]}... no tiene permisos en departamento {department_id[:8]}...")
        raise ValidationError(
            f"No tiene permisos para crear expedientes en este departamento"
        )

    logger.info(f"Permisos de departamento validados")


async def validate_owner_sector_belongs_to_department(
    connection,
    owner_sector_id: str,
    filing_department_id: str
) -> None:
    """Valida que el sector propietario pertenezca al departamento correcto."""
    result = await connection.fetchrow(
        """
        SELECT id as sector_id, acronym as name, department_id
        FROM sectors
        WHERE id = $1 AND is_active = true
        """,
        owner_sector_id
    )

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
