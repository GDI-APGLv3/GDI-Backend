
from shared.logging import get_logger
from typing import Dict, Any, Optional
from database import get_conn, transaction
from services.users.queries import (
    get_user_by_id_query,
    get_updated_user_profile_query,
    validate_sector_exists_query
)
from config.constants import (
    PROFILE_NO_FIELDS_TO_UPDATE_ERROR,
    PROFILE_SECTOR_NOT_FOUND_ERROR,
    PROFILE_SECTOR_INACTIVE_ERROR,
    PROFILE_INVALID_FULL_NAME_ERROR,
    PROFILE_FULL_NAME_TOO_LONG_ERROR
)
from shared.exceptions import (
    UserNotFoundError,
    ValidationError,
    DatabaseError
)

logger = get_logger(__name__)


async def get_user_profile(user_id: str, *, schema_name: str) -> Dict[str, Any]:
    logger.info(f"[PROFILE] Buscando user_id={user_id}, schema_name={schema_name}")

    try:
        async with get_conn(schema_name=schema_name) as conn:
            current_path = await conn.fetchval("SHOW search_path")
            logger.info(f"[PROFILE] search_path actual: {current_path}")

            query = get_user_by_id_query()
            logger.info(f"[PROFILE] Ejecutando query con user_id={user_id}")
            user_row = await conn.fetchrow(query, user_id)

            logger.info(f"[PROFILE] Resultado encontrado: {user_row is not None}")

            if not user_row:
                logger.warning(f"[PROFILE] Usuario {user_id} NO encontrado en schema {schema_name}")
                raise UserNotFoundError(user_id)

            logger.info(f"[PROFILE] Usuario encontrado: user_id={user_id}")

            additional_sectors_rows = await conn.fetch(
                """
                SELECT
                    s.id as sector_id,
                    s.acronym as sector_acronym,
                    s.primary_color as sector_color,
                    d.acronym as department_acronym,
                    d.name as department_name,
                    usp.can_view,
                    usp.can_edit
                FROM user_sector_permissions usp
                JOIN sectors s ON usp.sector_id = s.id
                JOIN departments d ON s.department_id = d.id
                WHERE usp.user_id = $1 AND s.is_active = true
                ORDER BY d.acronym, s.acronym
                """,
                user_id
            )

            result = dict(user_row)
            result['additional_sectors'] = [
                {
                    'sector_id': str(row['sector_id']),
                    'sector_acronym': row['sector_acronym'],
                    'sector_color': row.get('sector_color'),
                    'department_acronym': row['department_acronym'],
                    'department_name': row['department_name'],
                    'can_view': row['can_view'],
                    'can_edit': row['can_edit']
                }
                for row in additional_sectors_rows
            ]

            logger.info(f"[PROFILE] Sectores adicionales: {len(result['additional_sectors'])}")
            return result

    except (UserNotFoundError, ValidationError):
        raise
    except Exception as e:
        logger.error(f"Error BD al obtener perfil de usuario {user_id}: {str(e)}", exc_info=True)
        raise DatabaseError(f"Error al obtener perfil de usuario {user_id}: {str(e)}")


async def update_user_profile(
    user_id: str,
    full_name: Optional[str] = None,
    country_id: Optional[str] = None,
    profile_picture_url: Optional[str] = None,
    sector_id: Optional[str] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    logger.info(f"Actualizando perfil de usuario {user_id}")

    if full_name is not None:
        full_name = full_name.strip()
        if not full_name:
            raise ValidationError(PROFILE_INVALID_FULL_NAME_ERROR)
        if len(full_name) > 100:
            raise ValidationError(PROFILE_FULL_NAME_TOO_LONG_ERROR.format(max_length=100))

    update_fields = []
    update_values = []

    if full_name is not None:
        update_fields.append("full_name")
        update_values.append(full_name)

    if country_id is not None:
        update_fields.append('"CountryID"')
        update_values.append(country_id)

    if profile_picture_url is not None:
        update_fields.append("profile_picture_url")
        update_values.append(profile_picture_url)

    if sector_id is not None:
        update_fields.append("sector_id")
        update_values.append(sector_id)

    if not update_fields:
        logger.warning(f"Intento de actualizar perfil {user_id} sin campos")
        raise ValidationError(PROFILE_NO_FIELDS_TO_UPDATE_ERROR)

    update_values.append(user_id)

    try:
        async with transaction(schema_name=schema_name, user_id=user_id, auth_source="profile_update") as conn:
            if sector_id is not None:
                sector = await conn.fetchrow(validate_sector_exists_query(), sector_id)

                if not sector:
                    logger.warning(f"Intento de asignar sector inexistente {sector_id}")
                    raise ValidationError(PROFILE_SECTOR_NOT_FOUND_ERROR.format(sector_id=sector_id))

                if not sector['is_active']:
                    logger.warning(f"Intento de asignar sector inactivo {sector_id}")
                    raise ValidationError(PROFILE_SECTOR_INACTIVE_ERROR.format(sector_id=sector_id))

            set_parts = [f"{col} = ${i+1}" for i, col in enumerate(update_fields)]
            where_param_idx = len(update_fields) + 1
            update_sql = (
                f"UPDATE users SET {', '.join(set_parts)} "
                f"WHERE id = ${where_param_idx} AND estado = 1 RETURNING id as user_id"
            )

            result_row = await conn.fetchrow(update_sql, *update_values)

            if not result_row:
                logger.warning(f"Usuario {user_id} no encontrado al intentar actualizar")
                raise UserNotFoundError(user_id)

            updated_user = await conn.fetchrow(get_updated_user_profile_query(), user_id)

            if not updated_user:
                logger.error(f"Usuario {user_id} no encontrado después de UPDATE exitoso")
                raise UserNotFoundError(user_id)

            logger.info(f"Perfil actualizado exitosamente para usuario {user_id}")
            return dict(updated_user)

    except (ValidationError, UserNotFoundError):
        raise
    except Exception as e:
        logger.error(f"Error BD al actualizar perfil {user_id}: {str(e)}", exc_info=True)
        raise DatabaseError(f"Error al actualizar perfil de usuario {user_id}: {str(e)}")
