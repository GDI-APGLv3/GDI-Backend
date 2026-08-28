
from config.constants import SYSTEM_TEST_USER_UUID


def get_user_sectors_with_permissions_query() -> str:
    return """
        -- Sector principal (siempre full access)
        SELECT
            s.id as sector_id,
            true as can_view,
            true as can_edit
        FROM users u
        JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1 AND s.is_active = true

        UNION

        -- Sectores adicionales (respeta permisos de user_sector_permissions)
        SELECT
            s2.id as sector_id,
            usp.can_view,
            usp.can_edit
        FROM users u
        JOIN user_sector_permissions usp ON u.id = usp.user_id
        JOIN sectors s2 ON usp.sector_id = s2.id
        WHERE u.id = $1 AND s2.is_active = true
    """


def get_cases_count_query(
    where_conditions: str,
    view_join: str = ""
) -> str:
    return f"""
        SELECT COUNT(*) as total_count
        FROM cases c
        JOIN case_templates ct ON c.case_template_id = ct.id
        {view_join}
        {where_conditions}
    """


def get_cases_list_query(
    where_conditions: str,
    view_join: str = "",
    sort_dir: str = "DESC",
    limit_param_idx: int = 0,
    offset_param_idx: int = 0,
) -> str:
    return f"""
        SELECT
            c.id,
            c.short_ai_summary,
            c.ai_summary,
            c.case_number,
            c.reference,
            ct.type_name,
            ct.acronym as case_type,
            ct.is_reserved as case_type_is_reserved,
            -- A7.2: columna materializada (mig. 083/084 en GDI-BD), backfill +
            -- triggers en INSERT de case_movements/case_official_documents con
            -- la misma fórmula que tenía el LATERAL de abajo. Permite ORDER BY
            -- con índice (cases(last_modified_at DESC)) en vez de materializar
            -- y ordenar todo el set visible antes de aplicar LIMIT/OFFSET.
            c.last_modified_at,
            -- Admin sector via LATERAL JOIN (1 query en vez de 3 subqueries)
            admin.acronym as admin_sector_acronym,
            admin.department as admin_sector_department,
            admin.color as admin_sector_color,
            -- Determinar si tiene transferencias
            EXISTS (
                SELECT 1 FROM case_movements cm
                WHERE cm.case_id = c.id AND cm.type = 'transfer'
            ) as has_transfers,
            -- Verificar si es ADMIN por última transferencia
            EXISTS (
                SELECT 1 FROM case_movements cm
                WHERE cm.case_id = c.id
                AND cm.type = 'transfer'
                AND cm.is_active = false
                AND cm.admin_sector_id = ANY($1::uuid[])
                AND cm.closed_at = (
                    SELECT MAX(cm2.closed_at)
                    FROM case_movements cm2
                    WHERE cm2.case_id = c.id
                    AND cm2.type = 'transfer'
                    AND cm2.is_active = false
                )
            ) as is_admin_by_transfer,
            -- Verificar si es ADMIN por creación (sin transfers)
            (
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'creation'
                    AND cm.admin_sector_id = ANY($1::uuid[])
                )
                AND NOT EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'transfer'
                )
            ) as is_admin_by_creation,
            -- Favorito del usuario actual
            EXISTS (
                SELECT 1 FROM case_favorites cf
                WHERE cf.case_id = c.id AND cf.user_id = $2
            ) as is_favorite,
            -- assigned_sectors en una sola query (optimización N+1)
            (SELECT COALESCE(
                json_agg(sub.sector_info ORDER BY sub.sort_key),
                '[]'::json
            )
            FROM (
                SELECT DISTINCT ON (d.acronym || '#' || s.acronym)
                    json_build_object(
                        'sector_acronym', d.acronym || '#' || s.acronym,
                        'department_name', d.name,
                        'sector_color', s.primary_color
                    ) as sector_info,
                    d.acronym || '#' || s.acronym as sort_key
                FROM case_movements cm
                JOIN sectors s ON cm.assigned_sector_id = s.id
                JOIN departments d ON s.department_id = d.id
                WHERE cm.case_id = c.id
                  AND cm.is_active = true
                  AND cm.assigned_sector_id IS NOT NULL
            ) sub
            ) as assigned_sectors_json,
            -- Responsables activos agrupables por DPTO#SECTOR (caritas en la lista).
            -- Regla "el Panel manda" (12/08/2026): la lista es espejo del Panel.
            --   type='ADMIN' -> responsables del sector administrador (case_responsibles).
            --   type='TASK'  -> responsables de TAREA de sectores actuantes
            --                   (case_assignment_tasks abiertas), que son los que
            --                   se ven en las cards del Panel.
            -- Los ADDITIONAL (seguidores legacy) NO pintan caritas.
            (SELECT COALESCE(
                json_agg(json_build_object(
                    'sector_acronym', rsub.sector_acronym,
                    'user_id', rsub.user_id,
                    'full_name', rsub.full_name,
                    'profile_picture_url', rsub.profile_picture_url,
                    'type', rsub.type
                ) ORDER BY rsub.type ASC, rsub.added_at ASC),
                '[]'::json
            )
            FROM (
                SELECT
                    d.acronym || '#' || s.acronym as sector_acronym,
                    u.id as user_id,
                    u.full_name,
                    u.profile_picture_url,
                    cr.type::text as type,
                    cr.added_at
                FROM case_responsibles cr
                JOIN users u ON cr.user_id = u.id
                JOIN sectors s ON cr.sector_id = s.id
                JOIN departments d ON s.department_id = d.id
                WHERE cr.case_id = c.id
                  AND cr.is_active = true
                  AND cr.type = 'ADMIN'
                UNION ALL
                SELECT
                    d.acronym || '#' || s.acronym as sector_acronym,
                    u.id as user_id,
                    u.full_name,
                    u.profile_picture_url,
                    'TASK'::text as type,
                    MIN(cat.created_at) as added_at
                FROM case_assignment_tasks cat
                JOIN users u ON cat.assigned_user_id = u.id
                JOIN sectors s ON cat.assigned_sector_id = s.id
                JOIN departments d ON s.department_id = d.id
                WHERE cat.case_id = c.id
                  AND cat.status = 'open'
                  AND cat.assigned_user_id IS NOT NULL
                GROUP BY 1, 2, 3, 4
            ) rsub
            ) as responsibles_json
        FROM cases c
        JOIN case_templates ct ON c.case_template_id = ct.id
        -- LATERAL JOIN: obtener admin_sector en 1 sola pasada (reemplaza 3 subqueries)
        LEFT JOIN LATERAL (
            SELECT
                d2.acronym || '#' || s2.acronym as acronym,
                d2.name as department,
                s2.primary_color as color
            FROM case_movements cm
            JOIN sectors s2 ON cm.admin_sector_id = s2.id
            JOIN departments d2 ON s2.department_id = d2.id
            WHERE cm.case_id = c.id
              AND cm.is_active = false
              AND cm.type IN ('creation', 'transfer')
            ORDER BY cm.closed_at DESC
            LIMIT 1
        ) admin ON true
        {view_join}
        {where_conditions}
        ORDER BY c.last_modified_at {sort_dir}
        LIMIT ${limit_param_idx} OFFSET ${offset_param_idx}
    """


