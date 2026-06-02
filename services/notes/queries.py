"""
SQL queries para el módulo de NOTAS.
Queries centralizadas para operaciones de recipients, openings y consultas.
"""


def check_nota_document_type_query() -> str:
    """Verifica si un document_type_id corresponde a NOTA."""
    return """
        SELECT id FROM document_types
        WHERE id = $1 AND acronym = 'NOTA'
    """


def check_nota_by_acronym_query() -> str:
    """Obtiene el ID del tipo NOTA por acronym."""
    return """
        SELECT id FROM document_types
        WHERE acronym = 'NOTA' AND is_active = true
    """


def validate_sectors_exist_query() -> str:
    """Valida que los sector_ids existan y estén activos."""
    return """
        SELECT id FROM sectors
        WHERE id::text = ANY($1) AND is_active = true
    """


def insert_note_recipient_query() -> str:
    """Inserta un destinatario de nota."""
    return """
        INSERT INTO notes_recipients (document_id, sector_id, recipient_type, sender_sector_id)
        VALUES ($1, $2, $3, $4)
        RETURNING id
    """


def get_recipients_by_document_query() -> str:
    """Obtiene todos los recipients de un documento."""
    return """
        SELECT
            nr.id,
            nr.document_id,
            nr.sector_id,
            nr.recipient_type,
            nr.sender_sector_id,
            s.acronym as sector_acronym,
            d.name as department_name,
            d.acronym as department_acronym
        FROM notes_recipients nr
        JOIN sectors s ON s.id = nr.sector_id
        JOIN departments d ON d.id = s.department_id
        WHERE nr.document_id = $1
        ORDER BY
            CASE nr.recipient_type
                WHEN 'TO' THEN 1
                WHEN 'CC' THEN 2
                WHEN 'BCC' THEN 3
            END,
            d.name
    """


def get_sender_sector_query() -> str:
    """Obtiene el sector del sender de un documento NOTA."""
    return """
        SELECT DISTINCT sender_sector_id
        FROM notes_recipients
        WHERE document_id = $1
        LIMIT 1
    """


def insert_note_opening_query() -> str:
    """Registra apertura de nota (ON CONFLICT ignora si ya existe)."""
    return """
        INSERT INTO notes_openings (document_id, sector_id, user_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (document_id, user_id) DO NOTHING
        RETURNING id
    """


def get_opening_by_document_user_query() -> str:
    """Verifica si un usuario ya abrió un documento."""
    return """
        SELECT id, opened_at
        FROM notes_openings
        WHERE document_id = $1 AND user_id = $2
    """


def get_openings_by_document_query() -> str:
    """Obtiene todas las aperturas de un documento."""
    return """
        SELECT
            no.id,
            no.sector_id,
            no.user_id,
            no.opened_at,
            u.full_name as user_name,
            s.acronym as sector_acronym
        FROM notes_openings no
        JOIN users u ON u.id = no.user_id
        JOIN sectors s ON s.id = no.sector_id
        WHERE no.document_id = $1
        ORDER BY no.opened_at
    """


def get_sent_notes_query() -> str:
    """Obtiene notas enviadas por un sector (solo oficializadas)."""
    return """
        SELECT
            od.id,
            od.official_number,
            od.reference,
            od.signed_at,
            od.resume as ai_summary,
            dt.acronym as document_type,
            (
                SELECT json_agg(json_build_object(
                    'sector_id', nr2.sector_id,
                    'type', nr2.recipient_type,
                    'acronym', s2.acronym,
                    'department', d2.name
                ))
                FROM notes_recipients nr2
                JOIN sectors s2 ON s2.id = nr2.sector_id
                JOIN departments d2 ON d2.id = s2.department_id
                WHERE nr2.document_id = od.id
            ) as recipients,
            (
                SELECT COUNT(*)::int
                FROM notes_openings no
                WHERE no.document_id = od.id
            ) as openings_count
        FROM official_documents od
        JOIN document_types dt ON dt.id = od.document_type_id
        WHERE od.signed_at IS NOT NULL
        AND od.id IN (
            SELECT DISTINCT nr.document_id
            FROM notes_recipients nr
            WHERE nr.sender_sector_id = $1
        )
        ORDER BY od.signed_at DESC
        LIMIT $2 OFFSET $3
    """


