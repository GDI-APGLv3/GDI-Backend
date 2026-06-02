"""
Servicios para la gestión de usuarios que contienen la lógica de negocio.
Estos servicios implementan las consultas a la base de datos y la manipulación de datos de usuarios.
"""

import asyncpg
from typing import List, Dict, Any, Optional
from database import fetch_all, fetch_one, fetch_val, execute
from datetime import datetime
from shared.logging import get_logger

logger = get_logger(__name__)

async def create_user(auth_id: str, full_name: str, email: str,
                country_id: Optional[str] = None, profile_picture_url: Optional[str] = None,
                sector_id: Optional[str] = None, *, schema_name: str) -> Dict[str, Any]:
    """
    Crea un nuevo usuario usando la función stored procedure de PostgreSQL.

    Args:
        auth_id: ID de Auth0 del usuario
        full_name: Nombre completo del usuario
        email: Email del usuario
        country_id: Identificador nacional del usuario (columna CountryID en BD). En Argentina es el CUIT (opcional)
        profile_picture_url: URL de la foto de perfil (opcional)
        sector_id: UUID del sector (opcional)

    Returns:
        Diccionario con la información del usuario creado o error
    """
    try:
        user_data = await fetch_one(
            """
            SELECT * FROM create_user(
                p_auth_id := $1,
                p_full_name := $2,
                p_email := $3,
                p_cuit := $4,
                p_profile_picture_url := $5,
                p_sector_id := $6
            )
            """,
            auth_id, full_name, email, country_id, profile_picture_url, sector_id,
            schema_name=schema_name
        )

        if user_data:
            return {
                "success": True,
                "message": "Usuario creado exitosamente",
                "user": dict(user_data)
            }
        else:
            return {
                "success": False,
                "message": "Error al crear usuario: No se retornaron datos"
            }

    except asyncpg.UniqueViolationError as e:
        error_message = str(e)
        if "Ya existe un usuario con el email" in error_message:
            return {
                "success": False,
                "message": f"Ya existe un usuario registrado con el email: {email}"
            }
        elif "Ya existe un usuario con el auth_id" in error_message:
            return {
                "success": False,
                "message": f"Ya existe un usuario registrado con el auth_id: {auth_id}"
            }
        else:
            return {
                "success": False,
                "message": f"Error de integridad: {error_message}"
            }
    except Exception as e:
        error_message = f"Error al crear usuario: {str(e)}"
        logger.error(error_message)
        return {
            "success": False,
            "message": error_message
        }

