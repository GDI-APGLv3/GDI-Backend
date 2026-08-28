

def get_user_sectors_query() -> str:
    return """
        -- Sector principal (siempre incluido si el sector está activo)
        SELECT u.sector_id as sector_id
        FROM users u
        JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1 AND u.sector_id IS NOT NULL AND s.is_active = true

        UNION

        -- Sectores adicionales (can_view=true implica visibilidad; can_edit sin can_view no alcanza)
        SELECT usp.sector_id as sector_id
        FROM user_sector_permissions usp
        JOIN sectors s ON usp.sector_id = s.id
        WHERE usp.user_id = $2 AND usp.can_view = true AND s.is_active = true
    """
