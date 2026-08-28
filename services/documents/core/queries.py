
from config.constants import EXCLUDED_DOCUMENT_TYPES


def get_excluded_types_clause() -> str:
    types_list = ', '.join(f"'{t}'" for t in EXCLUDED_DOCUMENT_TYPES)
    return f"AND dt.acronym NOT IN ({types_list})"


def autocomplete_official_documents_query() -> str:
    excluded_clause = get_excluded_types_clause()

    query = f"""
        SELECT DISTINCT
            o.id as document_id,
            o.official_number,
            o.reference
        FROM official_documents o
        JOIN document_types dt ON o.document_type_id = dt.id
        WHERE o.official_number ILIKE $1
          AND o.signed_at IS NOT NULL
          {excluded_clause}
        ORDER BY o.official_number
        LIMIT $2 OFFSET $3
    """

    return query


def get_document_type_query() -> str:
    return """
        SELECT id AS document_type_id, name, type AS base_type
        FROM document_types
        WHERE acronym = $1
    """


def insert_document_draft_query() -> str:
    return """
        INSERT INTO document_draft (
            id, document_type_id, reference, created_by,
            last_modified_at, status
        ) VALUES (
            $1, $2, $3, $4,
            CURRENT_TIMESTAMP, 'draft'
        )
        RETURNING id AS document_id, last_modified_at
    """


def insert_document_signer_query() -> str:
    return """
        INSERT INTO document_signers (
            document_id,
            user_id,
            signing_order,
            is_numerator
        ) VALUES ($1, $2, $3, $4)
    """


def insert_document_draft_citizen_query() -> str:
    return """
        INSERT INTO document_draft (
            id, document_type_id, reference, created_by_citizen,
            last_modified_at, status
        ) VALUES (
            $1, $2, $3, $4,
            CURRENT_TIMESTAMP, 'draft'
        )
        RETURNING id AS document_id, last_modified_at
    """


def insert_document_signer_citizen_query() -> str:
    return """
        INSERT INTO document_signers (
            document_id,
            citizen_id,
            signing_order,
            is_numerator
        ) VALUES ($1, $2, $3, $4)
    """


def get_document_details_for_editing_query() -> str:
    return """
        SELECT
            d.id,
            d.reference,
            d.content,
            d.status,
            d.created_by as creator_id,
            d.document_type_id,
            d.last_modified_at,
            d.resume,
            d.short_resume,
            dt.name as document_type_name,
            dt.acronym as document_type_acronym,
            dt.type as document_type_source,
            EXISTS(SELECT 1 FROM document_type_fields dtf WHERE dtf.document_type_id = dt.id) AS has_fields,
            dt.accepts_embedded_files,
            dt.visibility as document_type_visibility,
            u.full_name as creator_name,
            u.profile_picture_url as creator_profile_picture_url,
            dep.acronym as creator_department_acronym,
            sec.acronym as creator_sector_acronym
        FROM document_draft d
            LEFT JOIN document_types dt ON d.document_type_id = dt.id
            LEFT JOIN users u ON d.created_by = u.id
            LEFT JOIN sectors sec ON u.sector_id = sec.id
            LEFT JOIN departments dep ON sec.department_id = dep.id
        WHERE d.id = $1
    """


def get_document_signers_query() -> str:
    return """
        SELECT
            ds.user_id,
            ds.signing_order,
            ds.is_numerator,
            COALESCE(u.full_name, '') as user_name,
            u.email,
            u.profile_picture_url,
            cs.name as seal_name,
            dep.acronym as department_acronym,
            sec.acronym as sector_acronym,
            sec.primary_color as sector_color
        FROM document_signers ds
        JOIN users u ON ds.user_id = u.id
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors sec ON u.sector_id = sec.id
        LEFT JOIN departments dep ON sec.department_id = dep.id
        WHERE ds.document_id = $1
        ORDER BY ds.signing_order
    """


