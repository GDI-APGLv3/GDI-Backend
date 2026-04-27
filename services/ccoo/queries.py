"""
SQL queries para el modulo de CCOO (Comunicaciones Oficiales).
Queries UNION ALL que combinan notas y memos en una sola consulta paginada.

Nota sobre exclusividad: No puede haber duplicados entre subqueries porque
document_type_id es exclusivo: un documento es NOTA o MEMO, nunca ambos.
"""


# ============================================================================
# QUERIES RECEIVED (bandeja de entrada unificada)
# ============================================================================


def get_received_ccoo_query(date_where: str = "") -> str:
    """
    Obtiene CCOO recibidas (notas + memos) con paginacion, NO archivadas.
    Usa named parameters %(param)s para evitar errores de orden.
    """
    return f"""
        WITH ccoo AS (
            (
                SELECT DISTINCT ON (od.id)
                    od.id,
                    od.official_number,
                    od.reference,
                    od.signed_at,
                    od.resume as ai_summary,
                    dt.acronym as document_type,
                    nr.recipient_type::text,
                    'NOTA'::text as ccoo_type,
                    json_build_object(
                        'label', ss.acronym,
                        'detail', sd.name,
                        'type', 'sector'
                    ) as sender,
                    json_build_object(
                        'opened', EXISTS(
                            SELECT 1 FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                        ),
                        'opened_at', (
                            SELECT no.opened_at FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                            LIMIT 1
                        )
                    ) as read_status
                FROM official_documents od
                JOIN notes_recipients nr ON nr.document_id = od.id
                    AND nr.sector_id = ANY(%(sector_ids)s::uuid[])
                    AND nr.is_archived = false
                JOIN document_types dt ON dt.id = od.document_type_id
                JOIN sectors ss ON ss.id = nr.sender_sector_id
                JOIN departments sd ON sd.id = ss.department_id
                WHERE od.signed_at IS NOT NULL {date_where}
                ORDER BY od.id, nr.recipient_type
            )
            UNION ALL
            (
                SELECT
                    od.id,
                    od.official_number,
                    od.reference,
                    od.signed_at,
                    od.resume as ai_summary,
                    dt.acronym as document_type,
                    mr.recipient_type::text,
                    'MEMO'::text as ccoo_type,
                    json_build_object(
                        'label', u_sender.full_name,
                        'detail', COALESCE(s_sender.acronym, ''),
                        'type', 'user'
                    ) as sender,
                    json_build_object(
                        'opened', mr.opened_at IS NOT NULL,
                        'opened_at', mr.opened_at
                    ) as read_status
                FROM official_documents od
                JOIN memo_recipients mr ON mr.document_id = od.id
                    AND mr.recipient_user_id = %(user_id)s
                    AND mr.is_archived = false
                JOIN document_types dt ON dt.id = od.document_type_id
                JOIN users u_sender ON u_sender.id = mr.sender_user_id
                LEFT JOIN sectors s_sender ON s_sender.id = mr.sender_sector_id
                WHERE od.signed_at IS NOT NULL {date_where}
            )
        )
        SELECT * FROM ccoo
        ORDER BY signed_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """


def get_received_ccoo_count_query(date_where: str = "") -> str:
    """
    Cuenta CCOO recibidas como suma de 2 counts separados.
    Mas eficiente que COUNT sobre UNION ALL.
    """
    return f"""
        SELECT (
            (SELECT COUNT(DISTINCT od.id) FROM official_documents od
             JOIN notes_recipients nr ON nr.document_id = od.id
                AND nr.sector_id = ANY(%(sector_ids)s::uuid[])
                AND nr.is_archived = false
             JOIN document_types dt ON dt.id = od.document_type_id
             WHERE od.signed_at IS NOT NULL {date_where})
            +
            (SELECT COUNT(*) FROM official_documents od
             JOIN memo_recipients mr ON mr.document_id = od.id
                AND mr.recipient_user_id = %(user_id)s
                AND mr.is_archived = false
             JOIN document_types dt ON dt.id = od.document_type_id
             WHERE od.signed_at IS NOT NULL {date_where})
        ) as total
    """