def get_case_basic_info_query() -> str:
    return """
        SELECT
            c.id,
            c.case_number,
            c.reference,
            c.ai_summary,
            ct.type_name,
            ct.acronym as template_acronym,
            ct.is_reserved as template_is_reserved
        FROM cases c
        JOIN case_templates ct ON c.case_template_id = ct.id
        WHERE c.id = $1
    """


def get_user_sectors_for_case_query() -> str:
    return """
        -- Sector principal (siempre puede ver)
        SELECT s.id as sector_id
        FROM users u
        JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1 AND s.is_active = true

        UNION

        -- Sectores adicionales (solo si can_view=true)
        SELECT s2.id as sector_id
        FROM users u
        JOIN user_sector_permissions usp ON u.id = usp.user_id
        JOIN sectors s2 ON usp.sector_id = s2.id
        WHERE u.id = $1 AND s2.is_active = true AND usp.can_view = true
    """


def get_admin_sector_for_case_query() -> str:
    return """
        SELECT
            d.acronym || '#' || s.acronym as sector_acronym,
            d.name as department_name,
            s.id as sector_id,
            s.primary_color as sector_color
        FROM case_movements cm
        JOIN sectors s ON cm.admin_sector_id = s.id
        JOIN departments d ON s.department_id = d.id
        WHERE cm.case_id = $1
          AND cm.is_active = false
          AND cm.type IN ('creation', 'transfer')
        ORDER BY cm.closed_at DESC
        LIMIT 1
    """


