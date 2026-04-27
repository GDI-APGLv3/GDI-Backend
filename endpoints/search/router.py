"""Semantic search endpoint."""

from fastapi import APIRouter, Request, Query, HTTPException
from shared.logging import get_logger
from services.search.semantic_search import semantic_search
import httpx

logger = get_logger(__name__)

router = APIRouter(tags=["search"])


@router.get("/api/v1/search/semantic")
def search_semantic(
    request: Request,
    q: str = Query(..., min_length=3, max_length=500, description="Texto de busqueda"),
    limit: int = Query(default=20, ge=1, le=50, description="Max resultados"),
):
    """Busqueda semantica de documentos por significado.

    Busca documentos similares al texto dado, filtrados por permisos del usuario.
    Devuelve documentos con sus vinculaciones a expedientes y legajos.
    """
    user_id = request.state.tenant_user_id
    schema_name = request.state.schema_name
    try:
        return semantic_search(q, user_id, schema_name=schema_name, limit=limit)
    except httpx.HTTPError as e:
        logger.error(f"AgenteLANG unavailable for semantic search: {e}")
        raise HTTPException(status_code=503, detail="Busqueda temporalmente no disponible")
    except Exception as e:
        logger.error(f"Semantic search error: {e}")
        raise HTTPException(status_code=500, detail="Error en busqueda semantica")