def get_sent_notes_count_query() -> str:
    """Cuenta notas enviadas por un sector."""
    return """
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND nr.sender_sector_id = $1
    """


def get_received_notes_query() -> str:
    """Obtiene notas recibidas por un sector (solo oficializadas, NO archivadas)."""
    return """
        SELECT
            od.id,
            od.official_number,
            od.reference,
            od.signed_at,
            od.resume as ai_summary,
            dt.acronym as document_type,
            nr.recipient_type,
            nr.sender_sector_id,
            ss.acronym as sender_sector_acronym,
            sd.name as sender_department_name,
            (
                SELECT json_build_object(
                    'opened', EXISTS(
                        SELECT 1 FROM notes_openings no
                        WHERE no.document_id = od.id AND no.sector_id = $1
                    ),
                    'opened_at', (
                        SELECT no.opened_at FROM notes_openings no
                        WHERE no.document_id = od.id AND no.sector_id = $2
                        LIMIT 1
                    )
                )
            ) as read_status
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = $3 AND nr.is_archived = false
        JOIN document_types dt ON dt.id = od.document_type_id
        JOIN sectors ss ON ss.id = nr.sender_sector_id
        JOIN departments sd ON sd.id = ss.department_id
        WHERE od.signed_at IS NOT NULL
        ORDER BY od.signed_at DESC
        LIMIT $4 OFFSET $5
    """


def get_received_notes_count_query() -> str:
    """Cuenta notas recibidas por un sector (NO archivadas)."""
    return """
        SELECT COUNT(*)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = $1 AND nr.is_archived = false
        WHERE od.signed_at IS NOT NULL
    """


def get_note_detail_query() -> str:
    """Obtiene el detalle completo de una nota para un visor."""
    return """
        SELECT
            od.id,
            od.official_number,
            od.reference,
            od.content,
            od.signed_at,
            od.resume as ai_summary,
            od.signers,
            dt.name as document_type_name,
            dt.acronym as document_type_acronym,
            d.name as department_name
        FROM official_documents od
        JOIN document_types dt ON dt.id = od.document_type_id
        JOIN departments d ON d.id = od.department_id
        WHERE od.id = $1
          AND od.signed_at IS NOT NULL
    """


def check_user_is_recipient_query() -> str:
    """Verifica si un sector es recipient de una nota."""
    return """
        SELECT recipient_type
        FROM notes_recipients
        WHERE document_id = $1 AND sector_id = $2
    """


def check_user_is_sender_query() -> str:
    """Verifica si un sector es el sender de una nota."""
    return """
        SELECT EXISTS(
            SELECT 1 FROM notes_recipients
            WHERE document_id = $1 AND sender_sector_id = $2
        ) as is_sender
    """


# ============================================================================
# QUERIES MULTI-SECTOR
# Buscan en múltiples sectores usando ANY($N::uuid[])
# ============================================================================


def get_received_notes_multi_sector_query() -> str:
    """
    Obtiene notas recibidas en CUALQUIERA de los sectores dados (NO archivadas).
    Usa ANY() para buscar en lista de sector_ids.
    Retorna el recipient_type del primer sector que matchea.
    """
    return """
        SELECT DISTINCT ON (od.id)
            od.id,
            od.official_number,
            od.reference,
            od.signed_at,
            od.resume as ai_summary,
            dt.acronym as document_type,
            nr.recipient_type,
            nr.sector_id as matched_sector_id,
            nr.sender_sector_id,
            ss.acronym as sender_sector_acronym,
            sd.name as sender_department_name,
            (
                SELECT json_build_object(
                    'opened', EXISTS(
                        SELECT 1 FROM notes_openings no
                        WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                    ),
                    'opened_at', (
                        SELECT no.opened_at FROM notes_openings no
                        WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                        LIMIT 1
                    )
                )
            ) as read_status
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = false
        JOIN document_types dt ON dt.id = od.document_type_id
        JOIN sectors ss ON ss.id = nr.sender_sector_id
        JOIN departments sd ON sd.id = ss.department_id
        WHERE od.signed_at IS NOT NULL
        ORDER BY od.id, od.signed_at DESC
    """


