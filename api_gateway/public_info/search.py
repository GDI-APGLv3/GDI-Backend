import asyncio
import hashlib
import re
import time
from typing import Optional

from shared.logging import get_logger
from database import fetch_all
from config.constants import SEMANTIC_SEARCH_EXCLUDED_TYPES
from services.search.embedding_client import get_embedding
from services.search.semantic_search import classify_intent

from api_gateway.public_info.queries import SEMANTIC_SEARCH_PUBLIC_SQL, LOOKUP_DOCUMENT_PUBLIC_SQL
from api_gateway.public_info.records import list_records_public
from api_gateway.public_info.sanitize import build_public_pdf_url
from api_gateway.public_info.muni import get_bucket_publico
from api_gateway.public_info import quota as ai_quota

logger = get_logger(__name__)

THRESHOLD = 0.30
CANDIDATE_LIMIT = 200
DOC_RESULT_LIMIT = 15
RECORD_RESULT_LIMIT = 15

PUBLIC_EMBED_TIMEOUT_SECONDS = 5.0

_EMBED_CONCURRENCY_LIMIT = 5
_embed_semaphore = asyncio.Semaphore(_EMBED_CONCURRENCY_LIMIT)

_OFFICIAL_NUMBER_EXTRACT = re.compile(
    r'PL[A-Z]{2,4}-\d{4}-\d+-[A-Z]+-[A-Z]+', re.IGNORECASE,
)
_TYPE_NUMBER_EXTRACT = re.compile(
    r'(ordenanza|resoluci[oó]n|comunicaci[oó]n|decreto)(?:\s+hcd)?\s+(\d+)', re.IGNORECASE,
)


def _escape_ilike(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_lookup_params_public(query: str, limit: int) -> tuple:
    if _OFFICIAL_NUMBER_EXTRACT.search(query):
        q = f"%{_escape_ilike(query.strip())}%"
        return (q, q, q, limit)
    m = _TYPE_NUMBER_EXTRACT.search(query)
    if m:
        tipo, numero = _escape_ilike(m.group(1)), _escape_ilike(m.group(2))
        return (f"%{numero}%", f"%{tipo}%", f"%{numero}%", limit)
    q = _escape_ilike(query)
    return (f"%{q}%", f"%{q}%", f"%{q}%", limit)


def _format_doc_row(row: dict, *, bucket_publico: Optional[str], muni: str) -> dict:
    pdf_url = (
        build_public_pdf_url(muni, row["document_id"])
        if bucket_publico and row.get("document_id")
        else None
    )
    return {
        "type": "document",
        "official_number": row["official_number"],
        "reference": row["reference"],
        "document_type": row["document_type"],
        "resume": row.get("short_resume") or row.get("resume"),
        "snippet": row.get("chunk_text") or None,
        "pdf_url": pdf_url,
        "linked_records": row.get("linked_records") or [],
    }


async def _search_documents_lookup(query: str, *, schema_name: str, bucket_publico: Optional[str], muni: str) -> list[dict]:
    params = _build_lookup_params_public(query, DOC_RESULT_LIMIT)
    rows = await fetch_all(LOOKUP_DOCUMENT_PUBLIC_SQL, *params, schema_name=schema_name)
    return [_format_doc_row(dict(r), bucket_publico=bucket_publico, muni=muni) for r in (rows or [])]


async def _search_documents_semantic(query: str, *, schema_name: str, bucket_publico: Optional[str], muni: str) -> list[dict]:
    query_norm = query.strip().lower()
    embedding = ai_quota.get_cached_embedding(query_norm, schema_name=schema_name)

    if embedding is None:
        if not ai_quota.check_and_consume_quota():
            logger.info("[PublicInfo] Cupo IA agotado o Redis no disponible, degradando a lookup")
            return await _search_documents_lookup(query, schema_name=schema_name, bucket_publico=bucket_publico, muni=muni)
        try:
            async with _embed_semaphore:
                result = await asyncio.wait_for(
                    get_embedding(query, schema_name=schema_name, rewrite=True),
                    timeout=PUBLIC_EMBED_TIMEOUT_SECONDS,
                )
            embedding = result.get("embedding")
            if embedding:
                ai_quota.set_cached_embedding(query_norm, embedding, schema_name=schema_name)
        except Exception as e:
            logger.warning(
                f"[PublicInfo] Embedding fallo/timeout ({PUBLIC_EMBED_TIMEOUT_SECONDS}s), degradando a lookup: {e}"
            )
            return await _search_documents_lookup(query, schema_name=schema_name, bucket_publico=bucket_publico, muni=muni)

    if not embedding:
        return await _search_documents_lookup(query, schema_name=schema_name, bucket_publico=bucket_publico, muni=muni)

    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
    rows = await fetch_all(
        SEMANTIC_SEARCH_PUBLIC_SQL,
        embedding_str,
        query,
        THRESHOLD,
        CANDIDATE_LIMIT,
        DOC_RESULT_LIMIT,
        list(SEMANTIC_SEARCH_EXCLUDED_TYPES),
        schema_name=schema_name,
    )
    return [_format_doc_row(dict(r), bucket_publico=bucket_publico, muni=muni) for r in (rows or [])]


def _format_record_row(row: dict) -> dict:
    return {
        "type": "record",
        "record_number": row["record_number"],
        "display_name": row["display_name"],
        "registry_code": row.get("registry_code"),
        "fields": row.get("fields") or {},
        "snippet": None,
        "pdf_url": None,
    }


async def _search_records(query: str, *, schema_name: str) -> list[dict]:
    result = await list_records_public(
        schema_name=schema_name, search=query, page=1, page_size=RECORD_RESULT_LIMIT,
    )
    return [_format_record_row(r) for r in result.get("records", [])]


async def public_search(query: str, *, schema_name: str, muni: str) -> dict:
    t0 = time.time()
    query = query.strip()
    intent = classify_intent(query)
    bucket_publico = await get_bucket_publico(schema_name=schema_name)

    if intent == "lookup":
        docs = await _search_documents_lookup(query, schema_name=schema_name, bucket_publico=bucket_publico, muni=muni)
    else:
        docs = await _search_documents_semantic(query, schema_name=schema_name, bucket_publico=bucket_publico, muni=muni)

    records = await _search_records(query, schema_name=schema_name)

    results = docs + records
    latency_ms = int((time.time() - t0) * 1000)
    query_fingerprint = hashlib.sha1(query.encode("utf-8")).hexdigest()[:8]
    logger.info(
        f"[PublicInfo] search len={len(query)} hash={query_fingerprint} "
        f"-> {len(results)} resultados ({latency_ms}ms)"
    )

    return {
        "success": True,
        "query": query,
        "intent": intent,
        "results": results,
        "total": len(results),
    }
