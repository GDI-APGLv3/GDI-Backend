"""
SQL queries para el modulo de MEMOS.
Queries centralizadas para operaciones de recipients, tracking y consultas.

Diferencias clave con NOTAS:
- memo_recipients en vez de notes_recipients
- recipient_user_id (persona) en vez de sector_id (sector)
- sender_user_id (persona) en vez de sender_sector_id (sector)
- opened_at inline en memo_recipients (no hay tabla notes_openings separada)
- No hay variantes multi_sector (query directa por user_id)
- JOINs con users en vez de sectors/departments
- document_id referencia document_draft.id; official_documents.id ES el mismo UUID que document_draft.id
"""


def check_memo_document_type_query() -> str:
    """Verifica si un document_type_id corresponde a MEMO."""
    return """
        SELECT id FROM document_types
        WHERE id = $1 AND acronym = 'MEMO'
    """


def check_memo_by_acronym_query() -> str:
    """Obtiene el ID del tipo MEMO por acronym."""
    return """
        SELECT id FROM document_types
        WHERE acronym = 'MEMO' AND is_active = true
    """


def validate_users_exist_query() -> str:
    """Valida que los user_ids existan y esten activos."""
    return """
        SELECT id FROM users
        WHERE id::text = ANY($1) AND estado = 1
    """


def insert_memo_recipient_query() -> str:
    """Inserta un destinatario de memo."""
    return """
        INSERT INTO memo_recipients
            (document_id, recipient_user_id, sender_user_id, recipient_type,
             recipient_sector_id, sender_sector_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
    """


def get_user_sector_id_query() -> str:
    """Obtiene el sector_id actual de un usuario (para snapshot)."""
    return """
        SELECT sector_id FROM users WHERE id = $1
    """


def get_recipients_by_document_query() -> str:
    """Obtiene todos los recipients de un memo."""
    return """
        SELECT
            mr.id,
            mr.document_id,
            mr.recipient_user_id,
            mr.recipient_type,
            mr.sender_user_id,
            mr.recipient_sector_id,
            mr.sender_sector_id,
            u.full_name as recipient_name,
            s.acronym as recipient_sector_acronym
        FROM memo_recipients mr
        JOIN users u ON u.id = mr.recipient_user_id
        LEFT JOIN sectors s ON s.id = mr.recipient_sector_id
        WHERE mr.document_id = $1
        ORDER BY
            CASE mr.recipient_type
                WHEN 'TO' THEN 1
                WHEN 'CC' THEN 2
                WHEN 'BCC' THEN 3
            END,
            u.full_name
    """


def get_sender_user_query() -> str:
    """Obtiene el user_id del sender de un memo."""
    return """
        SELECT DISTINCT sender_user_id
        FROM memo_recipients
        WHERE document_id = $1
        LIMIT 1
    """


def check_user_is_recipient_query() -> str:
    """Verifica si un usuario es recipient de un memo."""
    return """
        SELECT recipient_type
        FROM memo_recipients
        WHERE document_id = $1 AND recipient_user_id = $2
    """


def check_user_is_sender_query() -> str:
    """Verifica si un usuario es el sender de un memo."""
    return """
        SELECT EXISTS(
            SELECT 1 FROM memo_recipients
            WHERE document_id = $1 AND sender_user_id = $2
        ) as is_sender
    """


def record_memo_opening_query() -> str:
    """Marca un memo como abierto (solo si opened_at es NULL)."""
    return """
        UPDATE memo_recipients
        SET opened_at = NOW()
        WHERE document_id = $1 AND recipient_user_id = $2 AND opened_at IS NULL
        RETURNING id, opened_at
    """


def get_memo_opening_query() -> str:
    """Obtiene el estado de apertura de un memo para un usuario."""
    return """
        SELECT opened_at
        FROM memo_recipients
        WHERE document_id = $1 AND recipient_user_id = $2
    """


