"""SQL queries for semantic search.

Parameter order for asyncpg ($N positional):

SEMANTIC_SEARCH_SQL:
  $1 = user_id          (repeated: permissions filter)
  $2 = embedding        (vector string)
  $3 = query_text
  $4 = threshold
  $5 = candidate_limit
  $6 = result_limit
  $7 = excluded_types   (text[]: tipos de documento excluidos, ej. ['CAEX','PV','TST','IFRLM'])

LOOKUP_DOCUMENT_SQL:
  $1 = user_id
  $2 = search_official (ILIKE)
  $3 = search_ref_a    (ILIKE)
  $4 = search_ref_b    (ILIKE)
  $5 = result_limit
"""

SEMANTIC_SEARCH_SQL = """
WITH
user_sectors AS (
    SELECT s.id AS sector_id
    FROM users u JOIN sectors s ON u.sector_id = s.id
    WHERE u.id = $1 AND s.is_active = true
    UNION
    SELECT s2.id
    FROM user_sector_permissions usp
    JOIN sectors s2 ON usp.sector_id = s2.id
    WHERE usp.user_id = $1 AND s2.is_active = true AND usp.can_view = true
),
query_tsv AS (
    SELECT plainto_tsquery('spanish', $3) AS q
),
vector_cands AS (
    SELECT
        dc.official_document_id,
        dc.chunk_text,
        dc.chunk_index,
        1 - (dc.embedding <=> $2::vector) AS similarity
    FROM document_chunks dc
    ORDER BY dc.embedding <=> $2::vector
    LIMIT $5
),
bm25_cands AS (
    SELECT
        dc.official_document_id,
        dc.chunk_text,
        dc.chunk_index,
        ts_rank_cd(dc.content_tsv, (SELECT q FROM query_tsv)) AS bm25_score
    FROM document_chunks dc
    WHERE dc.content_tsv @@ (SELECT q FROM query_tsv)
    ORDER BY ts_rank_cd(dc.content_tsv, (SELECT q FROM query_tsv)) DESC
    LIMIT $5
),
best_vector AS (
    SELECT DISTINCT ON (official_document_id)
        official_document_id, chunk_text, chunk_index, similarity
    FROM vector_cands
    WHERE similarity >= $4
    ORDER BY official_document_id, similarity DESC
),
ranked_vector AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY similarity DESC) AS vec_rank
    FROM best_vector
),
best_bm25 AS (
    SELECT DISTINCT ON (official_document_id)
        official_document_id, chunk_text, chunk_index, bm25_score
    FROM bm25_cands
    ORDER BY official_document_id, bm25_score DESC
),
ranked_bm25 AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY bm25_score DESC) AS bm25_rank
    FROM best_bm25
),
fused AS (
    SELECT
        COALESCE(rv.official_document_id, rb.official_document_id) AS official_document_id,
        COALESCE(rv.chunk_text, rb.chunk_text) AS chunk_text,
        COALESCE(rv.chunk_index, rb.chunk_index) AS chunk_index,
        COALESCE(rv.similarity, 0.0) AS similarity,
        COALESCE(1.0 / (60.0 + rv.vec_rank), 0.0) +
        COALESCE(1.0 / (60.0 + rb.bm25_rank), 0.0) AS rrf_score
    FROM ranked_vector rv
    FULL OUTER JOIN ranked_bm25 rb ON rv.official_document_id = rb.official_document_id
),
permitted AS (
    SELECT f.*
    FROM fused f
    JOIN official_documents od ON f.official_document_id = od.id
    JOIN document_types dt_filter ON od.document_type_id = dt_filter.id
    WHERE od.signed_at IS NOT NULL
      AND NOT (dt_filter.acronym = ANY($7::text[]))
      AND (
        EXISTS (
            SELECT 1 FROM document_draft dd
            WHERE dd.id = od.id AND dd.created_by = $1
        )
        OR EXISTS (
            SELECT 1 FROM document_signers ds
            WHERE ds.document_id = od.id AND ds.user_id = $1
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
            -- Admin por creacion (cuando no hay transfers): mirrors can_user_view_case caso 3c
            SELECT 1 FROM case_official_documents cod
            JOIN case_movements cm ON cm.case_id = cod.case_id
            WHERE cod.official_document_id = od.id
              AND cod.is_active = true
              AND cm.type = 'creation'
              AND cm.admin_sector_id IN (SELECT sector_id FROM user_sectors)
              AND NOT EXISTS (
                  SELECT 1 FROM case_movements cm3
                  WHERE cm3.case_id = cod.case_id AND cm3.type = 'transfer'
              )
        )
        OR EXISTS (
            -- Creador del expediente vinculado: mirrors can_user_view_case caso 2
            SELECT 1 FROM case_official_documents cod
            JOIN cases c ON c.id = cod.case_id
            WHERE cod.official_document_id = od.id
              AND cod.is_active = true
              AND c.created_by_user_id = $1
        )
        OR EXISTS (
            -- Flag global de expedientes: usuario ve todos los expedientes,
            -- ergo puede encontrar documentos vinculados a expedientes via semantic search.
            -- Mirrors can_user_view_case caso 1.
            SELECT 1 FROM case_official_documents cod
            JOIN users u_global ON u_global.id = $1
            WHERE cod.official_document_id = od.id
              AND cod.is_active = true
              AND u_global.can_global_search_cases = true
        )
        OR EXISTS (
            SELECT 1 FROM record_document_links rdl
            JOIN records r ON r.id = rdl.record_id
            JOIN registry_families rf ON rf.id = r.registry_family_id
            JOIN registry_family_permissions rfp
              ON rfp.registry_family_id = r.registry_family_id
            WHERE rdl.document_id = od.id
              AND rf.is_active = true
              AND r.state = 'Activo'
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
    od.resume,
    dt.acronym AS document_type,
    p.similarity,
    p.rrf_score,
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
ORDER BY p.rrf_score DESC
LIMIT $6;
"""

