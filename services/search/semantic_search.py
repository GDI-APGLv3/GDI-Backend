"""Semantic search service - orchestrates embedding + SQL + formatting."""

from shared.logging import get_logger
from services.search.embedding_client import get_embedding
from services.search.queries import SEMANTIC_SEARCH_SQL
from database import execute_query

logger = get_logger(__name__)


def semantic_search(query: str, user_id: str, *, schema_name: str, limit: int = 20):
    """Busqueda semantica de documentos con filtro de permisos."""
    # 1. Obtener embedding de AgenteLANG
    result = get_embedding(query, schema_name=schema_name, rewrite=True)
    if not result.get("embedding"):
        return {"success": True, "query": query, "rewritten_query": None, "results": [], "total": 0}
    embedding_str = "[" + ",".join(str(x) for x in result["embedding"]) + "]"

    # 2. Ejecutar query SQL con CTEs
    rows = execute_query(
        SEMANTIC_SEARCH_SQL,
        {
            "user_id": user_id,
            "embedding": embedding_str,
            "threshold": 0.0,
            "candidate_limit": 200,
            "result_limit": limit,
        },
        schema_name=schema_name,
    )

    # 3. Formatear response (convertir NULL de json_agg a [])
    results = []
    for row in (rows or []):
        results.append({
            "document_id": str(row["document_id"]),
            "official_number": row["official_number"],
            "document_type": row["document_type"],
            "reference": row["reference"],
            "short_resume": row["short_resume"],
            "similarity": round(float(row["similarity"]), 4),
            "chunk_text": row["chunk_text"],
            "linked_cases": row["linked_cases"] or [],
            "linked_records": row["linked_records"] or [],
        })

    return {
        "success": True,
        "query": query,
        "rewritten_query": result.get("rewritten_text"),
        "results": results,
        "total": len(results),
    }
