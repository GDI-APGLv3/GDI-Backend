

def check_memo_document_type_query() -> str:
    return """
        SELECT id FROM document_types
        WHERE id = $1 AND acronym = 'MEMO'
    """


def check_memo_by_acronym_query() -> str:
    return """
        SELECT id FROM document_types
        WHERE acronym = 'MEMO' AND is_active = true
    """


def validate_users_exist_query() -> str:
    return """
        SELECT id FROM users
        WHERE id::text = ANY($1) AND estado = 1
    """


def get_user_sector_id_query() -> str:
    return """
        SELECT sector_id FROM users WHERE id = $1
    """


def get_users_sector_ids_bulk_query() -> str:
    return """
        SELECT id, sector_id FROM users WHERE id = ANY($1::uuid[])
    """


def insert_memo_recipients_bulk_query() -> str:
    return """
        INSERT INTO memo_recipients
            (document_id, recipient_user_id, sender_user_id, recipient_type,
             recipient_sector_id, sender_sector_id)
        SELECT $1, r.recipient_user_id, $2, r.recipient_type, r.recipient_sector_id, $3
        FROM UNNEST($4::uuid[], $5::text[], $6::uuid[])
            AS r(recipient_user_id, recipient_type, recipient_sector_id)
    """


def get_recipients_by_document_query() -> str:
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
    return """
        SELECT DISTINCT sender_user_id
        FROM memo_recipients
        WHERE document_id = $1
        LIMIT 1
    """


def check_user_is_recipient_query() -> str:
    return """
        SELECT recipient_type
        FROM memo_recipients
        WHERE document_id = $1 AND recipient_user_id = $2
    """


def check_user_is_sender_query() -> str:
    return """
        SELECT EXISTS(
            SELECT 1 FROM memo_recipients
            WHERE document_id = $1 AND sender_user_id = $2
        ) as is_sender
    """


def record_memo_opening_query() -> str:
    return """
        UPDATE memo_recipients
        SET opened_at = NOW()
        WHERE document_id = $1 AND recipient_user_id = $2 AND opened_at IS NULL
        RETURNING id, opened_at
    """


def get_memo_opening_query() -> str:
    return """
        SELECT opened_at
        FROM memo_recipients
        WHERE document_id = $1 AND recipient_user_id = $2
    """


def get_openings_by_document_query() -> str:
    return """
        SELECT
            mr.recipient_user_id as user_id,
            mr.recipient_type,
            mr.opened_at,
            u.full_name as user_name,
            u.profile_picture_url as profile_picture_url,
            s.id as sector_id,
            s.acronym as sector_acronym,
            s.primary_color as sector_color,
            cs.name as seal_name
        FROM memo_recipients mr
        JOIN users u ON u.id = mr.recipient_user_id
        LEFT JOIN sectors s ON s.id = mr.recipient_sector_id
        LEFT JOIN user_seals us ON us.user_id = u.id
        LEFT JOIN city_seals cs ON cs.id = us.city_seal_id
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


def get_received_memos_query(date_where: str = "") -> str:
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
    return f"""
        SELECT COUNT(*)::int as total
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND mr.recipient_user_id = $1 AND mr.is_archived = false
        {date_where}
    """


def get_received_memos_search_query(date_where: str = "") -> str:
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


def get_sent_memos_query(date_where: str = "") -> str:
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
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND mr.sender_user_id = $1
        {date_where}
    """


def get_sent_memos_search_query(date_where: str = "") -> str:
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


def update_memo_archived_status_query() -> str:
    return """
        UPDATE memo_recipients
        SET is_archived = $1,
            archived_at = CASE WHEN $2 = true THEN NOW() ELSE NULL END
        WHERE document_id = $3 AND recipient_user_id = $4
        RETURNING id, is_archived, archived_at
    """


def get_memo_recipient_info_query() -> str:
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
    return """
        SELECT COUNT(*)::int as total
        FROM official_documents od
        JOIN memo_recipients mr ON mr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND mr.recipient_user_id = $1 AND mr.is_archived = true
    """


def get_archived_memos_search_query() -> str:
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


def get_unread_memo_count_query() -> str:
    return """
        SELECT COUNT(*)::int as unread_count
        FROM memo_recipients mr
        JOIN official_documents od ON od.id = mr.document_id AND od.signed_at IS NOT NULL
        WHERE mr.recipient_user_id = $1 AND mr.is_archived = false AND mr.opened_at IS NULL
    """