LOOKUP_DOCUMENT_SQL = """
WITH
user_sectors AS (
    SELECT s.id AS sector_id
    FROM users u JOIN sectors s ON u.sector_id = s.id
    WHERE u.id = $1 AND s.is_active = true
    UNION
    SELECT s2.id
    FROM user_sector_permissions usp
    JOIN sectors s2 ON usp.sector_id = s2.id
    WHERE usp.user_id = $1 AND s2.is_active = true AND usp.can_view = true
)
SELECT
    od.id AS document_id,
    od.official_number,
    od.reference,
    od.short_resume,
    od.resume,
    dt.acronym AS document_type,
    1.0 AS similarity,
    1.0 AS rrf_score,
    '' AS chunk_text,
    0 AS chunk_index,
    cases_agg.linked_cases,
    records_agg.linked_records
FROM official_documents od
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
WHERE od.signed_at IS NOT NULL
  AND (
      od.official_number ILIKE $2
      OR (od.reference ILIKE $3 AND od.reference ILIKE $4)
  )
  AND (
    EXISTS (
        SELECT 1 FROM document_draft dd
        WHERE dd.id = od.id AND dd.created_by = $1
    )
    OR EXISTS (
        SELECT 1 FROM document_signers ds
        WHERE ds.document_id = od.id AND ds.user_id = $1
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
        -- Admin por creacion sin transfers: mirrors can_user_view_case caso 3c
        SELECT 1 FROM case_official_documents cod
        JOIN case_movements cm ON cm.case_id = cod.case_id
        WHERE cod.official_document_id = od.id
          AND cod.is_active = true
          AND cm.type = 'creation'
          AND cm.admin_sector_id IN (SELECT sector_id FROM user_sectors)
          AND NOT EXISTS (
              SELECT 1 FROM case_movements cm3
              WHERE cm3.case_id = cod.case_id AND cm3.type = 'transfer'
          )
    )
    OR EXISTS (
        -- Creador del expediente vinculado: mirrors can_user_view_case caso 2
        SELECT 1 FROM case_official_documents cod
        JOIN cases c ON c.id = cod.case_id
        WHERE cod.official_document_id = od.id
          AND cod.is_active = true
          AND c.created_by_user_id = $1
    )
    OR EXISTS (
        -- Flag global de expedientes: mirrors can_user_view_case caso 1
        SELECT 1 FROM case_official_documents cod
        JOIN users u_global ON u_global.id = $1
        WHERE cod.official_document_id = od.id
          AND cod.is_active = true
          AND u_global.can_global_search_cases = true
    )
    OR EXISTS (
        SELECT 1 FROM record_document_links rdl
        JOIN records r ON r.id = rdl.record_id
        JOIN registry_families rf ON rf.id = r.registry_family_id
        JOIN registry_family_permissions rfp
          ON rfp.registry_family_id = r.registry_family_id
        WHERE rdl.document_id = od.id
          AND rf.is_active = true
          AND r.state = 'Activo'
          AND rfp.sector_id IN (SELECT sector_id FROM user_sectors)
          AND rfp.can_view = true
    )
  )
ORDER BY od.signed_at DESC
LIMIT $5;
"""
