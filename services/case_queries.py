"""
Queries SQL centralizadas para el módulo de casos/expedientes.
Todas las consultas SQL relacionadas con casos deben estar aquí.

Nota: get_user_sectors_query() fue movida a services/shared/user_queries.py
para evitar duplicación con dashboard_queries.py
"""


def get_user_sectors_with_permissions_query() -> str:
    """
    Obtiene sectores del usuario CON permisos granulares (can_view, can_edit).

    - Sector principal (users.sector_id): siempre can_view=true, can_edit=true
    - Sectores adicionales (user_sector_permissions): respeta can_view y can_edit

    NOTA: Esta query se embebe como subquery en can_user_view_case via f-string.
    Los placeholders $N son globales a la query externa ($1=user_id, $2=case_id),
    por eso ambos branches deben usar $1 para filtrar por user_id.

    Returns:
        str: Query SQL que retorna sector_id, can_view, can_edit
    """
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
    """
    Query separada para contar expedientes (sin subqueries de SELECT ni ORDER BY).
    Mucho más rápida que COUNT(*) OVER() porque no evalúa datos por fila.

    Params: ($1=sector_ids::uuid[], $2+=dynamic WHERE params)

    Args:
        where_conditions: Condiciones WHERE completas (con $N ya numerados)
        view_join: JOIN adicional para filtrar por vista (asignado/admin/actuante/favoritos)

    Returns:
        str: Query SQL que retorna solo el conteo
    """
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
    """
    Query principal para listar casos/expedientes con filtros.
    Optimizada: LATERAL JOINs en vez de subqueries repetidas, sin COUNT(*) OVER().

    Params: ($1=sector_ids::uuid[], $2=user_id, $3+=dynamic WHERE, $N-1=limit, $N=offset)

    Args:
        where_conditions: Condiciones WHERE completas (con $N ya numerados)
        view_join: JOIN adicional para filtrar por vista
        sort_dir: Dirección de ordenamiento ("ASC" o "DESC"). Default: "DESC"
        limit_param_idx: Índice $N para LIMIT (calculado por el caller)
        offset_param_idx: Índice $N para OFFSET (calculado por el caller)

    Returns:
        str: Query SQL completo
    """
    return f"""
        SELECT
            c.id,
            c.short_ai_summary,
            c.ai_summary,
            c.case_number,
            c.reference,
            ct.type_name,
            ct.acronym as case_type,
            -- BACKEND-04: last_modified_at calculado UNA SOLA VEZ via LATERAL JOIN
            -- y reutilizado tanto en SELECT como en ORDER BY (antes se duplicaba la subquery).
            lm.last_modified_at,
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
            ) as assigned_sectors_json
        FROM cases c
        JOIN case_templates ct ON c.case_template_id = ct.id
        -- LATERAL JOIN: calcular last_modified_at una sola vez (reutilizado en SELECT y ORDER BY)
        LEFT JOIN LATERAL (
            SELECT GREATEST(
                COALESCE(
                    (SELECT MAX(cm_lm.created_at)
                     FROM case_movements cm_lm
                     WHERE cm_lm.case_id = c.id),
                    c.created_at
                ),
                COALESCE(
                    (SELECT MAX(cod_lm.linking_date)
                     FROM case_official_documents cod_lm
                     WHERE cod_lm.case_id = c.id AND cod_lm.is_active = true),
                    c.created_at
                )
            ) AS last_modified_at
        ) lm ON true
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
        ORDER BY lm.last_modified_at {sort_dir}
        LIMIT ${limit_param_idx} OFFSET ${offset_param_idx}
    """


def get_assigned_sectors_query() -> str:
    """
    Query para obtener sectores asignados activos de un caso.
    
    Returns:
        str: Query SQL que retorna información de sectores asignados
    """
    return """
        SELECT DISTINCT
            d.acronym || '#' || s.acronym as sector_acronym,
            d.name as department_name,
            s.primary_color as sector_color
        FROM case_movements cm
        JOIN sectors s ON cm.assigned_sector_id = s.id
        JOIN departments d ON s.department_id = d.id
        WHERE cm.case_id = $1
          AND cm.is_active = true
          AND cm.assigned_sector_id IS NOT NULL
        ORDER BY sector_acronym
    """


