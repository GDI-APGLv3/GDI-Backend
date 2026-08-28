

def get_viewable_cases_cte() -> str:
    return """
        WITH user_sectors AS (
            SELECT sector_id FROM (
                -- Sector principal
                SELECT s.id as sector_id
                FROM users u
                JOIN sectors s ON u.sector_id = s.id
                WHERE u.id = $1 AND s.is_active = true

                UNION

                -- Sectores adicionales con can_view
                SELECT s2.id as sector_id
                FROM users u
                JOIN user_sector_permissions usp ON u.id = usp.user_id
                JOIN sectors s2 ON usp.sector_id = s2.id
                WHERE u.id = $2 AND s2.is_active = true AND usp.can_view = true
            ) sectors
        ),
        viewable_cases AS (
            -- Expedientes que el usuario puede ver
            SELECT DISTINCT c.id
            FROM cases c
            WHERE c.status = 'active' AND (
                -- Tiene movimiento activo en sector del usuario
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.assigned_sector_id IN (SELECT sector_id FROM user_sectors)
                    AND cm.is_active = true
                )
                -- O es admin por última transferencia
                OR EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id AND cm.type = 'transfer'
                    AND cm.is_active = false
                    AND cm.admin_sector_id IN (SELECT sector_id FROM user_sectors)
                    AND cm.closed_at = (
                        SELECT MAX(cm2.closed_at) FROM case_movements cm2
                        WHERE cm2.case_id = c.id AND cm2.type = 'transfer'
                    )
                )
                -- O es admin por creación (sin transfers)
                OR (
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id AND cm.type = 'creation'
                        AND cm.admin_sector_id IN (SELECT sector_id FROM user_sectors)
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id AND cm.type = 'transfer'
                    )
                )
            )
        )
    """