def get_received_ccoo_search_query(date_where: str = "") -> str:
    """
    Obtiene CCOO recibidas con filtro de busqueda ILIKE, NO archivadas.
    """
    return f"""
        WITH ccoo AS (
            (
                SELECT DISTINCT ON (od.id)
                    od.id,
                    od.official_number,
                    od.reference,
                    od.signed_at,
                    od.resume as ai_summary,
                    dt.acronym as document_type,
                    nr.recipient_type::text,
                    'NOTA'::text as ccoo_type,
                    json_build_object(
                        'label', ss.acronym,
                        'detail', sd.name,
                        'type', 'sector'
                    ) as sender,
                    json_build_object(
                        'opened', EXISTS(
                            SELECT 1 FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                        ),
                        'opened_at', (
                            SELECT no.opened_at FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                            LIMIT 1
                        )
                    ) as read_status
                FROM official_documents od
                JOIN notes_recipients nr ON nr.document_id = od.id
                    AND nr.sector_id = ANY(%(sector_ids)s::uuid[])
                    AND nr.is_archived = false
                JOIN document_types dt ON dt.id = od.document_type_id
                JOIN sectors ss ON ss.id = nr.sender_sector_id
                JOIN departments sd ON sd.id = ss.department_id
                WHERE od.signed_at IS NOT NULL
                AND (
                    od.official_number ILIKE %(search_pattern)s
                    OR od.reference ILIKE %(search_pattern)s
                    OR od.content->>'html' ILIKE %(search_pattern)s
                    OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
                )
                {date_where}
                ORDER BY od.id, nr.recipient_type
            )
            UNION ALL
            (
                SELECT
                    od.id,
                    od.official_number,
                    od.reference,
                    od.signed_at,
                    od.resume as ai_summary,
                    dt.acronym as document_type,
                    mr.recipient_type::text,
                    'MEMO'::text as ccoo_type,
                    json_build_object(
                        'label', u_sender.full_name,
                        'detail', COALESCE(s_sender.acronym, ''),
                        'type', 'user'
                    ) as sender,
                    json_build_object(
                        'opened', mr.opened_at IS NOT NULL,
                        'opened_at', mr.opened_at
                    ) as read_status
                FROM official_documents od
                JOIN memo_recipients mr ON mr.document_id = od.id
                    AND mr.recipient_user_id = %(user_id)s
                    AND mr.is_archived = false
                JOIN document_types dt ON dt.id = od.document_type_id
                JOIN users u_sender ON u_sender.id = mr.sender_user_id
                LEFT JOIN sectors s_sender ON s_sender.id = mr.sender_sector_id
                WHERE od.signed_at IS NOT NULL
                AND (
                    od.official_number ILIKE %(search_pattern)s
                    OR od.reference ILIKE %(search_pattern)s
                    OR od.content->>'html' ILIKE %(search_pattern)s
                    OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
                )
                {date_where}
            )
        )
        SELECT * FROM ccoo
        ORDER BY signed_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """


def get_received_ccoo_search_count_query(date_where: str = "") -> str:
    """Cuenta CCOO recibidas con filtro de busqueda ILIKE."""
    return f"""
        SELECT (
            (SELECT COUNT(DISTINCT od.id) FROM official_documents od
             JOIN notes_recipients nr ON nr.document_id = od.id
                AND nr.sector_id = ANY(%(sector_ids)s::uuid[])
                AND nr.is_archived = false
             JOIN document_types dt ON dt.id = od.document_type_id
             WHERE od.signed_at IS NOT NULL
             AND (
                od.official_number ILIKE %(search_pattern)s
                OR od.reference ILIKE %(search_pattern)s
                OR od.content->>'html' ILIKE %(search_pattern)s
                OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
             )
             {date_where})
            +
            (SELECT COUNT(*) FROM official_documents od
             JOIN memo_recipients mr ON mr.document_id = od.id
                AND mr.recipient_user_id = %(user_id)s
                AND mr.is_archived = false
             JOIN document_types dt ON dt.id = od.document_type_id
             WHERE od.signed_at IS NOT NULL
             AND (
                od.official_number ILIKE %(search_pattern)s
                OR od.reference ILIKE %(search_pattern)s
                OR od.content->>'html' ILIKE %(search_pattern)s
                OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
             )
             {date_where})
        ) as total
    """


