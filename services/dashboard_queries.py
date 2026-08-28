
from services.shared.viewable_cases import get_viewable_cases_cte


def get_feed_movements_query(sector_placeholders: str) -> str:
    return get_viewable_cases_cte() + """
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
            sd.short_resume as doc_short_resume
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


def get_feed_movements_count_query() -> str:
    return get_viewable_cases_cte() + """
        SELECT COUNT(*) as total_count
        FROM case_movements cm
        WHERE cm.case_id IN (SELECT id FROM viewable_cases)
    """


def get_cases_in_sector_count_query() -> str:
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