def get_assigned_sectors_for_case_query() -> str:
    return """
        SELECT DISTINCT
            d.acronym || '#' || s.acronym as sector_acronym,
            d.name as department_name,
            s.id as sector_id,
            s.primary_color as sector_color
        FROM case_movements cm
        JOIN sectors s ON cm.assigned_sector_id = s.id
        JOIN departments d ON s.department_id = d.id
        WHERE cm.case_id = $1
          AND cm.is_active = true
          AND cm.assigned_sector_id IS NOT NULL
        ORDER BY sector_acronym
    """


def get_available_templates_query() -> str:
    return """
        SELECT DISTINCT
            ct.id,
            ct.type_name,
            ct.acronym,
            ct.description,
            d.name as filing_department_name,
            d.acronym as filing_department_acronym,
            d.primary_color as filing_department_color
        FROM case_templates ct
        LEFT JOIN global_case_templates gct ON ct.global_case_template_id = gct.id
        LEFT JOIN departments d ON ct.filing_department_id = d.id
        WHERE ct.is_active = true
        AND (gct.id IS NULL OR gct.is_active = true)
        ORDER BY ct.type_name
    """


def get_user_validation_query() -> str:
    return """
        SELECT
            u.id as user_id,
            u.full_name,
            u.sector_id,
            s.department_id,
            u.can_global_search_documents,
            u.can_global_search_cases
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1 AND u.estado = 1
    """


def get_case_movements_query(paginated: bool = False) -> str:
    limit_clause = "\n        LIMIT $2 OFFSET $3" if paginated else ""
    return f"""
        SELECT
            cm.id,
            cm.type::text as movement_type,
            cm.reason,
            cm.created_at,
            cm.is_active,
            cm.closed_at,
            cm.closing_reason,
            u.id as user_id,
            u.full_name as user_full_name,
            u.email as user_email,
            u.profile_picture_url as user_profile_picture_url,
            cm.citizen_id as citizen_id,
            c_actor.full_name as citizen_full_name,
            c_actor.country_id as citizen_country_id,
            cs.id as creator_sector_id,
            d1.acronym || '#' || cs.acronym as creator_sector_name,
            adm.id as admin_sector_id,
            d2.acronym || '#' || adm.acronym as admin_sector_name,
            asn.id as assigned_sector_id,
            d3.acronym || '#' || asn.acronym as assigned_sector_name,
            cm.assigned_user_id,
            au.full_name as assigned_user_full_name,
            au.profile_picture_url as assigned_user_profile_picture_url,
            aud.acronym || '#' || aus.acronym as assigned_user_sector_name,
            aus.primary_color as assigned_user_sector_color,
            cm.supporting_document_id,
            sd.official_number as supporting_document_number,
            sd.reference as supporting_document_reference,
            sd.resume as supporting_document_resume
        FROM case_movements cm
        LEFT JOIN users u ON cm.user_id = u.id
        LEFT JOIN citizens c_actor ON cm.citizen_id = c_actor.id
        LEFT JOIN sectors cs ON cm.creator_sector_id = cs.id
        LEFT JOIN departments d1 ON cs.department_id = d1.id
        LEFT JOIN sectors adm ON cm.admin_sector_id = adm.id
        LEFT JOIN departments d2 ON adm.department_id = d2.id
        LEFT JOIN sectors asn ON cm.assigned_sector_id = asn.id
        LEFT JOIN departments d3 ON asn.department_id = d3.id
        LEFT JOIN users au ON cm.assigned_user_id = au.id
        LEFT JOIN sectors aus ON au.sector_id = aus.id
        LEFT JOIN departments aud ON aus.department_id = aud.id
        LEFT JOIN official_documents sd ON cm.supporting_document_id = sd.id AND sd.signed_at IS NOT NULL
        WHERE cm.case_id = $1
        ORDER BY cm.created_at DESC{limit_clause}
    """