# ============================================================================
# QUERIES SENT (bandeja de enviados unificada)
# ============================================================================


def get_sent_ccoo_query(date_where: str = "") -> str:
    """
    Obtiene CCOO enviadas (notas + memos) con paginacion.
    Notas: filtra por sender_sector_id = ANY(sector_ids)
    Memos: filtra por sender_user_id = user_id
    Normaliza recipients como recipients_label (primer destinatario) y recipients_count.
    """
    return f"""
        WITH ccoo AS (
            (
                SELECT
                    od.id,
                    od.official_number,
                    od.reference,
                    od.signed_at,
                    od.resume as ai_summary,
                    dt.acronym as document_type,
                    'NOTA'::text as ccoo_type,
                    (
                        SELECT s2.acronym
                        FROM notes_recipients nr2
                        JOIN sectors s2 ON s2.id = nr2.sector_id
                        WHERE nr2.document_id = od.id AND nr2.recipient_type = 'TO'
                        LIMIT 1
                    ) as recipients_label,
                    (
                        SELECT COUNT(DISTINCT nr2.sector_id)::int
                        FROM notes_recipients nr2
                        WHERE nr2.document_id = od.id
                    ) as recipients_count,
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
                    WHERE nr.sender_sector_id = ANY(%(sector_ids)s::uuid[])
                )
                {date_where}
            )
            UNION ALL
            (
                SELECT * FROM (
                    SELECT DISTINCT ON (od.id)
                        od.id,
                        od.official_number,
                        od.reference,
                        od.signed_at,
                        od.resume as ai_summary,
                        dt.acronym as document_type,
                        'MEMO'::text as ccoo_type,
                        (
                            SELECT u2.full_name
                            FROM memo_recipients mr2
                            JOIN users u2 ON u2.id = mr2.recipient_user_id
                            WHERE mr2.document_id = od.id AND mr2.recipient_type = 'TO'
                            LIMIT 1
                        ) as recipients_label,
                        (
                            SELECT COUNT(*)::int
                            FROM memo_recipients mr2
                            WHERE mr2.document_id = od.id
                        ) as recipients_count,
                        (
                            SELECT COUNT(*)::int
                            FROM memo_recipients mr3
                            WHERE mr3.document_id = od.id AND mr3.opened_at IS NOT NULL
                        ) as openings_count
                    FROM official_documents od
                    JOIN memo_recipients mr ON mr.document_id = od.id
                    JOIN document_types dt ON dt.id = od.document_type_id
                    WHERE od.signed_at IS NOT NULL
                    AND mr.sender_user_id = %(user_id)s
                    {date_where}
                    ORDER BY od.id, od.signed_at DESC
                ) sub
            )
        )
        SELECT * FROM ccoo
        ORDER BY signed_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """


def get_sent_ccoo_count_query(date_where: str = "") -> str:
    """Cuenta CCOO enviadas como suma de 2 counts separados."""
    return f"""
        SELECT (
            (SELECT COUNT(DISTINCT od.id) FROM official_documents od
             JOIN notes_recipients nr ON nr.document_id = od.id
             WHERE od.signed_at IS NOT NULL
             AND nr.sender_sector_id = ANY(%(sector_ids)s::uuid[])
             {date_where})
            +
            (SELECT COUNT(DISTINCT od.id) FROM official_documents od
             JOIN memo_recipients mr ON mr.document_id = od.id
             WHERE od.signed_at IS NOT NULL
             AND mr.sender_user_id = %(user_id)s
             {date_where})
        ) as total
    """