def get_case_basic_info_query() -> str:
    """
    Query para obtener información básica de un caso.
    
    Returns:
        str: Query SQL con datos básicos del caso y template
    """
    return """
        SELECT
            c.id,
            c.case_number,
            c.reference,
            ct.type_name,
            ct.acronym as template_acronym
        FROM cases c
        JOIN case_templates ct ON c.case_template_id = ct.id
        WHERE c.id = $1
    """


def get_user_sectors_for_case_query() -> str:
    """
    Query para obtener sectores donde el usuario puede VER (para case detail).

    IMPORTANTE: Usa solo $1 (user_id) en ambos branches del UNION.
    Cuando esta query se embebe como subquery en can_user_view_case, $1 referencia
    el user_id del scope externo. NO usar $2 aqui porque en ese contexto $2 apunta
    a case_id (no a user_id), lo que causaria que el segundo branch nunca retorne filas.

    Returns:
        str: Query SQL que retorna sector_ids del usuario
    """
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
    """
    Query para obtener sector administrador del caso.
    Retorna el último movimiento cerrado de tipo creation/transfer.

    Returns:
        str: Query SQL con sector admin
    """
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
    """
    Query para obtener sectores asignados al caso.
    Retorna movimientos activos con sectores asignados (DISTINCT).

    Returns:
        str: Query SQL con sectores asignados
    """
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
    """
    Query para obtener plantillas de expedientes activas.
    
    Returns:
        str: Query SQL que retorna todas las plantillas activas
    """
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
        JOIN global_case_templates gct ON ct.global_case_template_id = gct.id
        LEFT JOIN departments d ON ct.filing_department_id = d.id
        WHERE ct.is_active = true
        AND gct.is_active = true
        ORDER BY ct.type_name
    """


def get_case_detail_query() -> str:
    """
    Query para obtener información básica de un caso/expediente.
    
    Returns:
        str: Query SQL que retorna datos del caso
    """
    return """
        SELECT
            c.id,
            c.case_number,
            c.reference,
            ct.type_name,
            ct.acronym as template_acronym
        FROM cases c
        JOIN case_templates ct ON c.case_template_id = ct.id
        WHERE c.id = $1
    """


def get_assigned_sectors_with_id_query() -> str:
    """
    Query para obtener sectores asignados con IDs.

    Returns:
        str: Query SQL que retorna sectores asignados con sector_id
    """
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


# ============================================================================
# CASE CREATION & VALIDATION QUERIES
# ============================================================================

def get_user_validation_query() -> str:
    """Query para validar usuario y obtener sus datos"""
    return """
        SELECT
            u.id as user_id,
            u.full_name,
            u.sector_id,
            s.department_id
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1 AND u.estado = 1
    """


def get_template_validation_query() -> str:
    """Query para validar template de expediente"""
    return """
        SELECT
            ct.id,
            ct.filing_department_id,
            ct.type_name,
            ct.acronym,
            ct.is_active
        FROM case_templates ct
        WHERE ct.id = $1
    """


def get_department_permissions_query() -> str:
    """Query para validar permisos de departamento del usuario"""
    return """
        SELECT 1
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1
        AND (
            s.department_id = $2
            OR EXISTS (
                SELECT 1 FROM user_departments
                WHERE user_id = u.id AND department_id = $3
            )
        )
    """


def get_sector_validation_query() -> str:
    """Query para validar sector propietario"""
    return """
        SELECT id as sector_id, acronym as name
        FROM sectors
        WHERE id = $1 AND is_active = true
    """

def get_case_movements_query() -> str:
    """
    Query para obtener el historial de movimientos de un expediente.
    Incluye información de usuarios, sectores involucrados y documento de soporte.
    """
    return """
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
            cs.id as creator_sector_id,
            d1.acronym || '#' || cs.acronym as creator_sector_name,
            adm.id as admin_sector_id,
            d2.acronym || '#' || adm.acronym as admin_sector_name,
            asn.id as assigned_sector_id,
            d3.acronym || '#' || asn.acronym as assigned_sector_name,
            cm.supporting_document_id,
            sd.official_number as supporting_document_number,
            sd.reference as supporting_document_reference,
            sd.resume as supporting_document_resume
        FROM case_movements cm
        LEFT JOIN users u ON cm.user_id = u.id
        LEFT JOIN sectors cs ON cm.creator_sector_id = cs.id
        LEFT JOIN departments d1 ON cs.department_id = d1.id
        LEFT JOIN sectors adm ON cm.admin_sector_id = adm.id
        LEFT JOIN departments d2 ON adm.department_id = d2.id
        LEFT JOIN sectors asn ON cm.assigned_sector_id = asn.id
        LEFT JOIN departments d3 ON asn.department_id = d3.id
        LEFT JOIN official_documents sd ON cm.supporting_document_id = sd.id AND sd.signed_at IS NOT NULL
        WHERE cm.case_id = $1
        ORDER BY cm.created_at DESC
    """

def get_official_documents_query() -> str:
    """
    Query para obtener documentos oficiales vinculados a un expediente.
    """
    return """
        SELECT
            cod.id,
            cod.order_number,
            cod.linking_date,
            cod.is_active,
            od.id as document_id,
            od.official_number,
            od.reference,
            od.short_resume,
            u.full_name as linked_by,
            COALESCE(d.acronym || '#' || s.acronym, '') as linked_sector
        FROM case_official_documents cod
        JOIN official_documents od ON cod.official_document_id = od.id AND od.signed_at IS NOT NULL
        LEFT JOIN users u ON cod.linking_user_id = u.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE cod.case_id = $1
        ORDER BY cod.order_number ASC
    """

def get_proposed_documents_query() -> str:
    """
    Query para obtener documentos propuestos para un expediente.
    """
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
    """
    Query para obtener un documento propuesto específico por ID.
    """
    return """
        SELECT cpd.id, cpd.case_id, cpd.document_draft_id, cpd.is_active,
               dd.reference, dd.status, dd.document_number
        FROM case_proposed_documents cpd
        JOIN document_draft dd ON cpd.document_draft_id = dd.id
        WHERE cpd.id = $1 AND cpd.case_id = $2
    """


def deactivate_proposed_document_query() -> str:
    """
    Query para desactivar un documento propuesto.
    """
    return """
        UPDATE case_proposed_documents SET is_active = false WHERE id = $1 RETURNING id
    """


def get_case_permissions_data_query() -> str:
    """
    Query para obtener datos del caso necesarios para calcular permisos del usuario.
    """
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
    """
    Query para obtener información del sector y departamento del usuario.
    """
    return """
        SELECT u.id as user_id, s.id as sector_id, s.department_id
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1
    """

def get_case_number_query() -> str:
    """
    Query para obtener el número de un expediente.
    """
    return """
        SELECT case_number, ai_summary, ai_summary_updated_at, short_ai_summary
        FROM cases
        WHERE id = $1
    """


# ============================================================================
# COVER CREATOR QUERIES
# ============================================================================

def get_document_type_id_query() -> str:
    """Query para obtener document_type_id de CAEX"""
    return """
        SELECT id as document_type_id
        FROM document_types
        WHERE acronym = $1
    """


def get_document_reference_query() -> str:
    """Query para obtener reference del documento"""
    return """
        SELECT reference
        FROM document_draft
        WHERE id = $1
    """


def get_document_signers_query() -> str:
    """Query para obtener firmantes del documento"""
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
    """Query para actualizar contenido del documento"""
    return """
        UPDATE document_draft
        SET content = $1::jsonb,
            last_modified_at = CURRENT_TIMESTAMP
        WHERE id = $2
    """


def delete_document_chunks_by_official_query() -> str:
    """Query para eliminar chunks de un documento oficial (antes del rollback de official_documents por FK)"""
    return "DELETE FROM document_chunks WHERE official_document_id = $1"


def delete_official_document_query() -> str:
    """Query para eliminar documento oficial (rollback)"""
    return """
        DELETE FROM official_documents
        WHERE id = $1
    """


def update_document_status_signed_query() -> str:
    """Query para actualizar estado de documento a 'signed'"""
    return """
        UPDATE document_draft
        SET status = 'signed'
        WHERE id = $1
    """


def update_signer_status_signed_query() -> str:
    """Query para actualizar estado de firmante a 'signed'"""
    return """
        UPDATE document_signers
        SET status = 'signed', signed_at = CURRENT_TIMESTAMP
        WHERE document_id = $1 AND user_id = $2
    """


def insert_case_official_document_query() -> str:
    """Query para vincular carátula al expediente"""
    return """
        INSERT INTO case_official_documents (
            case_id, official_document_id, linking_user_id, order_number,
            linking_date, is_active
        ) VALUES ($1, $2, $3, 1, CURRENT_TIMESTAMP, true)
    """


def delete_document_signers_query() -> str:
    """Query para eliminar firmantes del documento (cleanup)"""
    return "DELETE FROM document_signers WHERE document_id = $1"


def delete_document_draft_query() -> str:
    """Query para eliminar documento borrador (cleanup)"""
    return "DELETE FROM document_draft WHERE id = $1"


# ============================================================================
# TRANSFER & ASSIGNMENT QUERIES
# ============================================================================

def get_user_with_sector_query() -> str:
    """Query para obtener datos completos del usuario con su sector"""
    return """
        SELECT u.id as user_id, u.full_name, u.sector_id, s.department_id
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1
    """

def get_case_with_target_sector_query() -> str:
    """Query para obtener expediente con información del sector destino"""
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
    """Query para obtener el sector administrador actual del expediente"""
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
    """Query para verificar que el sector destino existe y está activo"""
    return """
        SELECT s.id as sector_id, s.acronym, s.is_active,
               d.name as department_name, d.acronym as department_acronym
        FROM sectors s
        JOIN departments d ON s.department_id = d.id
        WHERE s.id = $1 AND s.is_active = true
    """

def get_assigned_user_query() -> str:
    """Query para verificar que el usuario asignado pertenece al sector destino"""
    return """
        SELECT u.id as user_id, u.full_name, u.sector_id
        FROM users u
        WHERE u.id = $1 AND u.sector_id = $2
    """

def update_case_ownership_query() -> str:
    """Query para actualizar el propietario del expediente"""
    return """
        UPDATE cases
        SET owner_sector_id = $1, owner_department_id = $2
        WHERE id = $3
    """

def close_movement_query() -> str:
    """Query para cerrar un movimiento"""
    return """
        UPDATE case_movements
        SET closed_at = NOW(), closing_reason = $1, closed_by = $2, is_active = false
        WHERE id = $3
    """

def get_movement_for_closing_query() -> str:
    """Query para obtener movimiento con validación de expediente"""
    return """
        SELECT id, type, is_active, closed_at, assigned_sector_id, supporting_document_id
        FROM case_movements
        WHERE id = $1 AND case_id = $2
    """

def get_available_sectors_for_transfer_query() -> str:
    """Query para obtener sectores disponibles para transferencia

    En multi-tenant, el schema actual YA filtra por municipio.
    Todos los sectores en el schema pertenecen al mismo municipio.
    """
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
    """Query para obtener usuarios activos de un sector"""
    return """
        SELECT u.id as user_id, u.full_name
        FROM users u
        WHERE u.sector_id = $1
        AND u.estado = 1
        ORDER BY u.full_name
    """


def check_duplicate_assignment_query() -> str:
    """Query para verificar si ya existe una asignación activa al sector"""
    return """
        SELECT id FROM case_movements
        WHERE case_id = $1
          AND assigned_sector_id = $2
          AND is_active = true
          AND type = 'assignment'
        LIMIT 1
    """


def get_cases_summary_query() -> str:
    """Query para estadísticas de expedientes del usuario.

    Parámetros: (user_id, user_id, user_id)
    - Primero para user_sector_permissions
    - Segundo para users.sector_id
    - Tercero para created_by_user_id
    """
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