def get_document_rejection_info_query() -> str:
    return """
        SELECT
            r.reason,
            r.rejected_at as created_at,
            r.rejected_by,
            u.full_name as rejected_by_name
        FROM document_rejections r
        JOIN users u ON r.rejected_by = u.id
        WHERE r.document_id = $1
        ORDER BY r.rejected_at DESC
        LIMIT 1
    """


def get_document_status_query() -> str:
    return "SELECT id AS document_id, status FROM document_draft WHERE id = $1"


def delete_document_signers_query() -> str:
    return "DELETE FROM document_signers WHERE document_id = $1"


def insert_document_signer_ordered_query() -> str:
    return "INSERT INTO document_signers (document_id, user_id, signing_order, is_numerator) VALUES ($1, $2, $3, $4)"


def get_official_document_info_query() -> str:
    return """
        SELECT
            od.official_number,
            od.id,
            od.pdf_location,
            dd.status
        FROM official_documents od
        INNER JOIN document_draft dd ON od.id = dd.id
        WHERE od.id = $1
          AND od.signed_at IS NOT NULL
    """

def get_all_display_states_query() -> str:
    return """
        SELECT display_state_name as display_state
        FROM document_display_states
        ORDER BY display_state_code ASC
    """

def get_display_state_by_code_query() -> str:
    return """
        SELECT display_state_name
        FROM document_display_states
        WHERE display_state_code = $1
    """

def get_all_state_mappings_query() -> str:
    return """
        SELECT display_state_code, display_state_name
        FROM document_display_states
        ORDER BY display_state_code ASC
    """

def get_all_document_types_query() -> str:
    return """
        SELECT
            dt.name,
            dt.acronym,
            dt.type,
            dt.trust,
            dt.description,
            dt.is_reserved,
            dt.visibility,
            EXISTS(
                SELECT 1 FROM document_type_fields dtf
                WHERE dtf.document_type_id = dt.id
            ) AS has_fields,
            (
                SELECT json_agg(json_build_object(
                    'department_acronym', d.acronym,
                    'sector_acronym', s.acronym
                ) ORDER BY d.acronym, s.acronym)
                FROM enabled_document_types_by_sector edts
                JOIN sectors s ON s.id = edts.sector_id
                JOIN departments d ON d.id = s.department_id
                WHERE edts.document_type_id = dt.id
                  AND s.is_active = true
                  AND d.is_active = true
            ) as restricted_sectors
        FROM document_types dt
        WHERE dt.is_active = true
        ORDER BY dt.name ASC
    """

def get_preview_document_info_query() -> str:
    return """
        SELECT
            d.id AS document_id,
            d.reference,
            d.content,
            d.created_by,
            d.id AS document_generate_id,
            d.document_type_id,
            d.status,
            dt.acronym as type_acronym,
            dt.name as type_name,
            dt.type as source_type,
            EXISTS(SELECT 1 FROM document_type_fields dtf WHERE dtf.document_type_id = dt.id) AS has_fields,
            dt.visibility as type_visibility
        FROM document_draft d
        LEFT JOIN document_types dt ON d.document_type_id = dt.id
        WHERE d.id = $1
    """


def get_document_for_deletion_query() -> str:
    return """
        SELECT id, status, created_by, is_deleted, reference
        FROM document_draft
        WHERE id = $1
    """

def unlink_document_from_cases_query() -> str:
    return """
        DELETE FROM case_proposed_documents
        WHERE document_draft_id = $1
    """

def soft_delete_document_query() -> str:
    return """
        UPDATE document_draft
        SET is_deleted = true, last_modified_at = CURRENT_TIMESTAMP
        WHERE id = $1
    """

def get_user_info_for_deletion_query() -> str:
    return """
        SELECT full_name, email
        FROM users
        WHERE id = $1
    """


def get_document_info_for_rejection_query() -> str:
    return """
        SELECT d.id as document_id, d.reference, d.status, d.created_by,
               dt.name as document_type_name, dt.acronym as document_type_acronym,
               COALESCE(creator.full_name, creator_citizen.full_name) as creator_name
        FROM document_draft d
        JOIN document_types dt ON d.document_type_id = dt.id
        LEFT JOIN users creator ON d.created_by = creator.id
        LEFT JOIN citizens creator_citizen ON d.created_by_citizen = creator_citizen.id
        WHERE d.id = $1
    """

