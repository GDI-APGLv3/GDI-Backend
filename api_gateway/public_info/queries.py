
SEMANTIC_SEARCH_PUBLIC_SQL = """
WITH
query_tsv AS (
    SELECT plainto_tsquery('spanish', $2) AS q
),
vector_cands AS (
    SELECT
        dc.official_document_id,
        dc.chunk_text,
        dc.chunk_index,
        1 - (dc.embedding <=> $1::halfvec(768)) AS similarity
    FROM document_chunks dc
    ORDER BY dc.embedding <=> $1::halfvec(768)
    LIMIT $4
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
    LIMIT $4
),
best_vector AS (
    SELECT DISTINCT ON (official_document_id)
        official_document_id, chunk_text, chunk_index, similarity
    FROM vector_cands
    WHERE similarity >= $3
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
permitted_public AS (
    -- GDI-098 D6/D16: reemplaza la CTE `permitted` (gate GDI-069 por
    -- user_id) de services/search/queries.py::SEMANTIC_SEARCH_SQL. Sin
    -- user_id: solo pasan documentos de tipo PUBLICO. `NOT is_reserved` es
    -- cinturon-y-tirantes ademas de `visibility = 'publico'` (son
    -- mutuamente excluyentes por CHECK en BD, pero si ese CHECK fallara
    -- alguna vez esta es la ultima barrera).
    SELECT f.*
    FROM fused f
    JOIN official_documents od ON f.official_document_id = od.id
    JOIN document_types dt_filter ON od.document_type_id = dt_filter.id
    WHERE od.signed_at IS NOT NULL
      AND dt_filter.visibility = 'publico'
      AND NOT dt_filter.is_reserved
      AND NOT (dt_filter.acronym = ANY($6::text[]))
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
    records_agg.linked_records
FROM permitted_public p
JOIN official_documents od ON p.official_document_id = od.id
JOIN document_types dt ON od.document_type_id = dt.id
LEFT JOIN LATERAL (
    -- D6: un legajo solo aparece vinculado si SU familia tambien es
    -- publica (misma logica de "no filtrar info privada por el buscador").
    SELECT json_agg(json_build_object(
        'record_number', r.record_number,
        'display_name', r.display_name,
        'registry_name', rf.name
    )) AS linked_records
    FROM record_document_links rdl
    JOIN records r ON r.id = rdl.record_id
    JOIN registry_families rf ON rf.id = r.registry_family_id
    WHERE rdl.document_id = od.id
      AND rf.is_public = true
      -- D19 (fix crack seguridad): ademas de la familia publica, el legajo
      -- DEBE estar en uno de sus visible_states; si no, un legajo
      -- Archivado/Suspendido igual aparecia vinculado desde la busqueda.
      -- Semantica de visible_states (alineada con records.py::_resolve_visible_states,
      -- NO tocar al reves): COALESCE solo actua sobre SQL NULL, es decir la
      -- clave `visible_states` ausente (o explicitamente `null` en el JSON,
      -- que via `->` NO es SQL NULL pero el operador `?` sobre jsonb 'null'
      -- da false igual, asi que en la practica tambien termina sin mostrar
      -- nada -- caso raro y ya cubierto del lado seguro). Con `[]` explicito
      -- el COALESCE NO dispara: `'[]'::jsonb ? r.state` da false para
      -- CUALQUIER estado -> "no mostrar nada", que es la intencion literal
      -- del municipio al guardar una lista vacia. NO reemplazar por
      -- `visible_states IS NULL OR ... = '[]'` ni nada que trate `[]` como
      -- ausente: seria publicar "Activo" cuando el municipio pidio lo
      -- contrario.
      AND (COALESCE(rf.public_config -> 'visible_states', '["Activo"]'::jsonb) ? r.state)
) records_agg ON true
ORDER BY p.rrf_score DESC
LIMIT $5;
"""

LOOKUP_DOCUMENT_PUBLIC_SQL = """
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
    records_agg.linked_records
FROM official_documents od
JOIN document_types dt ON od.document_type_id = dt.id
LEFT JOIN LATERAL (
    SELECT json_agg(json_build_object(
        'record_number', r.record_number,
        'display_name', r.display_name,
        'registry_name', rf.name
    )) AS linked_records
    FROM record_document_links rdl
    JOIN records r ON r.id = rdl.record_id
    JOIN registry_families rf ON rf.id = r.registry_family_id
    WHERE rdl.document_id = od.id
      AND rf.is_public = true
      -- D19 (fix crack seguridad): ademas de la familia publica, el legajo
      -- DEBE estar en uno de sus visible_states; si no, un legajo
      -- Archivado/Suspendido igual aparecia vinculado desde la busqueda.
      -- Semantica de visible_states (alineada con records.py::_resolve_visible_states,
      -- NO tocar al reves): COALESCE solo actua sobre SQL NULL, es decir la
      -- clave `visible_states` ausente (o explicitamente `null` en el JSON,
      -- que via `->` NO es SQL NULL pero el operador `?` sobre jsonb 'null'
      -- da false igual, asi que en la practica tambien termina sin mostrar
      -- nada -- caso raro y ya cubierto del lado seguro). Con `[]` explicito
      -- el COALESCE NO dispara: `'[]'::jsonb ? r.state` da false para
      -- CUALQUIER estado -> "no mostrar nada", que es la intencion literal
      -- del municipio al guardar una lista vacia. NO reemplazar por
      -- `visible_states IS NULL OR ... = '[]'` ni nada que trate `[]` como
      -- ausente: seria publicar "Activo" cuando el municipio pidio lo
      -- contrario.
      AND (COALESCE(rf.public_config -> 'visible_states', '["Activo"]'::jsonb) ? r.state)
) records_agg ON true
WHERE od.signed_at IS NOT NULL
  AND dt.visibility = 'publico'
  AND NOT dt.is_reserved
  AND (
      od.official_number ILIKE $1
      OR (od.reference ILIKE $2 AND od.reference ILIKE $3)
  )
ORDER BY od.signed_at DESC
LIMIT $4;
"""

DOCUMENT_CONTENT_PUBLIC_SQL = """
SELECT
    od.id,
    od.official_number,
    od.reference,
    od.content,
    od.signed_at,
    dt.name AS document_type_name,
    dt.acronym AS document_type_acronym
FROM official_documents od
JOIN document_types dt ON dt.id = od.document_type_id
WHERE od.id = $1
  AND od.signed_at IS NOT NULL
  AND dt.visibility = 'publico'
  AND NOT dt.is_reserved
  AND NOT (dt.acronym = ANY($2::text[]));
"""
