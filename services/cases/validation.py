
from shared.logging import get_logger
from typing import Dict, Any
from shared.exceptions import ValidationError, NotFoundError

logger = get_logger(__name__)


async def validate_and_get_user(connection, user_id: str) -> Dict[str, Any]:
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
    result = await connection.fetchrow(
        """
        SELECT
            ct.id,
            ct.filing_department_id,
            ct.filing_sector_id,
            ct.creation_channel,
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
        'filing_sector_id': str(result['filing_sector_id']),
        'creation_channel': result['creation_channel'],
        'type_name': result['type_name'],
        'acronym': result['acronym'],
        'is_active': result['is_active'],
    }

    logger.info(f"Template validado: {template_data['type_name']} ({template_data['acronym']})")
    return template_data


async def validate_and_get_citizen(connection, citizen_id: str) -> Dict[str, Any]:
    result = await connection.fetchrow(
        "SELECT id, full_name, estado FROM citizens WHERE id = $1",
        citizen_id
    )

    if not result:
        logger.info(f"Ciudadano {citizen_id[:8]}... no encontrado")
        raise NotFoundError(f"Ciudadano {citizen_id} no encontrado en el sistema")

    citizen_data = {
        'citizen_id': str(result['id']),
        'full_name': result['full_name'],
        'estado': result['estado'],
    }

    logger.info(f"Ciudadano validado: {citizen_data['full_name']}")
    return citizen_data


def validate_creation_channel_for_citizen(template_data: Dict[str, Any]) -> None:
    channel = template_data.get('creation_channel')
    if channel not in ('api', 'both'):
        raise ValidationError(
            f"El tipo de expediente '{template_data.get('acronym')}' no admite creacion "
            f"por Trámites a Distancia (creation_channel='{channel}', requiere 'api' o 'both')."
        )


async def validate_department_permissions(
    connection,
    user_id: str,
    department_id: str
) -> None:
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
