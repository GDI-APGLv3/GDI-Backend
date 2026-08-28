


def get_registries_query() -> str:
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
        WHERE rf.id = $1 AND rf.is_active = true
        GROUP BY rf.id
    """


def get_registry_by_code_query() -> str:
    return """
        SELECT id, code, name, description, data_schema,
               states, is_active
        FROM registry_families
        WHERE code = $1 AND is_active = true
    """


def get_record_family_query() -> str:
    return "SELECT registry_family_id FROM records WHERE id = $1"


def get_record_detail_query() -> str:
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
        WHERE r.id = $1
    """


def insert_record_query() -> str:
    return """
        INSERT INTO records (
            registry_family_id, record_number, state, data,
            created_by_user_id, created_by_sector_id, next_expiration, display_name
        ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
        RETURNING id, record_number, state, created_at
    """


def update_record_query(update_state: bool = False, update_display_name: bool = False) -> str:
    set_clauses = []
    param_idx = 1
    if update_state:
        set_clauses.append(f"state = ${param_idx}")
        param_idx += 1
    if update_display_name:
        set_clauses.append(f"display_name = ${param_idx}")
        param_idx += 1
    set_clauses.append("updated_at = now()")
    set_sql = ", ".join(set_clauses)
    return f"""
        UPDATE records
        SET {set_sql}
        WHERE id = ${param_idx}
        RETURNING id, state, display_name, updated_at
    """


def get_record_detail_for_update_query() -> str:
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
        WHERE r.id = $1
        FOR UPDATE OF r
    """


def update_record_field_atomic_query() -> str:
    return """
        UPDATE records
        SET data = jsonb_set(COALESCE(data, '{}'::jsonb), $1::text[], $2::jsonb),
            next_expiration = $3,
            updated_at = now()
        WHERE id = $4
        RETURNING id, data, next_expiration, updated_at
    """


def get_sector_permissions_query() -> str:
    return """
        SELECT can_create, can_edit, can_view, can_verify
        FROM registry_family_permissions
        WHERE registry_family_id = $1 AND sector_id = $2
    """


def get_all_permissions_for_sectors_query(sector_ids: list[str]) -> tuple[str, tuple]:
    placeholders = ", ".join([f"${i+1}" for i in range(len(sector_ids))])
    query = f"""
        SELECT registry_family_id, can_create, can_edit, can_view, can_verify
        FROM registry_family_permissions
        WHERE sector_id IN ({placeholders})
    """
    return query, tuple(sector_ids)


def insert_history_query() -> str:
    return """
        INSERT INTO record_history (
            record_id, action, field_name, before_value, after_value,
            user_id, sector_id
        ) VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7)
        RETURNING id, created_at
    """


def get_record_history_query() -> str:
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
        WHERE rh.record_id = $1
        ORDER BY rh.created_at DESC
        LIMIT $2 OFFSET $3
    """


def get_record_history_count_query() -> str:
    return """
        SELECT COUNT(*) as total
        FROM record_history
        WHERE record_id = $1
    """


def get_record_documents_query() -> str:
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
            COALESCE(od.resume, '') as resume,
            COALESCE(dt.is_reserved, false) as is_reserved,
            dt.visibility as document_type_visibility
        FROM record_document_links rdl
        LEFT JOIN official_documents od ON rdl.document_id = od.id AND od.signed_at IS NOT NULL
        LEFT JOIN document_draft dd ON rdl.document_id = dd.id
        LEFT JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE rdl.record_id = $1
        ORDER BY rdl.linked_at DESC
        LIMIT $2 OFFSET $3
    """


def get_record_documents_count_query() -> str:
    return """
        SELECT COUNT(*) as total
        FROM record_document_links
        WHERE record_id = $1
    """


def insert_document_link_query() -> str:
    return """
        INSERT INTO record_document_links (record_id, document_id, field_name, notes, linked_by_user_id)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, linked_at as created_at
    """


def delete_document_link_query() -> str:
    return """
        DELETE FROM record_document_links
        WHERE id = $1 AND record_id = $2
        RETURNING id
    """


def get_record_cases_query() -> str:
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
        WHERE rcl.record_id = $1
        ORDER BY rcl.linked_at DESC
        LIMIT $2 OFFSET $3
    """


def get_record_cases_count_query() -> str:
    return """
        SELECT COUNT(*) as total
        FROM record_case_links
        WHERE record_id = $1
    """


def insert_case_link_query() -> str:
    return """
        INSERT INTO record_case_links (record_id, case_id, notes, linked_by_user_id)
        VALUES ($1, $2, $3, $4)
        RETURNING id, linked_at as created_at
    """


def delete_case_link_query() -> str:
    return """
        DELETE FROM record_case_links
        WHERE id = $1 AND record_id = $2
        RETURNING id
    """


def get_record_relations_query() -> str:
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
            WHERE rr.source_record_id = $1
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
            WHERE rr.target_record_id = $2
        ) sub
        ORDER BY created_at DESC
        LIMIT $3 OFFSET $4
    """


def get_record_relations_count_query() -> str:
    return """
        SELECT COUNT(*) as total FROM (
            SELECT rr.id FROM record_relations rr WHERE rr.source_record_id = $1
            UNION ALL
            SELECT rr.id FROM record_relations rr WHERE rr.target_record_id = $2
        ) sub
    """


def insert_relation_query() -> str:
    return """
        INSERT INTO record_relations (source_record_id, target_record_id, relation_type, notes, created_by_user_id)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, created_at
    """


def delete_relation_query() -> str:
    return """
        DELETE FROM record_relations
        WHERE id = $1 AND (source_record_id = $2 OR target_record_id = $3)
        RETURNING id
    """


def get_next_record_sequence_query() -> str:
    return r"""
        SELECT COALESCE(MAX(
            CAST(SUBSTRING(record_number FROM 'RLM-\d{4}-(\d+)-') AS INTEGER)
        ), 0) + 1 as next_sequence
        FROM records
        WHERE record_number LIKE $1
    """


def get_user_sector_info_query() -> str:
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
        WHERE u.id = $1
    """


def autocomplete_records_query(sector_ids: list[str]) -> str:
    placeholders = ", ".join([f"${i+1}" for i in range(len(sector_ids))])
    n = len(sector_ids)
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
          AND (r.record_number ILIKE ${n+1} OR r.display_name ILIKE ${n+2})
        GROUP BY r.id, r.record_number, r.display_name, rf.code, r.state
        ORDER BY r.record_number DESC
        LIMIT ${n+3}
    """
    return query
