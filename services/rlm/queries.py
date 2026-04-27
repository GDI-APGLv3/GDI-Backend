"""
Queries SQL para el módulo RLM.
Centraliza todas las consultas SQL usadas por los servicios de RLM.
"""


# ============================================================================
# QUERIES DE REGISTROS (registry_families)
# ============================================================================

def get_registries_query() -> str:
    """Lista todos los registros disponibles con conteo de legajos."""
    return """
        SELECT
            rf.id,
            rf.code,
            rf.name,
            rf.description,
            rf.states,
            rf.is_active,
            COUNT(r.id) as record_count
        FROM registry_families rf
        LEFT JOIN records r ON r.registry_family_id = rf.id
        WHERE rf.is_active = true
        GROUP BY rf.id
        ORDER BY rf.code
    """


def get_registry_detail_query() -> str:
    """Detalle de un registro con su data_schema."""
    return """
        SELECT
            rf.id,
            rf.code,
            rf.name,
            rf.description,
            rf.data_schema,
            rf.states,
            rf.is_active,
            COUNT(r.id) as record_count
        FROM registry_families rf
        LEFT JOIN records r ON r.registry_family_id = rf.id
        WHERE rf.id = %s AND rf.is_active = true
        GROUP BY rf.id
    """


def get_registry_by_code_query() -> str:
    """Obtiene un registro por su código."""
    return """
        SELECT id, code, name, description, data_schema,
               states, is_active
        FROM registry_families
        WHERE code = %s AND is_active = true
    """


# ============================================================================
# QUERIES DE LEGAJOS (records)
# ============================================================================

def get_records_list_query(where_clauses: list[str]) -> str:
    """Lista legajos con filtros dinámicos y paginación."""
    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    return f"""
        SELECT
            r.id,
            r.record_number,
            r.display_name,
            r.state,
            r.resume,
            r.next_expiration,
            r.created_at,
            r.updated_at,
            rf.code as registry_code,
            rf.name as registry_name,
            u.full_name as created_by_name,
            s.acronym as created_by_sector,
            d.acronym as created_by_department
        FROM records r
        JOIN registry_families rf ON r.registry_family_id = rf.id
        JOIN users u ON r.created_by_user_id = u.id
        LEFT JOIN sectors s ON r.created_by_sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE {where_sql}
        ORDER BY r.created_at DESC
        LIMIT %s OFFSET %s
    """


def get_records_count_query(where_clauses: list[str]) -> str:
    """Cuenta total de legajos con filtros dinámicos."""
    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    return f"""
        SELECT COUNT(*) as total
        FROM records r
        JOIN registry_families rf ON r.registry_family_id = rf.id
        WHERE {where_sql}
    """


def get_record_family_query() -> str:
    """Obtiene el registry_family_id de un legajo."""
    return "SELECT registry_family_id FROM records WHERE id = %s"


def get_record_detail_query() -> str:
    """Detalle completo de un legajo."""
    return """
        SELECT
            r.id,
            r.record_number,
            r.display_name,
            r.state,
            r.data,
            r.resume,
            r.next_expiration,
            r.created_at,
            r.updated_at,
            r.registry_family_id,
            rf.code as registry_code,
            rf.name as registry_name,
            rf.data_schema,
            rf.states,
            u.id as created_by_user_id,
            u.full_name as created_by_name,
            s.id as created_by_sector_id,
            s.acronym as created_by_sector,
            d.acronym as created_by_department
        FROM records r
        JOIN registry_families rf ON r.registry_family_id = rf.id
        JOIN users u ON r.created_by_user_id = u.id
        LEFT JOIN sectors s ON r.created_by_sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE r.id = %s
    """


def insert_record_query() -> str:
    """Inserta un nuevo legajo."""
    return """
        INSERT INTO records (
            registry_family_id, record_number, state, data,
            created_by_user_id, created_by_sector_id, next_expiration, display_name
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, record_number, state, created_at
    """


def update_record_query(update_state: bool = False, update_display_name: bool = False) -> str:
    """Actualiza campos del legajo dinámicamente."""
    set_clauses = []
    if update_state:
        set_clauses.append("state = %s")
    if update_display_name:
        set_clauses.append("display_name = %s")
    set_clauses.append("updated_at = now()")
    set_sql = ", ".join(set_clauses)
    return f"""
        UPDATE records
        SET {set_sql}
        WHERE id = %s
        RETURNING id, state, display_name, updated_at
    """


def update_record_data_query() -> str:
    """Actualiza el JSONB data de un legajo."""
    return """
        UPDATE records
        SET data = %s, next_expiration = %s, updated_at = now()
        WHERE id = %s
        RETURNING id, data, next_expiration, updated_at
    """