def get_openings_by_document_query() -> str:
    """Obtiene todas las aperturas de un memo (para el sender)."""
    return """
        SELECT
            mr.recipient_user_id as user_id,
            mr.recipient_type,
            mr.opened_at,
            u.full_name as user_name,
            s.acronym as sector_acronym
        FROM memo_recipients mr
        JOIN users u ON u.id = mr.recipient_user_id
        LEFT JOIN sectors s ON s.id = mr.recipient_sector_id
        WHERE mr.document_id = $1
        ORDER BY
            CASE mr.recipient_type
                WHEN 'TO' THEN 1
                WHEN 'CC' THEN 2
                WHEN 'BCC' THEN 3
            END,
            mr.opened_at NULLS LAST
    """


def get_memo_detail_query() -> str:
    """Obtiene el detalle completo de un memo para un visor."""
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


# ============================================================================
# QUERIES PARA BANDEJA RECIBIDOS
# ============================================================================


def get_received_memos_query(date_where: str = "") -> str:
    """
    Obtiene memos recibidos por un usuario (solo oficializados, NO archivados).
    Params posicionales: $1=user_id, $2=limit, $3=offset [+ date params si hay]
    """
    return f"""
        SELECT
            od.id,
            od.official_number,
            od.reference,
            od.signed_at,
            od.resume as ai_summary,
            dt.acronym as document_type,
            mr.recipient_type,
            mr.opened_at,
            mr.sender_user_id,
            u_sender.full_name as sender_name,
            s_sender.acronym as sender_sector_acronym
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        JOIN document_types dt ON dt.id = od.document_type_id
        JOIN users u_sender ON u_sender.id = mr.sender_user_id
        LEFT JOIN sectors s_sender ON s_sender.id = mr.sender_sector_id
        WHERE od.signed_at IS NOT NULL
        AND mr.recipient_user_id = $1 AND mr.is_archived = false
        {date_where}
        ORDER BY od.signed_at DESC
        LIMIT $2 OFFSET $3
    """


def get_received_memos_count_query(date_where: str = "") -> str:
    """Cuenta memos recibidos por un usuario (NO archivados). Params: $1=user_id."""
    return f"""
        SELECT COUNT(*)::int as total
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND mr.recipient_user_id = $1 AND mr.is_archived = false
        {date_where}
    """


def get_received_memos_search_query(date_where: str = "") -> str:
    """
    Obtiene memos recibidos con filtro de busqueda ILIKE (NO archivados).
    Params: $1=user_id, $2=search_pattern, $3=search_pattern, $4=search_pattern,
            $5=search_term, [date params...], $N=limit, $N+1=offset
    """
    return f"""
        SELECT
            od.id,
            od.official_number,
            od.reference,
            od.signed_at,
            od.resume as ai_summary,
            dt.acronym as document_type,
            mr.recipient_type,
            mr.opened_at,
            mr.sender_user_id,
            u_sender.full_name as sender_name,
            s_sender.acronym as sender_sector_acronym
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        JOIN document_types dt ON dt.id = od.document_type_id
        JOIN users u_sender ON u_sender.id = mr.sender_user_id
        LEFT JOIN sectors s_sender ON s_sender.id = mr.sender_sector_id
        WHERE od.signed_at IS NOT NULL
        AND mr.recipient_user_id = $1 AND mr.is_archived = false
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


def get_received_memos_search_count_query(date_where: str = "") -> str:
    """Cuenta memos recibidos con filtro de busqueda ILIKE (NO archivados)."""
    return f"""
        SELECT COUNT(*)::int as total
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND mr.recipient_user_id = $1 AND mr.is_archived = false
        AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
        )
        {date_where}
    """


# ============================================================================
# QUERIES PARA BANDEJA ENVIADOS
# ============================================================================


def get_sent_memos_query(date_where: str = "") -> str:
    """
    Obtiene memos enviados por un usuario (solo oficializados).
    Params: $1=sender_user_id, [date params...], $N=limit, $N+1=offset
    """
    return f"""
        SELECT * FROM (
            SELECT DISTINCT ON (od.id)
                od.id,
                od.official_number,
                od.reference,
                od.signed_at,
                od.resume as ai_summary,
                dt.acronym as document_type,
                (
                    SELECT json_agg(json_build_object(
                        'user_id', mr2.recipient_user_id,
                        'type', mr2.recipient_type,
                        'full_name', u2.full_name,
                        'sector_acronym', s2.acronym
                    ))
                    FROM memo_recipients mr2
                    JOIN users u2 ON u2.id = mr2.recipient_user_id
                    LEFT JOIN sectors s2 ON s2.id = mr2.recipient_sector_id
                    WHERE mr2.document_id = od.id
                ) as recipients,
                (
                    SELECT COUNT(*)::int
                    FROM memo_recipients mr3
                    WHERE mr3.document_id = od.id AND mr3.opened_at IS NOT NULL
                ) as openings_count
            FROM official_documents od
            JOIN memo_recipients mr ON mr.document_id = od.id
            JOIN document_types dt ON dt.id = od.document_type_id
            WHERE od.signed_at IS NOT NULL
            AND mr.sender_user_id = $1
            {date_where}
            ORDER BY od.id, od.signed_at DESC
        ) sub
        ORDER BY sub.signed_at DESC
        LIMIT $2 OFFSET $3
    """


def get_sent_memos_count_query(date_where: str = "") -> str:
    """Cuenta memos enviados por un usuario. Params: $1=sender_user_id."""
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND mr.sender_user_id = $1
        {date_where}
    """