def get_official_documents_query() -> str:
    return """
        SELECT
            cod.id,
            cod.order_number,
            cod.linking_date,
            cod.is_active,
            od.id as document_id,
            od.official_number,
            od.pdf_location,
            od.reference,
            od.short_resume,
            u.full_name as linked_by,
            COALESCE(d.acronym || '#' || s.acronym, '') as linked_sector,
            COALESCE(dt.is_reserved, false) as is_reserved,
            dt.visibility as document_type_visibility
        FROM case_official_documents cod
        JOIN official_documents od ON cod.official_document_id = od.id AND od.signed_at IS NOT NULL
        LEFT JOIN document_types dt ON od.document_type_id = dt.id
        LEFT JOIN users u ON cod.linking_user_id = u.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE cod.case_id = $1
        ORDER BY cod.order_number ASC
    """

def get_proposed_documents_query() -> str:
    return """
        SELECT
            cpd.id,
            cpd.document_draft_id,
            cpd.proposing_date,
            dd.reference,
            dd.short_resume,
            dd.status,
            dd.document_number,
            dt.name as document_type_name,
            dt.acronym as document_type_acronym,
            COALESCE(dt.is_reserved, false) as is_reserved,
            dt.visibility as document_type_visibility,
            u.full_name as proposed_by,
            CASE WHEN dd.status = 'signed' THEN true ELSE false END as can_link
        FROM case_proposed_documents cpd
        JOIN document_draft dd ON cpd.document_draft_id = dd.id
        LEFT JOIN document_types dt ON dd.document_type_id = dt.id
        LEFT JOIN users u ON cpd.proposing_user_id = u.id
        WHERE cpd.case_id = $1 AND cpd.is_active = true
        ORDER BY cpd.proposing_date ASC
    """


def get_proposed_document_by_id_query() -> str:
    return """
        SELECT cpd.id, cpd.case_id, cpd.document_draft_id, cpd.is_active,
               dd.reference, dd.status, dd.document_number
        FROM case_proposed_documents cpd
        JOIN document_draft dd ON cpd.document_draft_id = dd.id
        WHERE cpd.id = $1 AND cpd.case_id = $2
    """


def deactivate_proposed_document_query() -> str:
    return """
        UPDATE case_proposed_documents SET is_active = false WHERE id = $1 RETURNING id
    """


def get_case_permissions_data_query() -> str:
    return """
        SELECT
            c.id,
            c.owner_sector_id,
            c.created_by_user_id,
            c.status,
            s.department_id as owner_department_id
        FROM cases c
        LEFT JOIN sectors s ON c.owner_sector_id = s.id
        WHERE c.id = $1
    """

def get_user_sector_info_query() -> str:
    return """
        SELECT u.id as user_id, s.id as sector_id, s.department_id
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1
    """

def get_case_number_query() -> str:
    return """
        SELECT case_number, ai_summary, ai_summary_updated_at, short_ai_summary
        FROM cases
        WHERE id = $1
    """


def get_document_signers_query() -> str:
    return """
        SELECT json_agg(
            json_build_object(
                'user_id', ds.user_id,
                'full_name', u.full_name,
                'status', ds.status,
                'is_numerator', ds.is_numerator,
                'signing_order', ds.signing_order,
                'signed_at', ds.signed_at
            )
        ) as signers
        FROM document_signers ds
        JOIN users u ON ds.user_id = u.id
        WHERE ds.document_id = $1
    """


def update_document_content_query() -> str:
    return """
        UPDATE document_draft
        SET content = $1::jsonb,
            last_modified_at = CURRENT_TIMESTAMP
        WHERE id = $2
    """


def delete_document_signers_query() -> str:
    return "DELETE FROM document_signers WHERE document_id = $1"


def get_user_with_sector_query() -> str:
    return """
        SELECT u.id as user_id, u.full_name, u.sector_id, s.department_id
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1
    """

def get_case_with_target_sector_query() -> str:
    return """
        SELECT
            c.*,
            s_owner.department_id as owner_department_id,
            s_target.department_id as target_department_id,
            s_target.acronym as target_sector_acronym,
            d_target.name as target_department_name,
            d_target.acronym as target_department_acronym
        FROM cases c
        LEFT JOIN sectors s_owner ON c.owner_sector_id = s_owner.id
        LEFT JOIN sectors s_target ON s_target.id = $1
        LEFT JOIN departments d_target ON s_target.department_id = d_target.id
        WHERE c.id = $2
    """

def get_admin_sector_query() -> str:
    return """
        SELECT admin_sector_id
        FROM case_movements
        WHERE case_id = $1
          AND is_active = false
          AND type IN ('creation', 'transfer')
        ORDER BY closed_at DESC
        LIMIT 1
    """