def get_record_detail_for_update_query() -> str:
    """Detalle completo de un legajo con FOR UPDATE (row lock).

    Usa SELECT ... FOR UPDATE OF r para lockear solo la fila del record,
    evitando race conditions en actualizaciones concurrentes de campos.
    """
    return """
        SELECT
            r.id,
            r.record_number,
            r.state,
            r.data,
            r.next_expiration,
            r.created_at,
            r.updated_at,
            r.registry_family_id,
            rf.code as registry_code,
            rf.name as registry_name,
            rf.data_schema,
            rf.states,
            u.id as created_by_user_id,
            u.full_name as created_by_name,
            s.id as created_by_sector_id,
            s.acronym as created_by_sector,
            d.acronym as created_by_department
        FROM records r
        JOIN registry_families rf ON r.registry_family_id = rf.id
        JOIN users u ON r.created_by_user_id = u.id
        LEFT JOIN sectors s ON r.created_by_sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE r.id = %s
        FOR UPDATE OF r
    """


def update_record_field_atomic_query() -> str:
    """Actualiza un campo individual del JSONB data usando jsonb_set (atomico).

    Params: (jsonb_path array, field_value jsonb, next_expiration, record_id)
    Ejemplo de path: '{nombre_completo}' para data->'nombre_completo'
    """
    return """
        UPDATE records
        SET data = jsonb_set(COALESCE(data, '{}'::jsonb), %s, %s::jsonb),
            next_expiration = %s,
            updated_at = now()
        WHERE id = %s
        RETURNING id, data, next_expiration, updated_at
    """


# ============================================================================
# QUERIES DE PERMISOS
# ============================================================================

def get_sector_permissions_query() -> str:
    """Obtiene los permisos de un sector sobre un registro."""
    return """
        SELECT can_create, can_edit, can_view, can_verify
        FROM registry_family_permissions
        WHERE registry_family_id = %s AND sector_id = %s
    """


def get_all_permissions_for_sectors_query(sector_ids: list[str]) -> tuple[str, tuple]:
    """
    Obtiene TODOS los permisos de múltiples sectores sobre TODAS las familias
    en un solo query. Usado por get_bulk_permissions() para evitar N+1.

    Args:
        sector_ids: Lista de sector_ids (UUIDs como strings)

    Returns:
        Tupla (query_sql, params_tuple)
    """
    placeholders = ", ".join(["%s"] * len(sector_ids))
    query = f"""
        SELECT registry_family_id, can_create, can_edit, can_view, can_verify
        FROM registry_family_permissions
        WHERE sector_id IN ({placeholders})
    """
    return query, tuple(sector_ids)


# ============================================================================
# QUERIES DE HISTORIAL
# ============================================================================

def insert_history_query() -> str:
    """Inserta una entrada en el historial."""
    return """
        INSERT INTO record_history (
            record_id, action, field_name, before_value, after_value,
            user_id, sector_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at
    """


def get_record_history_query() -> str:
    """Historial de cambios de un legajo."""
    return """
        SELECT
            rh.id, rh.action, rh.field_name,
            rh.before_value, rh.after_value,
            rh.user_id,
            u.full_name as user_name,
            s.acronym as sector_name,
            rh.created_at
        FROM record_history rh
        LEFT JOIN users u ON rh.user_id = u.id
        LEFT JOIN sectors s ON rh.sector_id = s.id
        WHERE rh.record_id = %s
        ORDER BY rh.created_at DESC
        LIMIT %s OFFSET %s
    """


def get_record_history_count_query() -> str:
    """Cuenta total de entradas en el historial."""
    return """
        SELECT COUNT(*) as total
        FROM record_history
        WHERE record_id = %s
    """


# ============================================================================
# QUERIES DE VINCULACIONES (documents, cases)
# ============================================================================

def get_record_documents_query() -> str:
    """Documentos vinculados a un legajo con paginacion."""
    return """
        SELECT
            rdl.id as link_id,
            rdl.document_id,
            rdl.field_name,
            rdl.notes,
            rdl.linked_at as created_at,
            COALESCE(od.official_number, '') as official_number,
            COALESCE(od.reference, dd.reference) as reference,
            COALESCE(dt.acronym, '') as document_type,
            COALESCE(od.resume, '') as resume
        FROM record_document_links rdl
        LEFT JOIN official_documents od ON rdl.document_id = od.id AND od.signed_at IS NOT NULL
        LEFT JOIN document_draft dd ON rdl.document_id = dd.id
        LEFT JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE rdl.record_id = %s
        ORDER BY rdl.linked_at DESC
        LIMIT %s OFFSET %s
    """


def get_record_documents_count_query() -> str:
    """Cuenta total de documentos vinculados a un legajo."""
    return """
        SELECT COUNT(*) as total
        FROM record_document_links
        WHERE record_id = %s
    """


def insert_document_link_query() -> str:
    """Vincula un documento a un legajo."""
    return """
        INSERT INTO record_document_links (record_id, document_id, field_name, notes, linked_by_user_id)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, linked_at as created_at
    """


def delete_document_link_query() -> str:
    """Desvincula un documento de un legajo."""
    return """
        DELETE FROM record_document_links
        WHERE id = %s AND record_id = %s
        RETURNING id
    """


