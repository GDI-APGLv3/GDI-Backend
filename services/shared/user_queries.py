"""
Queries SQL compartidas para permisos de usuario.
Usadas por case_service y dashboard_service.
"""


def get_user_sectors_query() -> str:
    """
    Query para obtener todos los sectores donde el usuario puede VER (can_view=true).

    - Sector principal: siempre incluido
    - Sectores adicionales: solo si can_view=true en user_sector_permissions

    IMPORTANTE: Requiere 2 parámetros (%s, %s) por el UNION.

    Returns:
        str: Query SQL que retorna sector_id
    """
    return """
        -- Sector principal (siempre puede ver)
        SELECT s.id as sector_id
        FROM users u
        JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = %s AND s.is_active = true

        UNION

        -- Sectores adicionales (solo si can_view=true)
        SELECT s2.id as sector_id
        FROM users u
        JOIN user_sector_permissions usp ON u.id = usp.user_id
        JOIN sectors s2 ON usp.sector_id = s2.id
        WHERE u.id = %s AND s2.is_active = true AND usp.can_view = true
    """
