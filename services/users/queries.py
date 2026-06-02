"""
Consultas SQL centralizadas para el módulo de usuarios.
Placeholders asyncpg: $1, $2, $3 (NO %s).
"""


def get_user_by_id_query() -> str:
    """Query para obtener usuario por ID con información de sector, departamento y sello.

    Parámetro asyncpg: $1 = user_id
    """
    return """
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
            s.acronym as sector_acronym,
            s.id as sector_id,
            s.primary_color as sector_color,
            d.id as department_id,
            d.name as department_name,
            d.acronym as department_acronym,
            cs.id as default_seal_id,
            cs.name as default_seal_name
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        WHERE u.id = $1 AND u.estado = 1
    """


def get_updated_user_profile_query() -> str:
    """Query para obtener datos completos del usuario después de actualizar perfil.

    Parámetro asyncpg: $1 = user_id
    """
    return """
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
            s.acronym as sector_acronym,
            s.id as sector_id,
            d.id as department_id,
            d.name as department_name,
            d.acronym as department_acronym,
            cs.id as default_seal_id,
            cs.name as default_seal_name
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        WHERE u.id = $1
    """


def validate_sector_exists_query() -> str:
    """Query para verificar si un sector existe y está activo.

    Parámetro asyncpg: $1 = sector_id
    """
    return """
        SELECT id as sector_id, acronym, department_id, is_active
        FROM sectors
        WHERE id = $1
    """


def search_users_by_name_query() -> str:
    """Query para buscar usuarios por nombre con información completa (sello y departamento).

    Parámetros posicionales asyncpg:
      $1 = pattern_start     (ej: 'ma%')
      $2 = pattern_word_start (ej: '% ma%')
      $3 = search_term       (ej: 'ma')
      $4 = limit             (int o None → COALESCE con 100)
    """
    return """
        SELECT DISTINCT ON (u.id)
            u.id as user_id,
            u.full_name,
            u.email,
            u.profile_picture_url,
            u.estado as is_active,
            cs.name as seal_name,
            d.acronym as department_acronym,
            s.acronym as sector_acronym
        FROM users u
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.estado = 1
          AND (
            LOWER(u.full_name) LIKE $1
            OR LOWER(u.full_name) LIKE $2
            OR similarity(LOWER(u.full_name), LOWER($3)) > 0.3
          )
        ORDER BY u.id, cs.name
        LIMIT COALESCE($4, 100)
    """


def count_users_by_name_query() -> str:
    """Query para contar usuarios que coinciden con un patrón de búsqueda.

    Parámetros posicionales asyncpg:
      $1 = pattern_start
      $2 = pattern_word_start
      $3 = search_term
    """
    return """
        SELECT COUNT(DISTINCT u.id) as count
        FROM users u
        WHERE u.estado = 1
          AND (
            LOWER(u.full_name) LIKE $1
            OR LOWER(u.full_name) LIKE $2
            OR similarity(LOWER(u.full_name), LOWER($3)) > 0.3
          )
    """


def search_user_by_email_query() -> str:
    """Query para buscar un usuario por email (activo o inactivo).

    Parámetros posicionales asyncpg:
      $1 = email (lowercase)
    """
    return """
        SELECT DISTINCT ON (u.id)
            u.id as user_id,
            u.full_name,
            u.email,
            u.profile_picture_url,
            u.estado as is_active,
            cs.name as seal_name,
            d.acronym as department_acronym
        FROM users u
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE LOWER(u.email) = $1
        ORDER BY u.id, cs.name
        LIMIT 1
    """


def list_all_users_query() -> str:
    """Query para listar todos los usuarios activos del sistema."""
    return """
        SELECT
            id as user_id,
            full_name,
            email
        FROM users
        WHERE estado = 1
        ORDER BY full_name ASC
    """


def get_user_sector_permissions_query() -> str:
    """
    Query para obtener permisos de sectores de un usuario.

    Retorna lista de sectores con información completa y permisos (can_view, can_edit).

    - Sector principal (users.sector_id): siempre incluido con can_view=true, can_edit=true
    - Sectores adicionales (user_sector_permissions): respeta can_view y can_edit configurados

    Formato retornado:
    [
        {
            "sector_id": "uuid",
            "sector_acronym": "SECOBRA",
            "department_id": "uuid",
            "department_name": "Secretaría de Obras",
            "department_acronym": "SECOBR",
            "can_view": true,
            "can_edit": true,
            "is_primary": true
        },
        ...
    ]
    """
    return """
        -- Sector principal del usuario (siempre full access)
        SELECT
            s.id as sector_id,
            s.acronym as sector_acronym,
            d.id as department_id,
            d.name as department_name,
            d.acronym as department_acronym,
            true as can_view,
            true as can_edit,
            true as is_primary
        FROM users u
        JOIN sectors s ON u.sector_id = s.id
        JOIN departments d ON s.department_id = d.id
        WHERE u.id = $1 AND s.is_active = true

        UNION

        -- Sectores adicionales con permisos configurados
        SELECT
            s.id as sector_id,
            s.acronym as sector_acronym,
            d.id as department_id,
            d.name as department_name,
            d.acronym as department_acronym,
            usp.can_view,
            usp.can_edit,
            false as is_primary
        FROM user_sector_permissions usp
        JOIN sectors s ON usp.sector_id = s.id
        JOIN departments d ON s.department_id = d.id
        WHERE usp.user_id = $2 AND s.is_active = true

        ORDER BY is_primary DESC, department_acronym, sector_acronym
    """