def get_received_notes_multi_sector_paginated_query(date_where: str = "") -> str:
    """
    Obtiene notas recibidas en CUALQUIERA de los sectores dados, con paginación (NO archivadas).
    Usa ANY() para buscar en lista de sector_ids.
    Params: $1=sector_ids[], $2=limit, $3=offset (+ date params si aplica).
    """
    return f"""
        WITH distinct_notes AS (
            SELECT DISTINCT ON (od.id)
                od.id,
                od.official_number,
                od.reference,
                od.signed_at,
                od.resume as ai_summary,
                dt.acronym as document_type,
                nr.recipient_type,
                nr.sector_id as matched_sector_id,
                nr.sender_sector_id,
                ss.acronym as sender_sector_acronym,
                sd.name as sender_department_name,
                (
                    SELECT json_build_object(
                        'opened', EXISTS(
                            SELECT 1 FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                        ),
                        'opened_at', (
                            SELECT no.opened_at FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                            LIMIT 1
                        )
                    )
                ) as read_status
            FROM official_documents od
            JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = false
            JOIN document_types dt ON dt.id = od.document_type_id
            JOIN sectors ss ON ss.id = nr.sender_sector_id
            JOIN departments sd ON sd.id = ss.department_id
            WHERE od.signed_at IS NOT NULL {date_where}
            ORDER BY od.id, nr.recipient_type
        )
        SELECT * FROM distinct_notes
        ORDER BY signed_at DESC
        LIMIT $2 OFFSET $3
    """


def get_received_notes_multi_sector_count_query(date_where: str = "") -> str:
    """Cuenta notas recibidas en CUALQUIERA de los sectores dados (NO archivadas).
    Params: $1=sector_ids[] (+ date params si aplica).
    """
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = false
        WHERE od.signed_at IS NOT NULL {date_where}
    """


def get_sent_notes_multi_sector_query(date_where: str = "") -> str:
    """
    Obtiene notas enviadas desde CUALQUIERA de los sectores dados.
    Params: $1=sector_ids[], $2=limit, $3=offset (+ date params si aplica).
    """
    return f"""
        SELECT
            od.id,
            od.official_number,
            od.reference,
            od.signed_at,
            od.resume as ai_summary,
            dt.acronym as document_type,
            (
                SELECT json_agg(json_build_object(
                    'sector_id', nr2.sector_id,
                    'type', nr2.recipient_type,
                    'acronym', s2.acronym,
                    'department', d2.name
                ))
                FROM notes_recipients nr2
                JOIN sectors s2 ON s2.id = nr2.sector_id
                JOIN departments d2 ON d2.id = s2.department_id
                WHERE nr2.document_id = od.id
            ) as recipients,
            (
                SELECT COUNT(*)::int
                FROM notes_openings no
                WHERE no.document_id = od.id
            ) as openings_count
        FROM official_documents od
        JOIN document_types dt ON dt.id = od.document_type_id
        WHERE od.signed_at IS NOT NULL
        AND od.id IN (
            SELECT DISTINCT nr.document_id
            FROM notes_recipients nr
            WHERE nr.sender_sector_id = ANY($1::uuid[])
        )
        {date_where}
        ORDER BY od.signed_at DESC
        LIMIT $2 OFFSET $3
    """


def get_sent_notes_multi_sector_count_query(date_where: str = "") -> str:
    """Cuenta notas enviadas desde CUALQUIERA de los sectores dados.
    Params: $1=sector_ids[] (+ date params si aplica).
    """
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND nr.sender_sector_id = ANY($1::uuid[])
        {date_where}
    """


# ============================================================================
# QUERIES CON BÚSQUEDA (ILIKE)
# Versiones de las queries multi-sector con filtro de búsqueda
# ============================================================================