def get_record_cases_query() -> str:
    """Expedientes vinculados a un legajo con paginacion."""
    return """
        SELECT
            rcl.id as link_id,
            rcl.case_id,
            rcl.notes,
            rcl.linked_at as created_at,
            c.case_number,
            c.reference as case_reference,
            COALESCE(c.short_ai_summary, '') as short_ai_summary
        FROM record_case_links rcl
        LEFT JOIN cases c ON rcl.case_id = c.id
        WHERE rcl.record_id = %s
        ORDER BY rcl.linked_at DESC
        LIMIT %s OFFSET %s
    """


def get_record_cases_count_query() -> str:
    """Cuenta total de expedientes vinculados a un legajo."""
    return """
        SELECT COUNT(*) as total
        FROM record_case_links
        WHERE record_id = %s
    """


def insert_case_link_query() -> str:
    """Vincula un expediente a un legajo."""
    return """
        INSERT INTO record_case_links (record_id, case_id, notes, linked_by_user_id)
        VALUES (%s, %s, %s, %s)
        RETURNING id, linked_at as created_at
    """


def delete_case_link_query() -> str:
    """Desvincula un expediente de un legajo."""
    return """
        DELETE FROM record_case_links
        WHERE id = %s AND record_id = %s
        RETURNING id
    """


# ============================================================================
# QUERIES DE RELACIONES ENTRE LEGAJOS
# ============================================================================

def get_record_relations_query() -> str:
    """Relaciones de un legajo con otros legajos con paginacion."""
    return """
        SELECT * FROM (
            SELECT
                rr.id as relation_id,
                rr.relation_type,
                rr.notes,
                rr.created_at,
                r.id as related_record_id,
                r.record_number as related_record_number,
                r.display_name as related_display_name,
                r.state as related_record_state,
                rf.code as related_registry_code,
                rf.name as related_registry_name,
                COALESCE(r.resume, '') as related_resume
            FROM record_relations rr
            JOIN records r ON rr.target_record_id = r.id
            JOIN registry_families rf ON r.registry_family_id = rf.id
            WHERE rr.source_record_id = %s
            UNION ALL
            SELECT
                rr.id as relation_id,
                rr.relation_type,
                rr.notes,
                rr.created_at,
                r.id as related_record_id,
                r.record_number as related_record_number,
                r.display_name as related_display_name,
                r.state as related_record_state,
                rf.code as related_registry_code,
                rf.name as related_registry_name,
                COALESCE(r.resume, '') as related_resume
            FROM record_relations rr
            JOIN records r ON rr.source_record_id = r.id
            JOIN registry_families rf ON r.registry_family_id = rf.id
            WHERE rr.target_record_id = %s
        ) sub
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """


def get_record_relations_count_query() -> str:
    """Cuenta total de relaciones de un legajo."""
    return """
        SELECT COUNT(*) as total FROM (
            SELECT rr.id FROM record_relations rr WHERE rr.source_record_id = %s
            UNION ALL
            SELECT rr.id FROM record_relations rr WHERE rr.target_record_id = %s
        ) sub
    """


def insert_relation_query() -> str:
    """Crea una relación entre dos legajos."""
    return """
        INSERT INTO record_relations (source_record_id, target_record_id, relation_type, notes, created_by_user_id)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, created_at
    """


def delete_relation_query() -> str:
    """Elimina una relación entre legajos."""
    return """
        DELETE FROM record_relations
        WHERE id = %s AND (source_record_id = %s OR target_record_id = %s)
        RETURNING id
    """


# ============================================================================
# QUERIES DE NUMERACIÓN
# ============================================================================

def get_next_record_sequence_query() -> str:
    """Obtiene la siguiente secuencia para numeración de legajos."""
    return """
        SELECT COALESCE(MAX(
            CAST(SUBSTRING(record_number FROM 'RLM-\\d{4}-(\\d+)-') AS INTEGER)
        ), 0) + 1 as next_sequence
        FROM records
        WHERE record_number LIKE %s
    """


def get_user_sector_info_query() -> str:
    """Obtiene información del sector del usuario."""
    return """
        SELECT
            u.id as user_id,
            u.full_name,
            u.sector_id,
            s.acronym as sector_acronym,
            d.acronym as department_acronym
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.id = %s
    """


def autocomplete_records_query(sector_ids: list[str]) -> str:
    """Autocomplete de legajos por número. Respeta permisos can_view."""
    placeholders = ", ".join(["%s"] * len(sector_ids))
    query = f"""
        SELECT
            r.id as record_id,
            r.record_number,
            r.display_name,
            rf.code as family_code,
            r.state
        FROM records r
        JOIN registry_families rf ON r.registry_family_id = rf.id
        JOIN registry_family_permissions rfp ON rf.id = rfp.registry_family_id
        WHERE rfp.sector_id IN ({placeholders})
          AND rfp.can_view = true
          AND (r.record_number ILIKE %s OR r.display_name ILIKE %s)
        GROUP BY r.id, r.record_number, r.display_name, rf.code, r.state
        ORDER BY r.record_number DESC
        LIMIT %s
    """
    return query