async def get_user_by_auth_id(auth_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene un usuario por su auth_id de Auth0.

    Args:
        auth_id: ID de Auth0 del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Diccionario con los datos del usuario o None si no existe
    """
    try:
        user_data = await fetch_one(
            """
            SELECT
                u.id as user_id,
                u.auth_id,
                u.full_name,
                u.email,
                u.sector_id,
                u.last_access,
                u.created_at,
                u.estado,
                cs.id as default_seal_id,
                cs.name as default_seal_name
            FROM users u
            LEFT JOIN user_seals us ON u.id = us.user_id
            LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
            WHERE u.auth_id = $1 AND u.estado = 1
            """,
            auth_id,
            schema_name=schema_name
        )

        if user_data:
            # Actualizar last_access (pasando schema_name para multi-tenant)
            await update_last_access(auth_id, schema_name=schema_name)
            return dict(user_data)

        return None

    except Exception as e:
        logger.error(f"Error al obtener usuario por auth_id: {str(e)}")
        return None

async def get_user_by_id(user_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene un usuario por su UUID con información de sector y departamento.

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Diccionario con los datos del usuario o None si no existe
    """
    from services.users.queries import get_user_by_id_query

    try:
        user_data = await fetch_one(
            get_user_by_id_query(),
            user_id,
            schema_name=schema_name
        )

        if user_data:
            return dict(user_data)

        return None

    except Exception as e:
        logger.error(f"Error al obtener usuario por ID {user_id}: {str(e)}", exc_info=True)
        return None

async def update_user_profile(user_id: str, full_name: Optional[str] = None,
                       country_id: Optional[str] = None, profile_picture_url: Optional[str] = None,
                       sector_id: Optional[str] = None, *, schema_name: str) -> Dict[str, Any]:
    """
    Actualiza el perfil de un usuario.

    Args:
        user_id: UUID del usuario
        full_name: Nuevo nombre completo (opcional)
        country_id: Nuevo identificador nacional (columna CountryID en BD). En Argentina es el CUIT (opcional)
        profile_picture_url: Nueva URL de foto de perfil (opcional)
        sector_id: Nuevo sector (opcional)

    Returns:
        Diccionario con el resultado de la operación
    """
    import logging
    from services.users.queries import get_updated_user_profile_query
    from config.constants import (
        PROFILE_NO_FIELDS_TO_UPDATE_ERROR,
        PROFILE_UPDATE_FAILED_ERROR,
        PROFILE_UPDATE_ERROR
    )

    try:
        # Construir la consulta dinámicamente según los campos proporcionados
        update_fields = []
        update_values = []

        if full_name is not None:
            update_fields.append(f"full_name = ${len(update_values) + 1}")
            update_values.append(full_name)

        if country_id is not None:
            # CountryID es el nombre de la columna en BD (multi-país: CUIT en Argentina)
            update_fields.append(f'"CountryID" = ${len(update_values) + 1}')
            update_values.append(country_id)

        if profile_picture_url is not None:
            update_fields.append(f"profile_picture_url = ${len(update_values) + 1}")
            update_values.append(profile_picture_url)

        if sector_id is not None:
            update_fields.append(f"sector_id = ${len(update_values) + 1}")
            update_values.append(sector_id)

        if not update_fields:
            logger.warning(f"Intento de actualizar perfil {user_id} sin campos")
            return {
                "success": False,
                "message": PROFILE_NO_FIELDS_TO_UPDATE_ERROR
            }

        # Agregar user_id al final para la cláusula WHERE
        update_values.append(user_id)
        where_placeholder = f"${len(update_values)}"

        update_query = f"""
            UPDATE users
            SET {', '.join(update_fields)}
            WHERE id = {where_placeholder} AND estado = 1
            RETURNING id as user_id
        """

        # Primero hacer el UPDATE
        result = await fetch_one(update_query, *update_values, schema_name=schema_name)

        if not result:
            logger.warning(f"No se pudo actualizar perfil para user_id {user_id}")
            return {
                "success": False,
                "message": PROFILE_UPDATE_FAILED_ERROR
            }

        # Luego obtener los datos completos con JOIN
        updated_user = await fetch_one(
            get_updated_user_profile_query(),
            user_id,
            schema_name=schema_name
        )

        if updated_user:
            logger.info(f"Perfil actualizado exitosamente para user_id {user_id}")
            return {
                "success": True,
                "message": "Perfil actualizado exitosamente",
                "user": dict(updated_user)
            }
        else:
            logger.error(f"Usuario {user_id} no encontrado después de UPDATE")
            return {
                "success": False,
                "message": PROFILE_UPDATE_FAILED_ERROR
            }

    except Exception as e:
        error_message = PROFILE_UPDATE_ERROR.format(error=str(e))
        logger.error(error_message, exc_info=True)
        return {
            "success": False,
            "message": error_message
        }

async def update_last_access(auth_id: str, *, schema_name: str) -> bool:
    """
    Actualiza la fecha de último acceso de un usuario.

    Args:
        auth_id: ID de Auth0 del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        True si se actualizó correctamente, False en caso contrario
    """
    try:
        status = await execute(
            """
            UPDATE users
            SET last_access = NOW()
            WHERE auth_id = $1 AND estado = 1
            """,
            auth_id,
            schema_name=schema_name
        )

        return int(status.split()[-1]) > 0 if status else False

    except Exception as e:
        logger.error(f"Error al actualizar last_access: {str(e)}")
        return False

async def get_user_by_email(email: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene un usuario por su email.

    Args:
        email: Email del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Diccionario con los datos del usuario o None si no existe
    """
    try:
        user_data = await fetch_one(
            """
            SELECT
                u.id as user_id,
                u.auth_id,
                u.full_name,
                u.email,
                u.profile_picture_url,
                u.sector_id,
                u.last_access,
                u.created_at,
                u.estado,
                us.city_seal_id as default_seal_id,
                cs.name as default_seal_name,
                cs.acronym as default_seal_acronym
            FROM users u
            LEFT JOIN user_seals us ON u.id = us.user_id
            LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
            WHERE u.email = $1 AND u.estado = 1
            """,
            email,
            schema_name=schema_name
        )

        if user_data:
            return dict(user_data)

        return None

    except Exception as e:
        logger.error(f"Error al obtener usuario por email: {str(e)}")
        return None

async def get_first_active_user(*, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene el primer usuario activo del sistema.
    Usado para autenticación con API key en modo testing.

    Args:
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Diccionario con los datos del usuario o None si no existe
    """
    try:
        user_data = await fetch_one(
            """
            SELECT
                u.id as user_id,
                u.auth_id,
                u.full_name,
                u.email,
                u.sector_id,
                u.estado
            FROM users u
            WHERE u.estado = 1
            ORDER BY u.created_at ASC
            LIMIT 1
            """,
            schema_name=schema_name
        )

        if user_data:
            return dict(user_data)

        return None

    except Exception as e:
        logger.error(f"Error al obtener primer usuario activo: {str(e)}")
        return None

async def get_user_sector_permissions(user_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    """
    Obtiene todos los sectores a los que el usuario tiene acceso con sus permisos.

    Incluye:
    - Sector principal del usuario (siempre con can_view=true, can_edit=true)
    - Sectores adicionales de user_sector_permissions (con permisos configurados)

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Lista de diccionarios con información de sectores y permisos.
        Ejemplo:
        [
            {
                "sector_id": "uuid",
                "sector_acronym": "SECOBRA",
                "department_id": "uuid",
                "department_name": "Secretaría de Obras",
                "department_acronym": "SECOBR",
                "can_view": True,
                "can_edit": True,
                "is_primary": True
            },
            ...
        ]
    """
    from services.users.queries import get_user_sector_permissions_query

    try:
        # Query necesita user_id dos veces (para UNION de sector principal y adicionales)
        results = await fetch_all(
            get_user_sector_permissions_query(),
            user_id, user_id,
            schema_name=schema_name
        )

        if results:
            return [dict(row) for row in results]

        return []

    except Exception as e:
        logger.error(f"Error al obtener permisos de sectores para user_id {user_id}: {str(e)}", exc_info=True)
        return []
