"""
Consultas SQL centralizadas para el módulo de onboarding de usuarios.
"""

def validate_auth_id_not_in_use_query() -> str:
    """Query para verificar que auth_id no esté en uso por otro usuario activo."""
    return """
        SELECT user_id, email, full_name 
        FROM users 
        WHERE auth_id = %s AND estado = 1
    """


def fetch_user_by_email_query() -> str:
    """Query para buscar usuario completo por email con información de sector y sello."""
    return """
        SELECT
            u.id as user_id,
            u.auth_id,
            u.email,
            u.full_name,
            u.profile_picture_url,
            u.sector_id,
            u.estado,
            u.created_at,
            u.last_access,
            s.acronym as sector_name,
            d.name as department_name,
            cs.id as seal_id,
            cs.name as seal_name
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        WHERE LOWER(u.email) = %s
        LIMIT 1
    """


def get_random_active_sector_query() -> str:
    """Query para obtener sectores activos con información de departamento."""
    return """
        SELECT s.id as sector_id, s.acronym as name, d.name as department_name
        FROM sectors s
        JOIN departments d ON s.department_id = d.id
        WHERE s.is_active = true
        ORDER BY s.acronym
    """


def get_random_active_seal_query() -> str:
    """Query para obtener sellos activos."""
    return """
        SELECT id as seal_id, name
        FROM city_seals
        ORDER BY name
    """


def update_existing_active_user_query(update_fields: list) -> str:
    """
    Query para actualizar usuario existente activo.
    
    Args:
        update_fields: Lista de campos a actualizar (ej: ["profile_picture_url = %s"])
    """
    return f"""
        UPDATE users
        SET {', '.join(update_fields)}
        WHERE user_id = %s
    """


def insert_user_seal_query() -> str:
    """Query para insertar sello en user_seals (solo si no existe uno para ese usuario)."""
    return """
        INSERT INTO user_seals (user_id, city_seal_id)
        SELECT %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM user_seals WHERE user_id = %s
        )
    """


def activate_invited_user_with_sector_query() -> str:
    """Query para activar usuario invitado asignando sector aleatorio.
    NOTA: El sello se asigna via user_seals, no en columna users.default_seal_id.
    """
    return """
        UPDATE users
        SET auth_id = %s,
            full_name = %s,
            profile_picture_url = %s,
            sector_id = %s,
            estado = 1,
            created_at = NOW(),
            last_access = NOW()
        WHERE id = %s
        RETURNING id as user_id, auth_id, email, full_name, sector_id, estado
    """


def activate_invited_user_without_sector_query() -> str:
    """Query para activar usuario invitado que ya tiene sector.
    NOTA: El sello se asigna via user_seals, no en columna users.default_seal_id.
    """
    return """
        UPDATE users
        SET auth_id = %s,
            full_name = %s,
            profile_picture_url = %s,
            estado = 1,
            created_at = NOW(),
            last_access = NOW()
        WHERE id = %s
        RETURNING id as user_id, auth_id, email, full_name, sector_id, estado
    """


def create_new_user_query() -> str:
    """Query para crear nuevo usuario con asignaciones.
    NOTA: El sello se asigna via user_seals, no en columna users.default_seal_id.
    """
    return """
        INSERT INTO users (
            id,
            auth_id,
            email,
            full_name,
            profile_picture_url,
            sector_id,
            estado,
            created_at,
            last_access
        )
        VALUES (%s, %s, %s, %s, %s, %s, 1, NOW(), NOW())
        RETURNING id as user_id, auth_id, email, full_name, sector_id, estado
    """


def update_last_access_query() -> str:
    """Query para actualizar timestamp de last_access."""
    return """
        UPDATE users
        SET last_access = NOW()
        WHERE id = %s AND estado = 1
    """


def get_user_by_id_query() -> str:
    """Query para obtener usuario por ID con información de sector, departamento y sello."""
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
        WHERE u.id = %s AND u.estado = 1
    """


def update_user_profile_query(update_fields: list) -> str:
    """
    Query para actualizar perfil de usuario.

    Args:
        update_fields: Lista de campos a actualizar (ej: ["full_name = %s", "cuit = %s"])
    """
    return f"""
        UPDATE users
        SET {', '.join(update_fields)}
        WHERE id = %s AND estado = 1
        RETURNING id as user_id
    """


def get_updated_user_profile_query() -> str:
    """Query para obtener datos completos del usuario después de actualizar perfil."""
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
        WHERE u.id = %s
    """


def validate_sector_exists_query() -> str:
    """Query para verificar si un sector existe y está activo."""
    return """
        SELECT id as sector_id, acronym, department_id, is_active
        FROM sectors
        WHERE id = %s
    """


def search_users_by_name_query() -> str:
    """Query para buscar usuarios por nombre con información completa (sello y departamento)."""
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
        WHERE u.estado = 1
          AND (
            LOWER(u.full_name) LIKE %(pattern_start)s
            OR LOWER(u.full_name) LIKE %(pattern_word_start)s
            OR similarity(LOWER(u.full_name), LOWER(%(search_term)s)) > 0.3
          )
        ORDER BY u.id, cs.name
        LIMIT COALESCE(%(limit)s, 100)
    """


def count_users_by_name_query() -> str:
    """Query para contar usuarios que coinciden con un patrón de búsqueda."""
    return """
        SELECT COUNT(DISTINCT u.id) as count
        FROM users u
        WHERE u.estado = 1
          AND (
            LOWER(u.full_name) LIKE %(pattern_start)s
            OR LOWER(u.full_name) LIKE %(pattern_word_start)s
            OR similarity(LOWER(u.full_name), LOWER(%(search_term)s)) > 0.3
          )
    """


def search_user_by_email_query() -> str:
    """Query para buscar un usuario por email (activo o inactivo)."""
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
        WHERE LOWER(u.email) = %(email)s
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
        WHERE u.id = %s AND s.is_active = true

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
        WHERE usp.user_id = %s AND s.is_active = true

        ORDER BY is_primary DESC, department_acronym, sector_acronym
    """

