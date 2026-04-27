"""
Consultas SQL centralizadas para el modulo de documentos.
Facilita el mantenimiento y reutilizacion de consultas.

UBICACION: services/documents/core/queries.py
MIGRADO: Fase 2.2 del refactoring
"""

from config.constants import EXCLUDED_DOCUMENT_TYPES


def get_excluded_types_clause() -> str:
    """
    Genera la clausula SQL para excluir tipos de documentos especificos.

    Returns:
        str: Clausula SQL NOT IN con los tipos excluidos

    Example:
        >>> get_excluded_types_clause()
        "AND dt.acronym NOT IN ('CAEX', 'PV')"
    """
    types_list = ', '.join(f"'{t}'" for t in EXCLUDED_DOCUMENT_TYPES)
    return f"AND dt.acronym NOT IN ({types_list})"


def autocomplete_official_documents_query() -> str:
    """
    Genera la consulta SQL para autocompletado de documentos oficiales.

    Returns:
        str: Query SQL para buscar documentos oficiales

    Example:
        >>> query = autocomplete_official_documents_query()
    """
    excluded_clause = get_excluded_types_clause()

    query = f"""
        SELECT DISTINCT
            o.id as document_id,
            o.official_number,
            o.reference
        FROM official_documents o
        JOIN document_types dt ON o.document_type_id = dt.id
        WHERE o.official_number ILIKE %s
          AND o.signed_at IS NOT NULL
          {excluded_clause}
        ORDER BY o.official_number
        LIMIT %s OFFSET %s
    """

    return query


def get_document_type_query() -> str:
    """
    Query para verificar si un tipo de documento existe.

    Returns:
        str: Query SQL para buscar tipo de documento por acronimo
    """
    return """
        SELECT id AS document_type_id, name
        FROM document_types
        WHERE acronym = %s
    """


def insert_document_draft_query() -> str:
    """
    Query para insertar un nuevo documento en estado draft.

    Returns:
        str: Query SQL para insertar documento
    """
    return """
        INSERT INTO document_draft (
            id, document_type_id, reference, created_by,
            last_modified_at, status
        ) VALUES (
            %s, %s, %s, %s,
            CURRENT_TIMESTAMP, 'draft'
        )
        RETURNING id AS document_id, last_modified_at
    """


def insert_document_signer_query() -> str:
    """
    Query para asignar un firmante a un documento.

    Returns:
        str: Query SQL para insertar firmante
    """
    return """
        INSERT INTO document_signers (
            document_id,
            user_id,
            signing_order,
            is_numerator
        ) VALUES (%s, %s, %s, %s)
    """


def get_document_details_for_editing_query() -> str:
    """
    Query para obtener detalles completos de documento en edicion.

    Returns:
        str: Query SQL con todos los datos del documento
    """
    return """
        SELECT
            d.id,
            d.reference,
            d.content,
            d.status,
            d.created_by as creator_id,
            d.last_modified_at,
            d.resume,
            d.short_resume,
            dt.name as document_type_name,
            dt.acronym as document_type_acronym,
            dt.type as document_type_source,
            u.full_name as creator_name,
            u.profile_picture_url as creator_profile_picture_url,
            dep.acronym as creator_department_acronym,
            sec.acronym as creator_sector_acronym
        FROM document_draft d
            LEFT JOIN document_types dt ON d.document_type_id = dt.id
            LEFT JOIN users u ON d.created_by = u.id
            LEFT JOIN sectors sec ON u.sector_id = sec.id
            LEFT JOIN departments dep ON sec.department_id = dep.id
        WHERE d.id = %s
    """


def get_document_signers_query() -> str:
    """
    Query para obtener firmantes de un documento.

    Returns:
        str: Query SQL para lista de firmantes
    """
    return """
        SELECT
            ds.user_id,
            ds.signing_order,
            ds.is_numerator,
            COALESCE(u.full_name, '') as user_name,
            u.email,
            u.profile_picture_url,
            dep.acronym as department_acronym,
            sec.acronym as sector_acronym
        FROM document_signers ds
        JOIN users u ON ds.user_id = u.id
        LEFT JOIN sectors sec ON u.sector_id = sec.id
        LEFT JOIN departments dep ON sec.department_id = dep.id
        WHERE ds.document_id = %s
        ORDER BY ds.signing_order
    """


