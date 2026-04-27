"""SQL queries for semantic search."""

SEMANTIC_SEARCH_SQL = """
WITH
user_sectors AS (
    SELECT s.id AS sector_id
    FROM users u JOIN sectors s ON u.sector_id = s.id
    WHERE u.id = %(user_id)s AND s.is_active = true
    UNION
    SELECT s2.id
    FROM user_sector_permissions usp
    JOIN sectors s2 ON usp.sector_id = s2.id
    WHERE usp.user_id = %(user_id)s AND s2.is_active = true AND usp.can_view = true
),
candidates AS (
    SELECT
        dc.official_document_id,
        dc.chunk_text,
        dc.chunk_index,
        1 - (dc.embedding <=> %(embedding)s::vector) AS similarity
    FROM document_chunks dc
    ORDER BY dc.embedding <=> %(embedding)s::vector
    LIMIT %(candidate_limit)s
),
best_chunks AS (
    SELECT DISTINCT ON (official_document_id)
        official_document_id,
        chunk_text,
        chunk_index,
        similarity
    FROM candidates
    WHERE similarity >= %(threshold)s
    ORDER BY official_document_id, similarity DESC
),
permitted AS (
    SELECT bc.*
    FROM best_chunks bc
    JOIN official_documents od ON bc.official_document_id = od.id
    WHERE od.signed_at IS NOT NULL
      AND (
        EXISTS (
            SELECT 1 FROM document_draft dd
            WHERE dd.id = od.id AND dd.created_by = %(user_id)s
        )
        OR EXISTS (
            SELECT 1 FROM document_signers ds
            WHERE ds.document_id = od.id AND ds.user_id = %(user_id)s
        )
        OR od.signer_sector_ids && COALESCE(
            (SELECT ARRAY_AGG(sector_id) FILTER (WHERE sector_id IS NOT NULL) FROM user_sectors),
            '{}'::uuid[]
        )
        OR EXISTS (
            SELECT 1 FROM case_official_documents cod
            JOIN case_movements cm ON cm.case_id = cod.case_id
            WHERE cod.official_document_id = od.id
              AND cod.is_active = true
              AND cm.is_active = true
              AND cm.assigned_sector_id IN (SELECT sector_id FROM user_sectors)
        )
        OR EXISTS (
            SELECT 1 FROM case_official_documents cod
            JOIN case_movements cm ON cm.case_id = cod.case_id
            WHERE cod.official_document_id = od.id
              AND cod.is_active = true
              AND cm.type = 'transfer'
              AND cm.is_active = false
              AND cm.admin_sector_id IN (SELECT sector_id FROM user_sectors)
              AND cm.closed_at = (
                  SELECT MAX(cm2.closed_at) FROM case_movements cm2
                  WHERE cm2.case_id = cod.case_id
                    AND cm2.type = 'transfer' AND cm2.is_active = false
              )
        )
        OR EXISTS (
            SELECT 1 FROM record_document_links rdl
            JOIN records r ON r.id = rdl.record_id
            JOIN registry_family_permissions rfp
              ON rfp.registry_family_id = r.registry_family_id
            WHERE rdl.document_id = od.id
              AND rfp.sector_id IN (SELECT sector_id FROM user_sectors)
              AND rfp.can_view = true
        )
      )
)
SELECT
    p.official_document_id AS document_id,
    od.official_number,
    od.reference,
    od.short_resume,
    dt.acronym AS document_type,
    p.similarity,
    p.chunk_text,
    p.chunk_index,
    cases_agg.linked_cases,
    records_agg.linked_records
FROM permitted p
JOIN official_documents od ON p.official_document_id = od.id
JOIN document_types dt ON od.document_type_id = dt.id
LEFT JOIN LATERAL (
    SELECT json_agg(json_build_object(
        'case_id', c.id,
        'case_number', c.case_number
    )) AS linked_cases
    FROM case_official_documents cod
    JOIN cases c ON c.id = cod.case_id
    WHERE cod.official_document_id = od.id AND cod.is_active = true
) cases_agg ON true
LEFT JOIN LATERAL (
    SELECT json_agg(json_build_object(
        'record_id', r.id,
        'record_number', r.record_number,
        'display_name', r.display_name,
        'registry_name', rf.name
    )) AS linked_records
    FROM record_document_links rdl
    JOIN records r ON r.id = rdl.record_id
    JOIN registry_families rf ON rf.id = r.registry_family_id
    WHERE rdl.document_id = od.id
) records_agg ON true
ORDER BY p.similarity DESC
LIMIT %(result_limit)s;
"""
