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
        sector_placeholders: Ignorado (compatibilidad de firma). La query usa CTEs internas.

    Returns:
        str: Query SQL completo con placeholders asyncpg ($1, $2, $3, $4)
        Parámetros: (user_id, user_id, page_size, offset)
    """
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
        LIMIT $3 OFFSET $4
    """


def get_cases_in_sector_count_query() -> str:
    """
    Query para contar expedientes donde el sector del usuario es admin_sector activo.

    BACKEND-07: La subquery correlacionada de MAX(closed_at) fue reemplazada por un
    LEFT JOIN LATERAL que agrega en una sola pasada por case:
      - last_closed_transfer_admin: admin_sector_id de la última transferencia CERRADA
        (is_active=false), ordenada por closed_at DESC.
      - has_any_transfer: si el case tiene CUALQUIER movimiento type='transfer'
        (activo o cerrado). Replica el NOT EXISTS transfer (any state) del original.

    Equivalencia con el original:
      - Rama A (admin por última transferencia): requería que existiera una transfer
        cerrada cuyo closed_at fuera MAX(closed_at de transfers totales) Y admin=user.
        En la práctica el original solo matcheaba si la última transfer cerrada coincidía
        con el MAX absoluto; mantenemos esa semántica usando last_closed_transfer_admin.
      - Rama B (admin por creación sin transfers): el original exigía NOT EXISTS de
        transfer en CUALQUIER estado. Acá lo replicamos con has_any_transfer = false.

    Returns:
        str: Query SQL que retorna el conteo. Parámetro: (user_id,)
    """
    return """
        WITH user_main_sector AS (
            SELECT u.sector_id
            FROM users u
            WHERE u.id = $1
        )
        SELECT COUNT(DISTINCT c.id) as count
        FROM cases c
        -- BACKEND-07: agregado en una sola pasada de case_movements por case.
        -- Devuelve el admin de la última transfer cerrada y un flag de existencia
        -- de transferencias (cualquier estado) para replicar exactamente el original.
        LEFT JOIN LATERAL (
            SELECT
                -- admin de la última transferencia cerrada (NULL si no hay cerradas).
                -- Equivale al cm.admin_sector_id de la fila con MAX(closed_at) entre cerradas.
                (
                    SELECT cm_last.admin_sector_id
                    FROM case_movements cm_last
                    WHERE cm_last.case_id = c.id
                      AND cm_last.type = 'transfer'
                      AND cm_last.is_active = false
                    ORDER BY cm_last.closed_at DESC
                    LIMIT 1
                ) AS last_closed_transfer_admin,
                -- true si existe CUALQUIER transferencia (activa o cerrada).
                -- Necesario para que la Rama B (creación) replique el NOT EXISTS
                -- transfer (any state) del original: si hay transfer pendiente
                -- (is_active=true), el case NO debe contarse aunque el creador sea
                -- del sector del usuario.
                EXISTS (
                    SELECT 1 FROM case_movements cm_any
                    WHERE cm_any.case_id = c.id
                      AND cm_any.type = 'transfer'
                ) AS has_any_transfer
        ) mv ON true
        WHERE c.status = 'active'
        AND (
            -- Rama A: es admin por la ÚLTIMA transferencia cerrada
            (
                mv.last_closed_transfer_admin IS NOT NULL
                AND mv.last_closed_transfer_admin = (SELECT sector_id FROM user_main_sector)
            )
            -- Rama B: es admin por creación Y no existe NINGUNA transferencia
            -- (replica NOT EXISTS transfer (any state) del original).
            OR (
                mv.has_any_transfer = false
                AND EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id AND cm.type = 'creation'
                    AND cm.admin_sector_id = (SELECT sector_id FROM user_main_sector)
                )
            )
        )
    """