def get_document_rejection_info_query() -> str:
    """
    Query para obtener informacion del ultimo rechazo de documento.

    Returns:
        str: Query SQL para datos de rechazo
    """
    return """
        SELECT
            r.reason,
            r.rejected_at as created_at,
            r.rejected_by,
            u.full_name as rejected_by_name
        FROM document_rejections r
        JOIN users u ON r.rejected_by = u.id
        WHERE r.document_id = %s
        ORDER BY r.rejected_at DESC
        LIMIT 1
    """


def get_document_status_query() -> str:
    """
    Query para obtener solo el estado de un documento.

    Returns:
        str: Query SQL para obtener status
    """
    return "SELECT id AS document_id, status FROM document_draft WHERE id = %s"


def delete_document_signers_query() -> str:
    """
    Query para eliminar todos los firmantes de un documento.

    Returns:
        str: Query SQL para eliminar firmantes
    """
    return "DELETE FROM document_signers WHERE document_id = %s"


def insert_document_signer_ordered_query() -> str:
    """
    Query para insertar firmante con orden especifico.

    Returns:
        str: Query SQL para insertar firmante con orden
    """
    return "INSERT INTO document_signers (document_id, user_id, signing_order, is_numerator) VALUES (%s, %s, %s, %s)"


def get_official_document_info_query() -> str:
    """Query para obtener informacion de documento oficial."""
    return """
        SELECT
            od.official_number,
            od.id,
            dd.status
        FROM official_documents od
        INNER JOIN document_draft dd ON od.id = dd.id
        WHERE od.id = %s
          AND od.signed_at IS NOT NULL
    """

def get_all_display_states_query() -> str:
    """Query para obtener todos los estados de visualizacion."""
    return """
        SELECT display_state_name as display_state
        FROM document_display_states
        ORDER BY display_state_code ASC
    """

def get_display_state_by_code_query() -> str:
    """Query para obtener nombre de estado por codigo."""
    return """
        SELECT display_state_name
        FROM document_display_states
        WHERE display_state_code = %s
    """

def get_all_state_mappings_query() -> str:
    """Query para obtener todos los mapeos de codigos a nombres."""
    return """
        SELECT display_state_code, display_state_name
        FROM document_display_states
        ORDER BY display_state_code ASC
    """

def get_all_document_types_query() -> str:
    """Query para obtener todos los tipos de documentos activos con descripcion y sectores restringidos."""
    return """
        SELECT
            dt.name,
            dt.acronym,
            dt.type,
            dt.trust,
            dt.description,
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
    """Query para obtener informacion completa del documento para preview."""
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
            dt.type as source_type
        FROM document_draft d
        LEFT JOIN document_types dt ON d.document_type_id = dt.id
        WHERE d.id = %s
    """


# ============================================================================
# DELETION QUERIES
# ============================================================================

def get_document_for_deletion_query() -> str:
    """Query para obtener informacion del documento para eliminacion."""
    return """
        SELECT id, status, created_by, is_deleted, reference
        FROM document_draft
        WHERE id = %s
    """

def unlink_document_from_cases_query() -> str:
    """Query para desvincular documento de expedientes (proposed documents)."""
    return """
        DELETE FROM case_proposed_documents
        WHERE document_draft_id = %s
    """

def soft_delete_document_query() -> str:
    """Query para marcar documento como eliminado (soft delete)."""
    return """
        UPDATE document_draft
        SET is_deleted = true, last_modified_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """

def get_user_info_for_deletion_query() -> str:
    """Query para obtener informacion del usuario que elimina."""
    return """
        SELECT full_name, email
        FROM users
        WHERE id = %s
    """


# ============================================================================
# REJECTION QUERIES
# ============================================================================