def check_document_is_official_query() -> str:
    return """
        SELECT COUNT(*) as count
        FROM official_documents
        WHERE id = $1
          AND (signed_at IS NOT NULL OR reservation_status IN ('CONFIRMING', 'CONFIRMED'))
    """

def get_document_generate_id_query() -> str:
    return "SELECT id as document_generate_id FROM document_draft WHERE id = $1"

def update_document_to_rejected_query() -> str:
    return """
        UPDATE document_draft
        SET status = 'rejected',
            last_modified_at = CURRENT_TIMESTAMP,
            sent_to_sign_at = NULL,
            sent_by = NULL,
            resume = NULL
        WHERE id = $1
          AND status IN ('draft', 'sent_to_sign')
        RETURNING id
    """

def insert_rejection_record_query() -> str:
    return """
        INSERT INTO document_rejections (id, document_id, rejected_by, reason)
        VALUES ($1, $2, $3, $4)
    """

def update_signers_to_rejected_query() -> str:
    return """
        UPDATE document_signers
        SET status = 'rejected'
        WHERE document_id = $1 AND signed_at IS NULL
    """

def get_rejector_info_query() -> str:
    return """
        SELECT full_name, email
        FROM users WHERE id = $1
    """

def check_user_is_signer_query() -> str:
    return """
        SELECT user_id, signing_order, is_numerator
        FROM document_signers
        WHERE document_id = $1 AND user_id = $2
    """


def update_document_reference_query() -> str:
    return """
        UPDATE document_draft
        SET reference = $1, last_modified_at = CURRENT_TIMESTAMP
        WHERE id = $2
    """

def update_document_content_query() -> str:
    return """
        UPDATE document_draft
        SET content = $1::jsonb, last_modified_at = CURRENT_TIMESTAMP
        WHERE id = $2
    """

def update_document_reference_and_content_query() -> str:
    return """
        UPDATE document_draft
        SET reference = $1, content = $2::jsonb, last_modified_at = CURRENT_TIMESTAMP
        WHERE id = $3
    """

def reset_document_to_draft_query() -> str:
    return """
        UPDATE document_draft
        SET status = 'draft', last_modified_at = CURRENT_TIMESTAMP
        WHERE id = $1
    """


def get_user_by_id_query() -> str:
    return """
        SELECT u.id as user_id, u.full_name
        FROM users u
        WHERE u.id = $1
    """

def get_document_basic_info_for_signature_query() -> str:
    return """
        SELECT
            d.id as document_id,
            d.reference,
            d.content,
            d.status,
            d.created_by,
            d.last_modified_at,
            d.id as document_generate_id,
            d.sent_to_sign_at,
            d.sent_by,
            dt.name as document_type_name,
            dt.acronym as document_type_acronym,
            dt.type as document_base_type,
            dt.signature_policy,
            dt.visibility as document_type_visibility,
            COALESCE(od.resume, d.resume) as resume,
            COALESCE(od.short_resume, d.short_resume) as short_resume,
            od.official_number
        FROM document_draft d
        JOIN document_types dt ON d.document_type_id = dt.id
        LEFT JOIN official_documents od ON od.id = d.id AND od.signed_at IS NOT NULL
        WHERE d.id = $1
    """

def check_document_has_official_number_query() -> str:
    return """
        SELECT EXISTS(
            SELECT 1
            FROM official_documents
            WHERE id = $1
              AND signed_at IS NOT NULL
        ) as has_official_number
    """

def check_user_exists_query() -> str:
    return """
        SELECT EXISTS(
            SELECT 1
            FROM users
            WHERE id = $1
        ) as user_exists
    """

def check_user_is_document_signer_query() -> str:
    return """
        SELECT EXISTS(
            SELECT 1
            FROM document_signers
            WHERE document_id = $1 AND user_id = $2
        ) as is_signer
    """


