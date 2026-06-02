"""Semantic search endpoint."""

from fastapi import APIRouter, Request, Query, HTTPException
from shared.logging import get_logger
from services.search.semantic_search import semantic_search
import httpx

logger = get_logger(__name__)

router = APIRouter(tags=["search"])


@router.get("/api/v1/search/semantic")
async def search_semantic(
    request: Request,
    q: str = Query(..., min_length=3, max_length=500, description="Texto de busqueda"),
    limit: int = Query(default=20, ge=1, le=50, description="Max resultados"),
):
    """Busqueda semantica de documentos por significado.

    Busca documentos similares al texto dado, filtrados por permisos del usuario.
    Devuelve documentos con sus vinculaciones a expedientes y legajos.

    MejoraArranque FIX C — mapeo de errores AgenteLANG:
      - httpx.HTTPStatusError (AgenteLANG respondio 4xx/5xx con cuerpo, bug real)
        -> 500: el front NO entra en retry loop (isColdStartError solo dispara
        con 502/503/504). El usuario ve el error real y los textResults se
        conservan (logica de useSmartSearch).
      - httpx.TransportError (red/cold-start, tras agotar retries del
        embedding_client + budget wall-clock 5s) -> 503: el front lo trata como
        cold-start y reintenta con su propio backoff (~23s en retryWithBackoff).
      - httpx.HTTPError catch-all para casos exoticos (DecodingError, etc.) -> 500.
      - Cualquier otra Exception -> 500.
    """
    user_id = request.state.tenant_user_id
    schema_name = request.state.schema_name
    try:
        return await semantic_search(q, user_id, schema_name=schema_name, limit=limit, source="api")
    except httpx.HTTPStatusError as e:
        # Bug real de AgenteLANG: respondio con 4xx/5xx. NO es cold-start.
        # Mapeamos a 500 para que el front NO entre en retry-loop (su predicado
        # isColdStartError solo dispara con 502/503/504).
        status = e.response.status_code if e.response is not None else "?"
        logger.error("AgenteLANG HTTP error %s: %s", status, e)
        raise HTTPException(status_code=500, detail="Error en servicio de embeddings")
    except httpx.TransportError as e:
        # AgenteLANG dormido / inalcanzable tras agotar retries + budget del
        # embedding_client. 503 -> el front reintenta con su propio backoff.
        logger.error("AgenteLANG unreachable after retries: %s", e)
        raise HTTPException(status_code=503, detail="Busqueda temporalmente no disponible")
    except httpx.HTTPError as e:
        # Catch-all para casos exoticos de httpx (DecodingError, TooManyRedirects).
        logger.error("AgenteLANG httpx error (no clasificado): %s", e)
        raise HTTPException(status_code=500, detail="Error en busqueda semantica")
    except Exception as e:
        logger.error("Semantic search error: %s", e)
        raise HTTPException(status_code=500, detail="Error en busqueda semantica")