def get_document_info_for_rejection_query() -> str:
    """Query para obtener informacion del documento para rechazo."""
    return """
        SELECT d.id as document_id, d.reference, d.status, d.created_by,
               dt.name as document_type_name, dt.acronym as document_type_acronym,
               creator.full_name as creator_name
        FROM document_draft d
        JOIN document_types dt ON d.document_type_id = dt.id
        JOIN users creator ON d.created_by = creator.id
        WHERE d.id = %s
    """

def check_document_is_official_query() -> str:
    """Query para verificar si un documento es oficial."""
    return "SELECT COUNT(*) as count FROM official_documents WHERE id = %s AND signed_at IS NOT NULL"

def get_document_generate_id_query() -> str:
    """Query para obtener document_generate_id (usa el id del documento)."""
    return "SELECT id as document_generate_id FROM document_draft WHERE id = %s"

def update_document_to_rejected_query() -> str:
    """Query para actualizar documento a estado rechazado."""
    return """
        UPDATE document_draft
        SET status = 'rejected',
            last_modified_at = CURRENT_TIMESTAMP,
            sent_to_sign_at = NULL,
            sent_by = NULL,
            resume = NULL
        WHERE id = %s
    """

def insert_rejection_record_query() -> str:
    """Query para insertar registro de rechazo."""
    return """
        INSERT INTO document_rejections (id, document_id, rejected_by, reason)
        VALUES (%s, %s, %s, %s)
    """

def update_signers_to_rejected_query() -> str:
    """Query para actualizar firmantes pendientes a rechazado."""
    return """
        UPDATE document_signers
        SET status = 'rejected'
        WHERE document_id = %s AND signed_at IS NULL
    """

def get_rejector_info_query() -> str:
    """Query para obtener informacion del usuario que rechazo."""
    return """
        SELECT full_name, email
        FROM users WHERE id = %s
    """

def check_user_is_signer_query() -> str:
    """Query para verificar si usuario es firmante."""
    return """
        SELECT user_id, signing_order, is_numerator
        FROM document_signers
        WHERE document_id = %s AND user_id = %s
    """


# ============================================================================
# SAVE/EDITING QUERIES
# ============================================================================

def update_document_reference_query() -> str:
    """Query para actualizar solo la referencia del documento."""
    return """
        UPDATE document_draft
        SET reference = %s, last_modified_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """

def update_document_content_query() -> str:
    """Query para actualizar solo el contenido del documento."""
    return """
        UPDATE document_draft
        SET content = %s, last_modified_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """

def update_document_reference_and_content_query() -> str:
    """Query para actualizar referencia y contenido del documento."""
    return """
        UPDATE document_draft
        SET reference = %s, content = %s, last_modified_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """

def reset_document_to_draft_query() -> str:
    """Query para resetear documento a estado draft."""
    return """
        UPDATE document_draft
        SET status = 'draft', last_modified_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """

# ============================================================================
# SIGNATURE DETAILS QUERIES
# ============================================================================

def get_user_by_id_query() -> str:
    """Query para obtener informacion basica de un usuario."""
    return """
        SELECT u.id as user_id, u.full_name
        FROM users u
        WHERE u.id = %s
    """