def get_document_for_signing_start_query() -> str:
    return """
        SELECT d.id as document_id, d.reference, d.status, d.content, d.created_by,
               d.created_by_citizen, d.sent_to_sign_at, d.sent_by,
               d.document_type_id, dt.name as type_name, dt.acronym as type_acronym,
               dt.type as source_type,
               EXISTS(SELECT 1 FROM document_type_fields dtf WHERE dtf.document_type_id = dt.id) AS has_fields
        FROM document_draft d
        LEFT JOIN document_types dt ON d.document_type_id = dt.id
        WHERE d.id = $1
    """

def get_document_signers_for_pdf_query() -> str:
    return """
        SELECT ds.user_id, ds.citizen_id, ds.signing_order, ds.is_numerator,
               COALESCE(u.full_name, c.full_name) as user_name
        FROM document_signers ds
        LEFT JOIN users u ON ds.user_id = u.id
        LEFT JOIN citizens c ON ds.citizen_id = c.id
        WHERE ds.document_id = $1
        ORDER BY ds.signing_order
    """

def update_document_to_sent_to_sign_query() -> str:
    return """
        UPDATE document_draft
        SET status = 'sent_to_sign',
            last_modified_at = CURRENT_TIMESTAMP,
            sent_by = $1,
            sent_to_sign_at = CURRENT_TIMESTAMP
        WHERE id = $2
    """


def get_document_status_query_v2() -> str:
    return """
        SELECT status
        FROM document_draft
        WHERE id = $1
    """


def get_document_rejections_history_query() -> str:
    return """
        SELECT dr.id as rejection_id, dr.reason, dr.rejected_at,
               u.full_name as rejected_by_name,
               u.email as rejected_by_email
        FROM document_rejections dr
        JOIN users u ON dr.rejected_by = u.id
        WHERE dr.document_id = $1
        ORDER BY dr.rejected_at DESC
    """

def get_rejected_documents_for_user_query() -> str:
    return """
        SELECT DISTINCT d.id as document_id, d.reference, d.status, d.created_at, d.last_modified_at,
               dt.name as document_type_name, dt.acronym as document_type_acronym,
               COALESCE(creator.full_name, creator_citizen.full_name) as creator_name,
               (d.created_by = $1) as is_creator
        FROM document_draft d
        JOIN document_types dt ON d.document_type_id = dt.id
        LEFT JOIN users creator ON d.created_by = creator.id
        LEFT JOIN citizens creator_citizen ON d.created_by_citizen = creator_citizen.id
        LEFT JOIN document_signers ds ON d.id = ds.document_id
        WHERE d.status = 'rejected'
          AND (d.created_by = $2 OR ds.user_id = $3)
        ORDER BY d.last_modified_at DESC
        LIMIT $4 OFFSET $5
    """

def count_rejected_documents_for_user_query() -> str:
    return """
        SELECT COUNT(DISTINCT d.id)
        FROM document_draft d
        LEFT JOIN document_signers ds ON d.id = ds.document_id
        WHERE d.status = 'rejected'
          AND (d.created_by = $1 OR ds.user_id = $2)
    """


def update_document_signers_order_query() -> str:
    return "UPDATE document_signers SET signing_order = $1 WHERE document_id = $2 AND user_id = $3"

def get_document_draft_status_query() -> str:
    return "SELECT status FROM document_draft WHERE id = $1"

def update_signer_status_to_signed_query() -> str:
    return """
        UPDATE document_signers
        SET status = 'signed', signed_at = CURRENT_TIMESTAMP
        WHERE document_id = $1 AND user_id = $2
    """


def get_user_info_for_signing_query() -> str:
    return """
        SELECT u.id as user_id, u.full_name
        FROM users u
        WHERE u.id = $1
    """