def get_sent_memos_search_query(date_where: str = "") -> str:
    """
    Obtiene memos enviados con filtro de busqueda ILIKE.
    Params: $1=sender_user_id, $2-$5=search params, [date params...], $N=limit, $N+1=offset
    """
    return f"""
        SELECT * FROM (
            SELECT DISTINCT ON (od.id)
                od.id,
                od.official_number,
                od.reference,
                od.signed_at,
                od.resume as ai_summary,
                dt.acronym as document_type,
                (
                    SELECT json_agg(json_build_object(
                        'user_id', mr2.recipient_user_id,
                        'type', mr2.recipient_type,
                        'full_name', u2.full_name,
                        'sector_acronym', s2.acronym
                    ))
                    FROM memo_recipients mr2
                    JOIN users u2 ON u2.id = mr2.recipient_user_id
                    LEFT JOIN sectors s2 ON s2.id = mr2.recipient_sector_id
                    WHERE mr2.document_id = od.id
                ) as recipients,
                (
                    SELECT COUNT(*)::int
                    FROM memo_recipients mr3
                    WHERE mr3.document_id = od.id AND mr3.opened_at IS NOT NULL
                ) as openings_count
            FROM official_documents od
            JOIN memo_recipients mr ON mr.document_id = od.id
            JOIN document_types dt ON dt.id = od.document_type_id
            WHERE od.signed_at IS NOT NULL
            AND mr.sender_user_id = $1
            AND (
                od.official_number ILIKE $2
                OR od.reference ILIKE $3
                OR od.content->>'html' ILIKE $4
                OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
            )
            {date_where}
            ORDER BY od.id, od.signed_at DESC
        ) sub
        ORDER BY sub.signed_at DESC
        LIMIT $6 OFFSET $7
    """