def get_received_notes_multi_sector_search_query(date_where: str = "") -> str:
    """
    Obtiene notas recibidas con filtro de búsqueda ILIKE (NO archivadas).
    Params: $1=sector_ids[], $2-$4=pattern ILIKE, $5=search_term, $6=limit, $7=offset.
    """
    return f"""
        WITH distinct_notes AS (
            SELECT DISTINCT ON (od.id)
                od.id,
                od.official_number,
                od.reference,
                od.signed_at,
                od.resume as ai_summary,
                dt.acronym as document_type,
                nr.recipient_type,
                nr.sector_id as matched_sector_id,
                nr.sender_sector_id,
                ss.acronym as sender_sector_acronym,
                sd.name as sender_department_name,
                (
                    SELECT json_build_object(
                        'opened', EXISTS(
                            SELECT 1 FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                        ),
                        'opened_at', (
                            SELECT no.opened_at FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                            LIMIT 1
                        )
                    )
                ) as read_status
            FROM official_documents od
            JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = false
            JOIN document_types dt ON dt.id = od.document_type_id
            JOIN sectors ss ON ss.id = nr.sender_sector_id
            JOIN departments sd ON sd.id = ss.department_id
            WHERE od.signed_at IS NOT NULL
            AND (
                od.official_number ILIKE $2
                OR od.reference ILIKE $3
                OR od.content->>'html' ILIKE $4
                OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
            )
            {date_where}
            ORDER BY od.id, nr.recipient_type
        )
        SELECT * FROM distinct_notes
        ORDER BY signed_at DESC
        LIMIT $6 OFFSET $7
    """


def get_received_notes_multi_sector_search_count_query(date_where: str = "") -> str:
    """Cuenta notas recibidas con filtro de búsqueda ILIKE (NO archivadas).
    Params: $1=sector_ids[], $2-$4=pattern ILIKE, $5=search_term.
    """
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = false
        WHERE od.signed_at IS NOT NULL
        AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
        )
        {date_where}
    """


def get_sent_notes_multi_sector_search_query(date_where: str = "") -> str:
    """
    Obtiene notas enviadas con filtro de búsqueda ILIKE.
    Params: $1=sector_ids[], $2-$4=pattern ILIKE, $5=search_term, $6=limit, $7=offset.
    """
    return f"""
        SELECT
            od.id,
            od.official_number,
            od.reference,
            od.signed_at,
            od.resume as ai_summary,
            dt.acronym as document_type,
            (
                SELECT json_agg(json_build_object(
                    'sector_id', nr2.sector_id,
                    'type', nr2.recipient_type,
                    'acronym', s2.acronym,
                    'department', d2.name
                ))
                FROM notes_recipients nr2
                JOIN sectors s2 ON s2.id = nr2.sector_id
                JOIN departments d2 ON d2.id = s2.department_id
                WHERE nr2.document_id = od.id
            ) as recipients,
            (
                SELECT COUNT(*)::int
                FROM notes_openings no
                WHERE no.document_id = od.id
            ) as openings_count
        FROM official_documents od
        JOIN document_types dt ON dt.id = od.document_type_id
        WHERE od.signed_at IS NOT NULL
        AND od.id IN (
            SELECT DISTINCT nr.document_id
            FROM notes_recipients nr
            WHERE nr.sender_sector_id = ANY($1::uuid[])
        )
        AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
        )
        {date_where}
        ORDER BY od.signed_at DESC
        LIMIT $6 OFFSET $7
    """


def get_sent_notes_multi_sector_search_count_query(date_where: str = "") -> str:
    """Cuenta notas enviadas con filtro de búsqueda ILIKE.
    Params: $1=sector_ids[], $2-$4=pattern ILIKE, $5=search_term.
    """
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND nr.sender_sector_id = ANY($1::uuid[])
        AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
        )
        {date_where}
    """


# ============================================================================
# QUERIES PARA ARCHIVADO DE NOTAS
# Queries para gestionar y listar notas archivadas
# ============================================================================


def update_note_archived_status_query() -> str:
    """Actualiza el estado de archivado de una nota para un sector específico."""
    return """
        UPDATE notes_recipients
        SET is_archived = $1,
            archived_at = CASE WHEN $2 = true THEN NOW() ELSE NULL END
        WHERE document_id = $3 AND sector_id = $4
        RETURNING id, is_archived, archived_at
    """