def get_document_basic_info_for_signature_query() -> str:
    """Query para obtener informacion basica del documento para firma."""
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
            COALESCE(od.resume, d.resume) as resume,
            COALESCE(od.short_resume, d.short_resume) as short_resume,
            od.official_number
        FROM document_draft d
        JOIN document_types dt ON d.document_type_id = dt.id
        LEFT JOIN official_documents od ON od.id = d.id AND od.signed_at IS NOT NULL
        WHERE d.id = %s
    """

def check_document_has_official_number_query() -> str:
    """Query para verificar si documento tiene numero oficial."""
    return """
        SELECT EXISTS(
            SELECT 1
            FROM official_documents
            WHERE id = %s
              AND signed_at IS NOT NULL
        ) as has_official_number
    """

def check_user_exists_query() -> str:
    """Query para verificar existencia de usuario."""
    return """
        SELECT EXISTS(
            SELECT 1
            FROM users
            WHERE id = %s
        ) as user_exists
    """

def check_user_is_document_signer_query() -> str:
    """Query para verificar si usuario es firmante del documento."""
    return """
        SELECT EXISTS(
            SELECT 1
            FROM document_signers
            WHERE document_id = %s AND user_id = %s
        ) as is_signer
    """

# ============================================================================
# START SIGNING QUERIES
# ============================================================================

def get_document_for_signing_start_query() -> str:
    """Query para obtener informacion del documento antes de iniciar firma."""
    return """
        SELECT d.id as document_id, d.reference, d.status, d.content, d.created_by,
               d.document_type_id, dt.name as type_name, dt.acronym as type_acronym
        FROM document_draft d
        LEFT JOIN document_types dt ON d.document_type_id = dt.id
        WHERE d.id = %s
    """

def get_document_signers_for_pdf_query() -> str:
    """Query para obtener firmantes del documento para generar PDF."""
    return """
        SELECT ds.user_id, ds.signing_order, ds.is_numerator,
               u.full_name as user_name
        FROM document_signers ds
        JOIN users u ON ds.user_id = u.id
        WHERE ds.document_id = %s
        ORDER BY ds.signing_order
    """

def get_inactive_signers_query() -> str:
    """Query para obtener firmantes inactivos que requieren invitacion."""
    return """
        SELECT u.id as user_id, u.email, u.full_name
        FROM document_signers ds
        JOIN users u ON ds.user_id = u.id
        WHERE ds.document_id = %s AND u.estado = 2
    """

def update_document_to_sent_to_sign_query() -> str:
    """Query para actualizar documento a estado sent_to_sign."""
    return """
        UPDATE document_draft
        SET status = 'sent_to_sign',
            last_modified_at = CURRENT_TIMESTAMP,
            sent_by = %s,
            sent_to_sign_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """


# ============================================================
# UNIFIED DETAILS QUERIES
# ============================================================

def get_document_status_query_v2() -> str:
    """Query para obtener el estado actual de un documento."""
    return """
        SELECT status
        FROM document_draft
        WHERE id = %s
    """


# ============================================================
# REJECTION HISTORY QUERIES
# ============================================================

def get_document_rejections_history_query() -> str:
    """Query para obtener historial de rechazos de un documento."""
    return """
        SELECT dr.id as rejection_id, dr.reason, dr.rejected_at,
               u.full_name as rejected_by_name,
               u.email as rejected_by_email
        FROM document_rejections dr
        JOIN users u ON dr.rejected_by = u.id
        WHERE dr.document_id = %s
        ORDER BY dr.rejected_at DESC
    """

def get_rejected_documents_for_user_query() -> str:
    """Query para obtener documentos rechazados de un usuario."""
    return """
        SELECT DISTINCT d.id as document_id, d.reference, d.status, d.created_at, d.last_modified_at,
               dt.name as document_type_name, dt.acronym as document_type_acronym,
               creator.full_name as creator_name,
               (d.created_by = %s) as is_creator
        FROM document_draft d
        JOIN document_types dt ON d.document_type_id = dt.id
        JOIN users creator ON d.created_by = creator.id
        LEFT JOIN document_signers ds ON d.id = ds.document_id
        WHERE d.status = 'rejected'
          AND (d.created_by = %s OR ds.user_id = %s)
        ORDER BY d.last_modified_at DESC
        LIMIT %s OFFSET %s
    """

def count_rejected_documents_for_user_query() -> str:
    """Query para contar documentos rechazados de un usuario."""
    return """
        SELECT COUNT(DISTINCT d.id)
        FROM document_draft d
        LEFT JOIN document_signers ds ON d.id = ds.document_id
        WHERE d.status = 'rejected'
          AND (d.created_by = %s OR ds.user_id = %s)
    """

# === QUERIES PARA START_SIGNING ===

def update_document_signers_order_query() -> str:
    """Query para actualizar el orden de firmantes de un documento."""
    return "UPDATE document_signers SET signing_order = %s WHERE document_id = %s AND user_id = %s"

def get_document_draft_status_query() -> str:
    """Query para obtener el status de un documento draft."""
    return "SELECT status FROM document_draft WHERE id = %s"

def get_user_full_name_query() -> str:
    """Query para obtener el nombre completo de un usuario."""
    return "SELECT full_name FROM users WHERE id = %s"

def update_signer_status_to_signed_query() -> str:
    """Query para actualizar el estado de un firmante a 'signed'."""
    return """
        UPDATE document_signers
        SET status = 'signed', signed_at = CURRENT_TIMESTAMP
        WHERE document_id = %s AND user_id = %s
    """

# === QUERIES PARA SUPER_SIGN ===

def get_user_info_for_signing_query() -> str:
    """Query para obtener informacion basica del usuario para firma."""
    return """
        SELECT u.id as user_id, u.full_name
        FROM users u
        WHERE u.id = %s
    """

def get_signer_role_and_document_status_query() -> str:
    """Query para obtener rol del firmante y estado del documento con conteo de firmantes pendientes."""
    return """
        SELECT
            ds.is_numerator,
            ds.status as signer_status,
            dd.status as doc_status,
            (SELECT COUNT(*) FROM document_signers
             WHERE document_id = %s
             AND is_numerator = false
             AND (status = 'pending' OR status IS NULL)) as pending_common_signers
        FROM document_signers ds
        JOIN document_draft dd ON ds.document_id = dd.id
        WHERE ds.document_id = %s AND ds.user_id = %s
    """

# === QUERIES PARA SEARCH_OFFICIAL ===

def search_official_document_by_number_query() -> str:
    """Query compleja para buscar documentos oficiales por numero exacto."""
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
            -- Informacion del creador original
            creator.full_name as creator_name,
            creator.profile_picture_url as creator_profile_picture_id,
            -- Informacion del numerador
            numerator.full_name as numerator_name
        FROM official_documents od
        -- Join con document_draft para obtener informacion base
        LEFT JOIN document_draft dd ON od.id = dd.id
        -- Join con document_types para obtener tipo
        LEFT JOIN document_types dt ON od.document_type_id = dt.id
        -- Join con users para obtener creador
        LEFT JOIN users creator ON dd.created_by = creator.id
        -- Join con users para obtener numerador
        LEFT JOIN users numerator ON od.numerator_id = numerator.id
        WHERE od.official_number = %s
          AND od.signed_at IS NOT NULL
        LIMIT 1
    """