def get_signer_role_and_document_status_query() -> str:
    return """
        SELECT
            ds.is_numerator,
            ds.status as signer_status,
            dd.status as doc_status,
            (SELECT COUNT(*) FROM document_signers
             WHERE document_id = $1
             AND is_numerator = false
             AND (status = 'pending' OR status IS NULL)) as pending_common_signers
        FROM document_signers ds
        JOIN document_draft dd ON ds.document_id = dd.id
        WHERE ds.document_id = $2 AND ds.user_id = $3
    """


def search_official_document_by_number_query() -> str:
    return """
        SELECT
            od.id as document_id,
            od.official_number as reference,
            od.official_number,
            od.signed_at,
            dd.last_modified_at as updated_at,
            -- Informacion del tipo de documento
            dt.name as document_type_name,
            dt.acronym as document_type_acronym,
            dt.type as document_base_type,
            dt.is_reserved as document_type_is_reserved,
            -- Informacion del creador original (usuario o ciudadano TAD)
            COALESCE(creator.full_name, creator_citizen.full_name) as creator_name,
            creator.profile_picture_url as creator_profile_picture_id,
            -- Informacion del numerador (usuario o ciudadano TAD)
            COALESCE(numerator.full_name, numerator_citizen.full_name) as numerator_name
        FROM official_documents od
        -- Join con document_draft para obtener informacion base
        LEFT JOIN document_draft dd ON od.id = dd.id
        -- Join con document_types para obtener tipo
        LEFT JOIN document_types dt ON od.document_type_id = dt.id
        -- Join con users/citizens para obtener creador
        LEFT JOIN users creator ON dd.created_by = creator.id
        LEFT JOIN citizens creator_citizen ON dd.created_by_citizen = creator_citizen.id
        -- Join con users/citizens para obtener numerador
        LEFT JOIN users numerator ON od.numerator_id = numerator.id
        LEFT JOIN citizens numerator_citizen ON od.numerator_citizen = numerator_citizen.id
        WHERE od.official_number = $1
          AND od.signed_at IS NOT NULL
        LIMIT 1
    """


def check_document_has_embeddings_query() -> str:
    return """
        SELECT EXISTS(
            SELECT 1 FROM document_chunks
            WHERE official_document_id = $1
              AND embedding IS NOT NULL
            LIMIT 1
        ) as has_embeddings
    """


def get_proposed_cases_for_document_query() -> str:
    return """
        SELECT
            cpd.case_id,
            c.case_number,
            CASE WHEN COALESCE(ct.is_reserved, false) THEN NULL ELSE c.reference END as reference,
            COALESCE(ct.is_reserved, false) as is_reserved,
            cpd.proposing_date,
            cpd.auto_link_on_sign
        FROM case_proposed_documents cpd
        JOIN cases c ON cpd.case_id = c.id
        LEFT JOIN case_templates ct ON c.case_template_id = ct.id
        WHERE cpd.document_draft_id = $1
          AND cpd.is_active = true
        ORDER BY cpd.proposing_date DESC
    """

def get_linked_cases_for_official_document_query() -> str:
    return """
        SELECT
            cod.case_id,
            c.case_number,
            CASE WHEN COALESCE(ct.is_reserved, false) THEN NULL ELSE c.reference END AS reference,
            COALESCE(ct.is_reserved, false) AS is_reserved,
            cod.order_number,
            cod.linking_date
        FROM case_official_documents cod
        JOIN cases c ON c.id = cod.case_id
        LEFT JOIN case_templates ct ON ct.id = c.case_template_id
        WHERE cod.official_document_id = $1
          AND cod.is_active = true
        ORDER BY cod.linking_date DESC
    """


def delete_proposed_cases_for_document_query() -> str:
    return "DELETE FROM case_proposed_documents WHERE document_draft_id = $1"

def insert_proposed_case_query() -> str:
    return """
        INSERT INTO case_proposed_documents (
            id, case_id, document_draft_id, proposing_user_id, auto_link_on_sign, proposing_date, is_active
        ) VALUES ($1, $2, $3, $4, $5, NOW(), true)
    """

def validate_case_exists_query() -> str:
    return """
        SELECT id
        FROM cases
        WHERE id = $1 AND status != 'archived'
    """
