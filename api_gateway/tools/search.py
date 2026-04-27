"""Semantic search tool for MCP Gateway."""

from shared.logging import get_logger

logger = get_logger(__name__)


def semantic_search_tool(ctx, query: str, limit: int = 6):
    """Busca documentos por significado usando IA."""
    from services.search.semantic_search import semantic_search
    return semantic_search(query, ctx.user_id, schema_name=ctx.schema_name, limit=limit)
