"""
Queries SQL centralizadas para el módulo de Dashboard (Feed de actividad).

Nota: get_user_sectors_query() fue movida a services/shared/user_queries.py
para evitar duplicación con case_queries.py
"""


def get_feed_movements_query(sector_placeholders: str) -> str:
    """
    Query principal para obtener movimientos del feed.

    Trae case_movements de expedientes donde el usuario tiene VIEW,
    ordenados por fecha descendente con paginación.

    Args:
        sector_placeholders: Placeholders para los sectores del usuario (ej: "%s, %s, %s")

    Returns:
        str: Query SQL completo
    """
    return f"""
        WITH user_sectors AS (
            SELECT sector_id FROM (
                -- Sector principal
                SELECT s.id as sector_id
                FROM users u
                JOIN sectors s ON u.sector_id = s.id
                WHERE u.id = %s AND s.is_active = true

                UNION

                -- Sectores adicionales con can_view
                SELECT s2.id as sector_id
                FROM users u
                JOIN user_sector_permissions usp ON u.id = usp.user_id
                JOIN sectors s2 ON usp.sector_id = s2.id
                WHERE u.id = %s AND s2.is_active = true AND usp.can_view = true
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
        SELECT
            cm.id as movement_id,
            cm.type::text as movement_type,
            cm.reason,
            cm.created_at,
            cm.is_active,
            c.id as case_id,
            c.case_number,
            c.reference as case_reference,
            NULL as case_ai_summary,
            c.short_ai_summary as case_short_ai_summary,
            ct.acronym as case_type,
            ct.type_name as case_type_name,
            u.full_name as user_name,
            u.profile_picture_url as user_photo,
            COALESCE(d.acronym || '#' || s.acronym, '') as user_sector,
            s.primary_color as user_sector_color,
            sd.official_number as doc_number,
            sd.reference as doc_reference,
            sd.resume as doc_ai_summary,
            sd.short_resume as doc_short_resume,
            COUNT(*) OVER() as total_count
        FROM case_movements cm
        JOIN cases c ON cm.case_id = c.id
        JOIN case_templates ct ON c.case_template_id = ct.id
        LEFT JOIN users u ON cm.user_id = u.id
        LEFT JOIN sectors s ON cm.creator_sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        LEFT JOIN official_documents sd ON cm.supporting_document_id = sd.id AND sd.signed_at IS NOT NULL
        WHERE cm.case_id IN (SELECT id FROM viewable_cases)
        ORDER BY cm.created_at DESC
        LIMIT %s OFFSET %s
    """


def get_cases_in_sector_count_query() -> str:
    """
    Query para contar expedientes donde el sector del usuario es admin_sector activo.

    Returns:
        str: Query SQL que retorna el conteo
    """
    return """
        WITH user_main_sector AS (
            SELECT u.sector_id
            FROM users u
            WHERE u.id = %s
        )
        SELECT COUNT(DISTINCT c.id) as count
        FROM cases c
        WHERE c.status = 'active'
        AND (
            -- Es admin por última transferencia
            EXISTS (
                SELECT 1 FROM case_movements cm
                WHERE cm.case_id = c.id AND cm.type = 'transfer'
                AND cm.is_active = false
                AND cm.admin_sector_id = (SELECT sector_id FROM user_main_sector)
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
                    AND cm.admin_sector_id = (SELECT sector_id FROM user_main_sector)
                )
                AND NOT EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id AND cm.type = 'transfer'
                )
            )
        )
    """