def get_note_recipient_info_query() -> str:
    """Obtiene información del recipient incluyendo estado de archivado."""
    return """
        SELECT
            nr.id,
            nr.document_id,
            nr.sector_id,
            nr.recipient_type,
            nr.sender_sector_id,
            nr.is_archived,
            nr.archived_at
        FROM notes_recipients nr
        WHERE nr.document_id = $1 AND nr.sector_id = $2
    """


def get_archived_notes_multi_sector_paginated_query() -> str:
    """
    Obtiene notas ARCHIVADAS en CUALQUIERA de los sectores dados, con paginación.
    Params: $1=sector_ids[], $2=limit, $3=offset.
    """
    return """
        WITH distinct_notes AS (
            SELECT DISTINCT ON (od.id)
                od.id,
                od.official_number,
                od.reference,
                od.signed_at,
                od.resume as ai_summary,
                dt.acronym as document_type,
                nr.recipient_type,
                nr.sector_id as matched_sector_id,
                nr.sender_sector_id,
                nr.is_archived,
                nr.archived_at,
                ss.acronym as sender_sector_acronym,
                sd.name as sender_department_name,
                (
                    SELECT json_build_object(
                        'opened', EXISTS(
                            SELECT 1 FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                        ),
                        'opened_at', (
                            SELECT no.opened_at FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                            LIMIT 1
                        )
                    )
                ) as read_status
            FROM official_documents od
            JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = true
            JOIN document_types dt ON dt.id = od.document_type_id
            JOIN sectors ss ON ss.id = nr.sender_sector_id
            JOIN departments sd ON sd.id = ss.department_id
            WHERE od.signed_at IS NOT NULL
            ORDER BY od.id, nr.recipient_type
        )
        SELECT * FROM distinct_notes
        ORDER BY archived_at DESC
        LIMIT $2 OFFSET $3
    """


def get_archived_notes_multi_sector_count_query() -> str:
    """Cuenta notas ARCHIVADAS en CUALQUIERA de los sectores dados.
    Params: $1=sector_ids[].
    """
    return """
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = true
        WHERE od.signed_at IS NOT NULL
    """


def get_archived_notes_multi_sector_search_query() -> str:
    """
    Obtiene notas ARCHIVADAS con filtro de búsqueda ILIKE.
    Params: $1=sector_ids[], $2-$4=pattern ILIKE, $5=search_term, $6=limit, $7=offset.
    """
    return """
        WITH distinct_notes AS (
            SELECT DISTINCT ON (od.id)
                od.id,
                od.official_number,
                od.reference,
                od.signed_at,
                od.resume as ai_summary,
                dt.acronym as document_type,
                nr.recipient_type,
                nr.sector_id as matched_sector_id,
                nr.sender_sector_id,
                nr.is_archived,
                nr.archived_at,
                ss.acronym as sender_sector_acronym,
                sd.name as sender_department_name,
                (
                    SELECT json_build_object(
                        'opened', EXISTS(
                            SELECT 1 FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                        ),
                        'opened_at', (
                            SELECT no.opened_at FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                            LIMIT 1
                        )
                    )
                ) as read_status
            FROM official_documents od
            JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = true
            JOIN document_types dt ON dt.id = od.document_type_id
            JOIN sectors ss ON ss.id = nr.sender_sector_id
            JOIN departments sd ON sd.id = ss.department_id
            WHERE od.signed_at IS NOT NULL
            AND (
                od.official_number ILIKE $2
                OR od.reference ILIKE $3
                OR od.content->>'html' ILIKE $4
                OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
            )
            ORDER BY od.id, nr.recipient_type
        )
        SELECT * FROM distinct_notes
        ORDER BY archived_at DESC
        LIMIT $6 OFFSET $7
    """


def get_archived_notes_multi_sector_search_count_query() -> str:
    """Cuenta notas ARCHIVADAS con filtro de búsqueda ILIKE.
    Params: $1=sector_ids[], $2-$4=pattern ILIKE, $5=search_term.
    """
    return """
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = true
        WHERE od.signed_at IS NOT NULL
        AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
        )
    """