def check_document_has_embeddings_query() -> str:
    """Query para verificar si un documento oficial tiene embeddings indexados.

    IMPORTANTE: Verifica que embedding IS NOT NULL porque:
    - Un documento puede tener chunks sin embeddings (si OpenAI fallo)
    - El campo embedding es nullable en document_chunks

    Returns:
        str: Query SQL para verificar existencia de embeddings
    """
    return """
        SELECT EXISTS(
            SELECT 1 FROM document_chunks
            WHERE official_document_id = %s
              AND embedding IS NOT NULL
            LIMIT 1
        ) as has_embeddings
    """


# ============================================================================
# PROPOSED CASES QUERIES
# ============================================================================

def get_proposed_cases_for_document_query() -> str:
    """Query para obtener expedientes propuestos para un documento."""
    return """
        SELECT
            cpd.case_id,
            c.case_number,
            c.reference,
            cpd.proposing_date
        FROM case_proposed_documents cpd
        JOIN cases c ON cpd.case_id = c.id
        WHERE cpd.document_draft_id = %s
          AND cpd.is_active = true
        ORDER BY cpd.proposing_date DESC
    """

def delete_proposed_cases_for_document_query() -> str:
    """Query para eliminar todas las propuestas de un documento."""
    return "DELETE FROM case_proposed_documents WHERE document_draft_id = %s"

def insert_proposed_case_query() -> str:
    """Query para insertar una propuesta de vinculación documento-expediente."""
    return """
        INSERT INTO case_proposed_documents (
            id, case_id, document_draft_id, proposing_user_id, proposing_date, is_active
        ) VALUES (%s, %s, %s, %s, NOW(), true)
    """

def validate_case_exists_query() -> str:
    """Query para validar que un expediente existe y no está archivado."""
    return """
        SELECT id
        FROM cases
        WHERE id = %s AND status != 'archived'
    """
