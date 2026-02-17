"""
Servicios para la gestión de usuarios que contienen la lógica de negocio.
Estos servicios implementan las consultas a la base de datos y la manipulación de datos de usuarios.
"""

import psycopg2
from typing import List, Dict, Any, Optional
from database import get_db_connection, get_db_cursor
from datetime import datetime
from shared.logging import get_logger

logger = get_logger(__name__)

def create_user(auth_id: str, full_name: str, email: str,
                cuit: Optional[str] = None, profile_picture_url: Optional[str] = None,
                sector_id: Optional[str] = None, *, schema_name: str) -> Dict[str, Any]:
    """
    Crea un nuevo usuario usando la función stored procedure de PostgreSQL.

    Args:
        auth_id: ID de Auth0 del usuario
        full_name: Nombre completo del usuario
        email: Email del usuario
        cuit: CUIT del usuario (opcional)
        profile_picture_url: URL de la foto de perfil (opcional)
        sector_id: UUID del sector (opcional)

    Returns:
        Diccionario con la información del usuario creado o error
    """
    try:
        with get_db_cursor(commit=True, schema_name=schema_name) as cursor:
            # Llamar a la función stored procedure
            cursor.execute(
                """
                SELECT * FROM create_user(
                    p_auth_id := %s,
                    p_full_name := %s,
                    p_email := %s,
                    p_cuit := %s,
                    p_profile_picture_url := %s,
                    p_sector_id := %s
                )
                """,
                (auth_id, full_name, email, cuit, profile_picture_url, sector_id)
            )

            user_data = cursor.fetchone()

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

    except psycopg2.IntegrityError as e:
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
    except psycopg2.Error as e:
        error_message = f"Error al crear usuario: {str(e)}"
        logger.error(error_message)
        return {
            "success": False,
            "message": error_message
        }

def get_user_by_auth_id(auth_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene un usuario por su auth_id de Auth0.

    Args:
        auth_id: ID de Auth0 del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Diccionario con los datos del usuario o None si no existe
    """
    try:
        with get_db_cursor(schema_name=schema_name) as cursor:
            cursor.execute(
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
                WHERE u.auth_id = %s AND u.estado = 1
                """,
                (auth_id,)
            )

            user_data = cursor.fetchone()

            if user_data:
                # Actualizar last_access (pasando schema_name para multi-tenant)
                update_last_access(auth_id, schema_name=schema_name)
                return dict(user_data)

            return None

    except psycopg2.Error as e:
        logger.error(f"Error al obtener usuario por auth_id: {str(e)}")
        return None

def get_user_by_id(user_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
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
        with get_db_cursor(schema_name=schema_name) as cursor:
            cursor.execute(get_user_by_id_query(), (user_id,))
            user_data = cursor.fetchone()

            if user_data:
                return dict(user_data)

            return None

    except psycopg2.Error as e:
        logger.error(f"Error al obtener usuario por ID {user_id}: {str(e)}", exc_info=True)
        return None

def update_user_profile(user_id: str, full_name: Optional[str] = None,
                       cuit: Optional[str] = None, profile_picture_url: Optional[str] = None,
                       sector_id: Optional[str] = None, *, schema_name: str) -> Dict[str, Any]:
    """
    Actualiza el perfil de un usuario.

    Args:
        user_id: UUID del usuario
        full_name: Nuevo nombre completo (opcional)
        cuit: Nuevo CUIT (opcional)
        profile_picture_url: Nueva URL de foto de perfil (opcional)
        sector_id: Nuevo sector (opcional)

    Returns:
        Diccionario con el resultado de la operación
    """
    import logging
    from services.users.queries import update_user_profile_query, get_updated_user_profile_query
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
            update_fields.append("full_name = %s")
            update_values.append(full_name)

        if cuit is not None:
            update_fields.append("cuit = %s")
            update_values.append(cuit)

        if profile_picture_url is not None:
            update_fields.append("profile_picture_url = %s")
            update_values.append(profile_picture_url)

        if sector_id is not None:
            update_fields.append("sector_id = %s")
            update_values.append(sector_id)

        if not update_fields:
            logger.warning(f"Intento de actualizar perfil {user_id} sin campos")
            return {
                "success": False,
                "message": PROFILE_NO_FIELDS_TO_UPDATE_ERROR
            }

        # Agregar user_id al final para la cláusula WHERE
        update_values.append(user_id)

        # Actualizar y retornar con datos de sector y departamento
        with get_db_cursor(commit=True, schema_name=schema_name) as cursor:
            # Primero hacer el UPDATE
            cursor.execute(update_user_profile_query(update_fields), update_values)
            result = cursor.fetchone()

            if not result:
                logger.warning(f"No se pudo actualizar perfil para user_id {user_id}")
                return {
                    "success": False,
                    "message": PROFILE_UPDATE_FAILED_ERROR
                }

            # Luego obtener los datos completos con JOIN
            cursor.execute(get_updated_user_profile_query(), (user_id,))
            updated_user = cursor.fetchone()

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

    except psycopg2.Error as e:
        error_message = PROFILE_UPDATE_ERROR.format(error=str(e))
        logger.error(error_message, exc_info=True)
        return {
            "success": False,
            "message": error_message
        }

def update_last_access(auth_id: str, *, schema_name: str) -> bool:
    """
    Actualiza la fecha de último acceso de un usuario.

    Args:
        auth_id: ID de Auth0 del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        True si se actualizó correctamente, False en caso contrario
    """
    try:
        with get_db_cursor(commit=True, schema_name=schema_name) as cursor:
            cursor.execute(
                """
                UPDATE users
                SET last_access = NOW()
                WHERE auth_id = %s AND estado = 1
                """,
                (auth_id,)
            )

            return cursor.rowcount > 0

    except psycopg2.Error as e:
        logger.error(f"Error al actualizar last_access: {str(e)}")
        return False

def get_user_by_email(email: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene un usuario por su email.

    Args:
        email: Email del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Diccionario con los datos del usuario o None si no existe
    """
    try:
        with get_db_cursor(schema_name=schema_name) as cursor:
            cursor.execute(
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
                WHERE u.email = %s AND u.estado = 1
                """,
                (email,)
            )

            user_data = cursor.fetchone()

            if user_data:
                return dict(user_data)

            return None

    except psycopg2.Error as e:
        logger.error(f"Error al obtener usuario por email: {str(e)}")
        return None

def get_first_active_user(*, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene el primer usuario activo del sistema.
    Usado para autenticación con API key en modo testing.

    Args:
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Diccionario con los datos del usuario o None si no existe
    """
    try:
        with get_db_cursor(schema_name=schema_name) as cursor:
            cursor.execute(
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
                """
            )

            user_data = cursor.fetchone()

            if user_data:
                return dict(user_data)

            return None

    except psycopg2.Error as e:
        logger.error(f"Error al obtener primer usuario activo: {str(e)}")
        return None

def get_user_sector_permissions(user_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
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
        with get_db_cursor(schema_name=schema_name) as cursor:
            # Query necesita user_id dos veces (para UNION de sector principal y adicionales)
            cursor.execute(get_user_sector_permissions_query(), (user_id, user_id))
            results = cursor.fetchall()

            if results:
                return [dict(row) for row in results]

            return []

    except psycopg2.Error as e:
        logger.error(f"Error al obtener permisos de sectores para user_id {user_id}: {str(e)}", exc_info=True)
        return []