def get_target_sector_query() -> str:
    return """
        SELECT s.id as sector_id, s.acronym, s.is_active,
               d.name as department_name, d.acronym as department_acronym
        FROM sectors s
        JOIN departments d ON s.department_id = d.id
        WHERE s.id = $1 AND s.is_active = true
    """

def get_assigned_user_query() -> str:
    return """
        SELECT u.id as user_id, u.full_name, u.sector_id
        FROM users u
        WHERE u.id = $1 AND u.sector_id = $2
    """

def update_case_ownership_query() -> str:
    return """
        UPDATE cases
        SET owner_sector_id = $1, owner_department_id = $2
        WHERE id = $3
    """

def close_movement_query() -> str:
    return """
        UPDATE case_movements
        SET closed_at = NOW(), closing_reason = $1, closed_by = $2, is_active = false
        WHERE id = $3
    """

def get_movement_for_closing_query() -> str:
    return """
        SELECT id, type, is_active, closed_at, assigned_sector_id, supporting_document_id
        FROM case_movements
        WHERE id = $1 AND case_id = $2
    """

def get_available_sectors_for_transfer_query() -> str:
    return """
        SELECT
            s.id as sector_id,
            s.acronym as sector_acronym,
            d.name as department_name,
            d.acronym as department_acronym,
            COUNT(u.id) as user_count
        FROM sectors s
        JOIN departments d ON s.department_id = d.id
        LEFT JOIN users u ON u.sector_id = s.id AND u.estado = 1
        WHERE s.is_active = true
        AND d.is_active = true
        AND s.id != (SELECT owner_sector_id FROM cases WHERE id = $1)
        AND NOT EXISTS (
            SELECT 1 FROM case_movements cm
            WHERE cm.case_id = $2
              AND cm.assigned_sector_id = s.id
              AND cm.is_active = true
              AND cm.type = 'assignment'
        )
        GROUP BY s.id, s.acronym, d.name, d.acronym, d.id
        ORDER BY d.name, s.acronym
    """

def get_sector_users_query() -> str:
    return """
        SELECT u.id as user_id, u.full_name
        FROM users u
        WHERE u.sector_id = $1
        AND u.estado = 1
        ORDER BY u.full_name
    """


def check_duplicate_assignment_query() -> str:
    return """
        SELECT id FROM case_movements
        WHERE case_id = $1
          AND assigned_sector_id = $2
          AND is_active = true
          AND type = 'assignment'
        LIMIT 1
    """


def get_assignable_users_query(with_sector: bool = False) -> str:
    if with_sector:
        sector_filter = """
              AND (
                u.sector_id = $2
                OR EXISTS (
                    SELECT 1 FROM user_sector_permissions usp2
                    WHERE usp2.user_id = u.id AND usp2.sector_id = $2
                )
              )"""
    else:
        sector_filter = ""

    return f"""
        WITH matching_users AS (
            SELECT DISTINCT u.id
            FROM users u
            JOIN sectors s ON s.id = u.sector_id
            JOIN departments d ON d.id = s.department_id
            WHERE u.estado = 1
              AND u.id != '{SYSTEM_TEST_USER_UUID}'::uuid
              AND s.is_active = true
              AND (
                u.full_name ILIKE $1
                OR s.acronym ILIKE $1
                OR d.acronym ILIKE $1
              ){sector_filter}
            UNION
            SELECT DISTINCT u.id
            FROM users u
            JOIN user_sector_permissions usp ON usp.user_id = u.id AND usp.can_edit = true
            JOIN sectors s ON s.id = usp.sector_id
            JOIN departments d ON d.id = s.department_id
            WHERE u.estado = 1
              AND u.id != '{SYSTEM_TEST_USER_UUID}'::uuid
              AND s.is_active = true
              AND (
                u.full_name ILIKE $1
                OR s.acronym ILIKE $1
                OR d.acronym ILIKE $1
              ){sector_filter}
            LIMIT 50
        )
        SELECT
            u.id AS user_id,
            u.full_name,
            u.profile_picture_url,
            s.id AS sector_id,
            s.acronym AS sector_acronym,
            d.name AS department_name,
            d.acronym AS department_acronym,
            true AS can_edit,
            true AS is_primary
        FROM matching_users mu
        JOIN users u ON u.id = mu.id
        JOIN sectors s ON s.id = u.sector_id
        JOIN departments d ON d.id = s.department_id
        WHERE s.is_active = true

        UNION ALL

        SELECT
            u.id AS user_id,
            u.full_name,
            u.profile_picture_url,
            s.id AS sector_id,
            s.acronym AS sector_acronym,
            d.name AS department_name,
            d.acronym AS department_acronym,
            usp.can_edit,
            false AS is_primary
        FROM matching_users mu
        JOIN users u ON u.id = mu.id
        JOIN user_sector_permissions usp ON usp.user_id = u.id AND usp.can_edit = true
        JOIN sectors s ON s.id = usp.sector_id
        JOIN departments d ON d.id = s.department_id
        WHERE s.is_active = true

        ORDER BY full_name, is_primary DESC
    """


