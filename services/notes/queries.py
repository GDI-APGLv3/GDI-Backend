

def check_nota_document_type_query() -> str:
    return """
        SELECT id FROM document_types
        WHERE id = $1 AND acronym = 'NOTA'
    """


def check_nota_by_acronym_query() -> str:
    return """
        SELECT id FROM document_types
        WHERE acronym = 'NOTA' AND is_active = true
    """


def validate_sectors_exist_query() -> str:
    return """
        SELECT id FROM sectors
        WHERE id::text = ANY($1) AND is_active = true
    """


def insert_note_recipients_bulk_query() -> str:
    return """
        INSERT INTO notes_recipients (document_id, sector_id, recipient_type, sender_sector_id)
        SELECT $1, u.sector_id, u.recipient_type, $2
        FROM UNNEST($3::uuid[], $4::text[]) AS u(sector_id, recipient_type)
    """


def get_recipients_by_document_query() -> str:
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
    return """
        SELECT DISTINCT sender_sector_id
        FROM notes_recipients
        WHERE document_id = $1
        LIMIT 1
    """


def insert_note_opening_query() -> str:
    return """
        INSERT INTO notes_openings (document_id, sector_id, user_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (document_id, user_id) DO NOTHING
        RETURNING id
    """


def get_opening_by_document_user_query() -> str:
    return """
        SELECT id, opened_at
        FROM notes_openings
        WHERE document_id = $1 AND user_id = $2
    """


def get_openings_by_document_query() -> str:
    return """
        SELECT
            no.id,
            no.sector_id,
            no.user_id,
            no.opened_at,
            u.full_name as user_name,
            u.profile_picture_url as profile_picture_url,
            s.acronym as sector_acronym,
            s.primary_color as sector_color,
            cs.name as seal_name
        FROM notes_openings no
        JOIN users u ON u.id = no.user_id
        JOIN sectors s ON s.id = no.sector_id
        LEFT JOIN user_seals us ON us.user_id = u.id
        LEFT JOIN city_seals cs ON cs.id = us.city_seal_id
        WHERE no.document_id = $1
        ORDER BY no.opened_at
    """


def get_sent_notes_query() -> str:
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
    return """
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
        AND nr.sender_sector_id = $1
    """


def get_received_notes_query() -> str:
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
    return """
        SELECT COUNT(*)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = $1 AND nr.is_archived = false
        WHERE od.signed_at IS NOT NULL
    """


def get_note_detail_query() -> str:
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
    return """
        SELECT recipient_type
        FROM notes_recipients
        WHERE document_id = $1 AND sector_id = $2
    """


def check_user_is_sender_query() -> str:
    return """
        SELECT EXISTS(
            SELECT 1 FROM notes_recipients
            WHERE document_id = $1 AND sender_sector_id = $2
        ) as is_sender
    """


def check_sector_access_query() -> str:
    return """
        SELECT
            EXISTS(
                SELECT 1 FROM notes_recipients
                WHERE document_id = $1 AND sender_sector_id = $2
            ) as is_sender,
            (
                SELECT recipient_type FROM notes_recipients
                WHERE document_id = $1 AND sector_id = $2
                LIMIT 1
            ) as recipient_type
    """


def update_note_archived_status_query() -> str:
    return """
        UPDATE notes_recipients
        SET is_archived = $1,
            archived_at = CASE WHEN $2 = true THEN NOW() ELSE NULL END
        WHERE document_id = $3 AND sector_id = $4
        RETURNING id, is_archived, archived_at
    """


def get_note_recipient_info_query() -> str:
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
    return """
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = true
        WHERE od.signed_at IS NOT NULL
    """


def get_archived_notes_multi_sector_search_query() -> str:
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