def get_sent_ccoo_search_query(date_where: str = "") -> str:
    """Obtiene CCOO enviadas con filtro de busqueda ILIKE."""
    return f"""
        WITH ccoo AS (
            (
                SELECT
                    od.id,
                    od.official_number,
                    od.reference,
                    od.signed_at,
                    od.resume as ai_summary,
                    dt.acronym as document_type,
                    'NOTA'::text as ccoo_type,
                    (
                        SELECT s2.acronym
                        FROM notes_recipients nr2
                        JOIN sectors s2 ON s2.id = nr2.sector_id
                        WHERE nr2.document_id = od.id AND nr2.recipient_type = 'TO'
                        LIMIT 1
                    ) as recipients_label,
                    (
                        SELECT COUNT(DISTINCT nr2.sector_id)::int
                        FROM notes_recipients nr2
                        WHERE nr2.document_id = od.id
                    ) as recipients_count,
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
                    WHERE nr.sender_sector_id = ANY(%(sector_ids)s::uuid[])
                )
                AND (
                    od.official_number ILIKE %(search_pattern)s
                    OR od.reference ILIKE %(search_pattern)s
                    OR od.content->>'html' ILIKE %(search_pattern)s
                    OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
                )
                {date_where}
            )
            UNION ALL
            (
                SELECT * FROM (
                    SELECT DISTINCT ON (od.id)
                        od.id,
                        od.official_number,
                        od.reference,
                        od.signed_at,
                        od.resume as ai_summary,
                        dt.acronym as document_type,
                        'MEMO'::text as ccoo_type,
                        (
                            SELECT u2.full_name
                            FROM memo_recipients mr2
                            JOIN users u2 ON u2.id = mr2.recipient_user_id
                            WHERE mr2.document_id = od.id AND mr2.recipient_type = 'TO'
                            LIMIT 1
                        ) as recipients_label,
                        (
                            SELECT COUNT(*)::int
                            FROM memo_recipients mr2
                            WHERE mr2.document_id = od.id
                        ) as recipients_count,
                        (
                            SELECT COUNT(*)::int
                            FROM memo_recipients mr3
                            WHERE mr3.document_id = od.id AND mr3.opened_at IS NOT NULL
                        ) as openings_count
                    FROM official_documents od
                    JOIN memo_recipients mr ON mr.document_id = od.id
                    JOIN document_types dt ON dt.id = od.document_type_id
                    WHERE od.signed_at IS NOT NULL
                    AND mr.sender_user_id = %(user_id)s
                    AND (
                        od.official_number ILIKE %(search_pattern)s
                        OR od.reference ILIKE %(search_pattern)s
                        OR od.content->>'html' ILIKE %(search_pattern)s
                        OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
                    )
                    {date_where}
                    ORDER BY od.id, od.signed_at DESC
                ) sub
            )
        )
        SELECT * FROM ccoo
        ORDER BY signed_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """


def get_sent_ccoo_search_count_query(date_where: str = "") -> str:
    """Cuenta CCOO enviadas con filtro de busqueda ILIKE."""
    return f"""
        SELECT (
            (SELECT COUNT(DISTINCT od.id) FROM official_documents od
             JOIN notes_recipients nr ON nr.document_id = od.id
             WHERE od.signed_at IS NOT NULL
             AND nr.sender_sector_id = ANY(%(sector_ids)s::uuid[])
             AND (
                od.official_number ILIKE %(search_pattern)s
                OR od.reference ILIKE %(search_pattern)s
                OR od.content->>'html' ILIKE %(search_pattern)s
                OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
             )
             {date_where})
            +
            (SELECT COUNT(DISTINCT od.id) FROM official_documents od
             JOIN memo_recipients mr ON mr.document_id = od.id
             WHERE od.signed_at IS NOT NULL
             AND mr.sender_user_id = %(user_id)s
             AND (
                od.official_number ILIKE %(search_pattern)s
                OR od.reference ILIKE %(search_pattern)s
                OR od.content->>'html' ILIKE %(search_pattern)s
                OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
             )
             {date_where})
        ) as total
    """