def get_auto_link_proposals_query() -> str:
    return """
        SELECT
            cpd.id,
            cpd.case_id,
            cpd.proposing_user_id,
            c.case_number
        FROM case_proposed_documents cpd
        JOIN cases c ON c.id = cpd.case_id
        WHERE cpd.document_draft_id = $1
          AND cpd.is_active = true
          AND cpd.auto_link_on_sign = true
    """


def get_rebuild_auto_link_results_query() -> str:
    return """
        SELECT
            cpd.case_id::text AS case_id,
            c.case_number     AS case_number,
            EXISTS (
                SELECT 1 FROM case_official_documents cod
                WHERE cod.case_id      = cpd.case_id
                  AND cod.official_document_id = $1
                  AND cod.is_active    = true
            ) AS linked
        FROM case_proposed_documents cpd
        JOIN cases c ON c.id = cpd.case_id
        WHERE cpd.document_draft_id = $1
          AND cpd.auto_link_on_sign  = true
          AND cpd.proposing_date     < $2
          AND (
                cpd.is_active = true
                OR (
                    cpd.is_active = false
                    AND EXISTS (
                        SELECT 1 FROM case_official_documents cod2
                        WHERE cod2.case_id             = cpd.case_id
                          AND cod2.official_document_id = $1
                          AND cod2.is_active            = true
                    )
                )
              )
        ORDER BY cpd.proposing_date
    """


def get_cases_summary_query() -> str:
    return """
        WITH user_sectors AS (
            SELECT sector_id FROM user_sector_permissions
            WHERE user_id = $1 AND can_view = true
            UNION
            SELECT sector_id FROM users WHERE id = $2
        )
        SELECT
            COUNT(*) as total_cases,
            COUNT(*) FILTER (WHERE c.status = 'active') as active_cases,
            COUNT(*) FILTER (WHERE c.status = 'inactive') as inactive_cases,
            COUNT(*) FILTER (WHERE c.status = 'archived') as archived_cases,
            COUNT(*) FILTER (WHERE c.created_by_user_id = $3) as created_by_me,
            COUNT(DISTINCT c.owner_department_id) as departments_involved
        FROM cases c
        WHERE EXISTS (
            -- Asignado activamente
            SELECT 1 FROM case_movements cm
            WHERE cm.case_id = c.id
            AND cm.assigned_sector_id IN (SELECT sector_id FROM user_sectors)
            AND cm.is_active = true
        )
        OR EXISTS (
            -- Admin por transfer (último cerrado)
            SELECT 1 FROM case_movements cm
            WHERE cm.case_id = c.id
            AND cm.type = 'transfer'
            AND cm.is_active = false
            AND cm.admin_sector_id IN (SELECT sector_id FROM user_sectors)
            AND cm.closed_at = (
                SELECT MAX(cm2.closed_at) FROM case_movements cm2
                WHERE cm2.case_id = c.id AND cm2.type = 'transfer' AND cm2.is_active = false
            )
        )
        OR EXISTS (
            -- Admin por creation (sin transfers)
            SELECT 1 FROM case_movements cm
            WHERE cm.case_id = c.id
            AND cm.type = 'creation'
            AND cm.admin_sector_id IN (SELECT sector_id FROM user_sectors)
            AND NOT EXISTS (
                SELECT 1 FROM case_movements cm2
                WHERE cm2.case_id = c.id AND cm2.type = 'transfer'
            )
        )
    """
