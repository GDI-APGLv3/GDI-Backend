
import re
import time
import asyncio
from typing import Literal, Optional

from shared.logging import get_logger
from services.search.embedding_client import get_embedding
from services.search.queries import LOOKUP_DOCUMENT_SQL, SEMANTIC_SEARCH_SQL
from database import fetch_all, execute
from config.constants import SEMANTIC_SEARCH_EXCLUDED_TYPES

logger = get_logger(__name__)

THRESHOLD = 0.30

_LOOKUP_PATTERN = re.compile(
    r"""
    (?:
        (?:ordenanza|resoluci[oó]n|comunicaci[oó]n|decreto)
        (?:\s+hcd)?
        \s+\d{2,}
    |
        PL[A-Z]{2,4}-\d{4}-\d+-[A-Z]+-[A-Z]+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SEMANTIC_OVERRIDE = re.compile(
    r'\b(qu[eé]|dice|habla|trata|sobre|acerca|relacionado|contenido|explica|significa|define)\b',
    re.IGNORECASE,
)

_TYPE_NUMBER_EXTRACT = re.compile(
    r'(ordenanza|resoluci[oó]n|comunicaci[oó]n|decreto)(?:\s+hcd)?\s+(\d+)',
    re.IGNORECASE,
)
_OFFICIAL_NUMBER_EXTRACT = re.compile(
    r'PL[A-Z]{2,4}-\d{4}-\d+-[A-Z]+-[A-Z]+',
    re.IGNORECASE,
)


def classify_intent(query: str) -> Literal["lookup", "rag"]:
    if _LOOKUP_PATTERN.search(query) and not _SEMANTIC_OVERRIDE.search(query):
        return "lookup"
    return "rag"


def _build_lookup_params(query: str, result_limit: int, user_id: str) -> tuple:
    if _OFFICIAL_NUMBER_EXTRACT.search(query):
        return (
            user_id,
            f"%{query.strip()}%",
            f"%{query.strip()}%",
            f"%{query.strip()}%",
            result_limit,
        )
    m = _TYPE_NUMBER_EXTRACT.search(query)
    if m:
        tipo = m.group(1)
        numero = m.group(2)
        return (
            user_id,
            f"%{numero}%",
            f"%{tipo}%",
            f"%{numero}%",
            result_limit,
        )
    return (
        user_id,
        f"%{query}%",
        f"%{query}%",
        f"%{query}%",
        result_limit,
    )


async def _log_query(
    *,
    schema_name: str,
    user_id: Optional[str],
    source: str,
    intent: str,
    query: str,
    rewritten_query: Optional[str],
    candidates: int,
    final: int,
    top_similarity: Optional[float],
    bottom_similarity: Optional[float],
    threshold_applied: float,
    latency_ms: int,
    results_doc_ids: list,
) -> None:
    try:
        await execute(
            """
            INSERT INTO public.rag_query_log (
                schema_name, user_id, source, intent, query, rewritten_query,
                candidates_returned, final_returned,
                top_similarity, bottom_similarity, threshold_applied,
                latency_ms, results_doc_ids
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8,
                $9, $10, $11,
                $12, $13::uuid[]
            )
            """,
            schema_name,
            user_id,
            source,
            intent,
            query,
            rewritten_query,
            candidates,
            final,
            top_similarity,
            bottom_similarity,
            threshold_applied,
            latency_ms,
            results_doc_ids if results_doc_ids else [],
            schema_name="public",
        )
    except Exception as e:
        logger.warning(f"rag_query_log insert failed: {e}")


_background_tasks: set = set()


def _fire_and_forget_log(**kwargs) -> None:
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_log_query(**kwargs))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except RuntimeError:
        pass
    except Exception:
        pass


def _format_row(row: dict) -> dict:
    return {
        "document_id": str(row["document_id"]),
        "official_number": row["official_number"],
        "document_type": row["document_type"],
        "reference": row["reference"],
        "short_resume": row["short_resume"],
        "resume": row["resume"],
        "similarity": round(float(row["similarity"]), 4),
        "rrf_score": round(float(row["rrf_score"]), 6),
        "chunk_text": row["chunk_text"],
        "linked_cases": row["linked_cases"] or [],
        "linked_records": row["linked_records"] or [],
    }


async def _lookup_document(
    query: str,
    user_id: str,
    *,
    schema_name: str,
    limit: int,
    source: str,
    t0: float,
) -> dict:
    params = _build_lookup_params(query, limit, user_id)
    rows = await fetch_all(
        LOOKUP_DOCUMENT_SQL,
        *params,
        schema_name=schema_name,
    )

    results = [_format_row(dict(row)) for row in (rows or [])]
    latency_ms = int((time.time() - t0) * 1000)
    _fire_and_forget_log(
        schema_name=schema_name,
        user_id=user_id,
        source=source,
        intent="lookup",
        query=query,
        rewritten_query=None,
        candidates=len(results),
        final=len(results),
        top_similarity=1.0 if results else None,
        bottom_similarity=1.0 if results else None,
        threshold_applied=1.0,
        latency_ms=latency_ms,
        results_doc_ids=[r["document_id"] for r in results],
    )
    return {
        "success": True,
        "query": query,
        "rewritten_query": None,
        "intent": "lookup",
        "results": results,
        "total": len(results),
    }


async def semantic_search(
    query: str,
    user_id: str,
    *,
    schema_name: str,
    limit: int = 20,
    source: str = "api",
):
    t0 = time.time()
    intent = classify_intent(query)

    if intent == "lookup":
        return await _lookup_document(query, user_id, schema_name=schema_name, limit=limit, source=source, t0=t0)

    result = await get_embedding(query, schema_name=schema_name, rewrite=True)
    if not result.get("embedding"):
        latency_ms = int((time.time() - t0) * 1000)
        _fire_and_forget_log(
            schema_name=schema_name,
            user_id=user_id,
            source=source,
            intent=intent,
            query=query,
            rewritten_query=result.get("rewritten_text"),
            candidates=0,
            final=0,
            top_similarity=None,
            bottom_similarity=None,
            threshold_applied=THRESHOLD,
            latency_ms=latency_ms,
            results_doc_ids=[],
        )
        return {"success": True, "query": query, "rewritten_query": None, "intent": intent, "results": [], "total": 0}

    embedding_str = "[" + ",".join(str(x) for x in result["embedding"]) + "]"
    query_text = result.get("rewritten_text") or query

    rows = await fetch_all(
        SEMANTIC_SEARCH_SQL,
        user_id,
        embedding_str,
        query_text,
        THRESHOLD,
        200,
        limit,
        list(SEMANTIC_SEARCH_EXCLUDED_TYPES),
        schema_name=schema_name,
    )

    results = [_format_row(dict(row)) for row in (rows or [])]
    vec_sims = [r["similarity"] for r in results if r["similarity"] > 0]
    latency_ms = int((time.time() - t0) * 1000)
    _fire_and_forget_log(
        schema_name=schema_name,
        user_id=user_id,
        source=source,
        intent=intent,
        query=query,
        rewritten_query=result.get("rewritten_text"),
        candidates=len(rows or []),
        final=len(results),
        top_similarity=max(vec_sims) if vec_sims else None,
        bottom_similarity=min(vec_sims) if vec_sims else None,
        threshold_applied=THRESHOLD,
        latency_ms=latency_ms,
        results_doc_ids=[r["document_id"] for r in results],
    )

    return {
        "success": True,
        "query": query,
        "rewritten_query": result.get("rewritten_text"),
        "intent": intent,
        "results": results,
        "total": len(results),
    }