# ============================================================================
# QUERIES ARCHIVED (bandeja de archivados unificada)
# ============================================================================


def get_archived_ccoo_query() -> str:
    """
    Obtiene CCOO archivadas (notas + memos) con paginacion.
    Orden por archived_at DESC.
    """
    return """
        WITH ccoo AS (
            (
                SELECT DISTINCT ON (od.id)
                    od.id,
                    od.official_number,
                    od.reference,
                    od.signed_at,
                    od.resume as ai_summary,
                    dt.acronym as document_type,
                    nr.recipient_type::text,
                    'NOTA'::text as ccoo_type,
                    json_build_object(
                        'label', ss.acronym,
                        'detail', sd.name,
                        'type', 'sector'
                    ) as sender,
                    json_build_object(
                        'opened', EXISTS(
                            SELECT 1 FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                        ),
                        'opened_at', (
                            SELECT no.opened_at FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                            LIMIT 1
                        )
                    ) as read_status,
                    nr.archived_at
                FROM official_documents od
                JOIN notes_recipients nr ON nr.document_id = od.id
                    AND nr.sector_id = ANY(%(sector_ids)s::uuid[])
                    AND nr.is_archived = true
                JOIN document_types dt ON dt.id = od.document_type_id
                JOIN sectors ss ON ss.id = nr.sender_sector_id
                JOIN departments sd ON sd.id = ss.department_id
                WHERE od.signed_at IS NOT NULL
                ORDER BY od.id, nr.recipient_type
            )
            UNION ALL
            (
                SELECT
                    od.id,
                    od.official_number,
                    od.reference,
                    od.signed_at,
                    od.resume as ai_summary,
                    dt.acronym as document_type,
                    mr.recipient_type::text,
                    'MEMO'::text as ccoo_type,
                    json_build_object(
                        'label', u_sender.full_name,
                        'detail', COALESCE(s_sender.acronym, ''),
                        'type', 'user'
                    ) as sender,
                    json_build_object(
                        'opened', mr.opened_at IS NOT NULL,
                        'opened_at', mr.opened_at
                    ) as read_status,
                    mr.archived_at
                FROM official_documents od
                JOIN memo_recipients mr ON mr.document_id = od.id
                    AND mr.recipient_user_id = %(user_id)s
                    AND mr.is_archived = true
                JOIN document_types dt ON dt.id = od.document_type_id
                JOIN users u_sender ON u_sender.id = mr.sender_user_id
                LEFT JOIN sectors s_sender ON s_sender.id = mr.sender_sector_id
                WHERE od.signed_at IS NOT NULL
            )
        )
        SELECT * FROM ccoo
        ORDER BY archived_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """


def get_archived_ccoo_count_query() -> str:
    """Cuenta CCOO archivadas como suma de 2 counts separados."""
    return """
        SELECT (
            (SELECT COUNT(DISTINCT od.id) FROM official_documents od
             JOIN notes_recipients nr ON nr.document_id = od.id
                AND nr.sector_id = ANY(%(sector_ids)s::uuid[])
                AND nr.is_archived = true
             WHERE od.signed_at IS NOT NULL)
            +
            (SELECT COUNT(*) FROM official_documents od
             JOIN memo_recipients mr ON mr.document_id = od.id
                AND mr.recipient_user_id = %(user_id)s
                AND mr.is_archived = true
             WHERE od.signed_at IS NOT NULL)
        ) as total
    """