def get_sent_memos_search_count_query(date_where: str = "") -> str:
    """Cuenta memos enviados con filtro de busqueda ILIKE."""
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND mr.sender_user_id = $1
        AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
        )
        {date_where}
    """


# ============================================================================
# QUERIES PARA ARCHIVADO
# ============================================================================


def update_memo_archived_status_query() -> str:
    """Actualiza el estado de archivado de un memo para un usuario especifico."""
    return """
        UPDATE memo_recipients
        SET is_archived = $1,
            archived_at = CASE WHEN $2 = true THEN NOW() ELSE NULL END
        WHERE document_id = $3 AND recipient_user_id = $4
        RETURNING id, is_archived, archived_at
    """


def get_memo_recipient_info_query() -> str:
    """Obtiene informacion del recipient incluyendo estado de archivado."""
    return """
        SELECT
            mr.id,
            mr.document_id,
            mr.recipient_user_id,
            mr.recipient_type,
            mr.sender_user_id,
            mr.is_archived,
            mr.archived_at,
            mr.opened_at
        FROM memo_recipients mr
        WHERE mr.document_id = $1 AND mr.recipient_user_id = $2
    """


def get_archived_memos_query() -> str:
    """Obtiene memos ARCHIVADOS de un usuario, con paginacion. Params: $1=user_id, $2=limit, $3=offset"""
    return """
        SELECT
            od.id,
            od.official_number,
            od.reference,
            od.signed_at,
            od.resume as ai_summary,
            dt.acronym as document_type,
            mr.recipient_type,
            mr.opened_at,
            mr.is_archived,
            mr.archived_at,
            mr.sender_user_id,
            u_sender.full_name as sender_name,
            s_sender.acronym as sender_sector_acronym
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        JOIN document_types dt ON dt.id = od.document_type_id
        JOIN users u_sender ON u_sender.id = mr.sender_user_id
        LEFT JOIN sectors s_sender ON s_sender.id = mr.sender_sector_id
        WHERE od.signed_at IS NOT NULL
        AND mr.recipient_user_id = $1 AND mr.is_archived = true
        ORDER BY mr.archived_at DESC
        LIMIT $2 OFFSET $3
    """


def get_archived_memos_count_query() -> str:
    """Cuenta memos ARCHIVADOS de un usuario. Params: $1=user_id"""
    return """
        SELECT COUNT(*)::int as total
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND mr.recipient_user_id = $1 AND mr.is_archived = true
    """


def get_archived_memos_search_query() -> str:
    """
    Obtiene memos ARCHIVADOS con filtro de busqueda ILIKE.
    Params: $1=user_id, $2-$5=search params, $6=limit, $7=offset
    """
    return """
        SELECT
            od.id,
            od.official_number,
            od.reference,
            od.signed_at,
            od.resume as ai_summary,
            dt.acronym as document_type,
            mr.recipient_type,
            mr.opened_at,
            mr.is_archived,
            mr.archived_at,
            mr.sender_user_id,
            u_sender.full_name as sender_name,
            s_sender.acronym as sender_sector_acronym
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        JOIN document_types dt ON dt.id = od.document_type_id
        JOIN users u_sender ON u_sender.id = mr.sender_user_id
        LEFT JOIN sectors s_sender ON s_sender.id = mr.sender_sector_id
        WHERE od.signed_at IS NOT NULL
        AND mr.recipient_user_id = $1 AND mr.is_archived = true
        AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
        )
        ORDER BY mr.archived_at DESC
        LIMIT $6 OFFSET $7
    """


def get_archived_memos_search_count_query() -> str:
    """Cuenta memos ARCHIVADOS con filtro de busqueda ILIKE. Params: $1=user_id, $2-$5=search params"""
    return """
        SELECT COUNT(*)::int as total
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND mr.recipient_user_id = $1 AND mr.is_archived = true
        AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
        )
    """


# ============================================================================
# QUERY PARA CONTADOR NO LEIDOS (badge)
# ============================================================================


def get_unread_memo_count_query() -> str:
    """Cuenta memos no leidos de un usuario (para badge). Params: $1=user_id"""
    return """
        SELECT COUNT(*)::int as unread_count
        FROM memo_recipients mr
        JOIN official_documents od ON od.id = mr.document_id AND od.signed_at IS NOT NULL
        WHERE mr.recipient_user_id = $1 AND mr.is_archived = false AND mr.opened_at IS NULL
    """
