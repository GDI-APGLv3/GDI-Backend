from config.constants import SYSTEM_TEST_USER_UUID


def get_user_by_id_query() -> str:
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
    return """
        SELECT id as sector_id, acronym, department_id, is_active
        FROM sectors
        WHERE id = $1
    """


def search_users_by_name_query() -> str:
    return f"""
        SELECT DISTINCT ON (u.id)
            u.id as user_id,
            u.full_name,
            u.email,
            u.profile_picture_url,
            u.estado as is_active,
            cs.name as seal_name,
            d.acronym as department_acronym,
            s.acronym as sector_acronym,
            s.primary_color as sector_color
        FROM users u
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.estado = 1
          AND u.id != '{SYSTEM_TEST_USER_UUID}'::uuid
          AND (
            LOWER(u.full_name) LIKE $1
            OR LOWER(u.full_name) LIKE $2
            OR similarity(LOWER(u.full_name), LOWER($3)) > 0.3
          )
        ORDER BY u.id, cs.name
        LIMIT COALESCE($4, 100)
    """


def get_users_by_ids_query() -> str:
    return f"""
        SELECT DISTINCT ON (u.id)
            u.id as user_id,
            u.full_name,
            u.email,
            u.profile_picture_url,
            u.estado as is_active,
            cs.name as seal_name,
            d.acronym as department_acronym,
            s.acronym as sector_acronym,
            s.primary_color as sector_color
        FROM users u
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.id = ANY($1::uuid[])
          AND u.id != '{SYSTEM_TEST_USER_UUID}'::uuid
        ORDER BY u.id, cs.name
    """


def count_users_by_name_query() -> str:
    return f"""
        SELECT COUNT(DISTINCT u.id) as count
        FROM users u
        WHERE u.estado = 1
          AND u.id != '{SYSTEM_TEST_USER_UUID}'::uuid
          AND (
            LOWER(u.full_name) LIKE $1
            OR LOWER(u.full_name) LIKE $2
            OR similarity(LOWER(u.full_name), LOWER($3)) > 0.3
          )
    """


def search_user_by_email_query() -> str:
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
    return f"""
        SELECT
            id as user_id,
            full_name,
            email
        FROM users
        WHERE estado = 1
          AND id != '{SYSTEM_TEST_USER_UUID}'::uuid
        ORDER BY full_name ASC
    """


def get_user_sector_permissions_query() -> str:
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