def get_archived_ccoo_search_query() -> str:
    """Obtiene CCOO archivadas con filtro de busqueda ILIKE."""
    return """
        WITH ccoo AS (
            (
                SELECT DISTINCT ON (od.id)
                    od.id,
                    od.official_number,
                    od.reference,
                    od.signed_at,
                    od.resume as ai_summary,
                    dt.acronym as document_type,
                    nr.recipient_type::text,
                    'NOTA'::text as ccoo_type,
                    json_build_object(
                        'label', ss.acronym,
                        'detail', sd.name,
                        'type', 'sector'
                    ) as sender,
                    json_build_object(
                        'opened', EXISTS(
                            SELECT 1 FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                        ),
                        'opened_at', (
                            SELECT no.opened_at FROM notes_openings no
                            WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                            LIMIT 1
                        )
                    ) as read_status,
                    nr.archived_at
                FROM official_documents od
                JOIN notes_recipients nr ON nr.document_id = od.id
                    AND nr.sector_id = ANY(%(sector_ids)s::uuid[])
                    AND nr.is_archived = true
                JOIN document_types dt ON dt.id = od.document_type_id
                JOIN sectors ss ON ss.id = nr.sender_sector_id
                JOIN departments sd ON sd.id = ss.department_id
                WHERE od.signed_at IS NOT NULL
                AND (
                    od.official_number ILIKE %(search_pattern)s
                    OR od.reference ILIKE %(search_pattern)s
                    OR od.content->>'html' ILIKE %(search_pattern)s
                    OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
                )
                ORDER BY od.id, nr.recipient_type
            )
            UNION ALL
            (
                SELECT
                    od.id,
                    od.official_number,
                    od.reference,
                    od.signed_at,
                    od.resume as ai_summary,
                    dt.acronym as document_type,
                    mr.recipient_type::text,
                    'MEMO'::text as ccoo_type,
                    json_build_object(
                        'label', u_sender.full_name,
                        'detail', COALESCE(s_sender.acronym, ''),
                        'type', 'user'
                    ) as sender,
                    json_build_object(
                        'opened', mr.opened_at IS NOT NULL,
                        'opened_at', mr.opened_at
                    ) as read_status,
                    mr.archived_at
                FROM official_documents od
                JOIN memo_recipients mr ON mr.document_id = od.id
                    AND mr.recipient_user_id = %(user_id)s
                    AND mr.is_archived = true
                JOIN document_types dt ON dt.id = od.document_type_id
                JOIN users u_sender ON u_sender.id = mr.sender_user_id
                LEFT JOIN sectors s_sender ON s_sender.id = mr.sender_sector_id
                WHERE od.signed_at IS NOT NULL
                AND (
                    od.official_number ILIKE %(search_pattern)s
                    OR od.reference ILIKE %(search_pattern)s
                    OR od.content->>'html' ILIKE %(search_pattern)s
                    OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
                )
            )
        )
        SELECT * FROM ccoo
        ORDER BY archived_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """


def get_archived_ccoo_search_count_query() -> str:
    """Cuenta CCOO archivadas con filtro de busqueda ILIKE."""
    return """
        SELECT (
            (SELECT COUNT(DISTINCT od.id) FROM official_documents od
             JOIN notes_recipients nr ON nr.document_id = od.id
                AND nr.sector_id = ANY(%(sector_ids)s::uuid[])
                AND nr.is_archived = true
             WHERE od.signed_at IS NOT NULL
             AND (
                od.official_number ILIKE %(search_pattern)s
                OR od.reference ILIKE %(search_pattern)s
                OR od.content->>'html' ILIKE %(search_pattern)s
                OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
             ))
            +
            (SELECT COUNT(*) FROM official_documents od
             JOIN memo_recipients mr ON mr.document_id = od.id
                AND mr.recipient_user_id = %(user_id)s
                AND mr.is_archived = true
             WHERE od.signed_at IS NOT NULL
             AND (
                od.official_number ILIKE %(search_pattern)s
                OR od.reference ILIKE %(search_pattern)s
                OR od.content->>'html' ILIKE %(search_pattern)s
                OR similarity(LOWER(COALESCE(od.reference, '')), LOWER(%(search_term)s)) > 0.3
             ))
        ) as total
    """
