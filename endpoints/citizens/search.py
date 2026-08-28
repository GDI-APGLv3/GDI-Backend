from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.exceptions import exception_to_http_exception
from shared.dependencies import get_tenant_schema
from services.citizens.search import search_citizens, MAX_SEARCH_LIMIT
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/citizens", tags=["citizens"])


class CitizenSearchItem(BaseModel):
    id: str
    full_name: str
    country_id: str
    estado: str


class CitizenSearchResponse(BaseModel):
    success: bool = True
    data: list[CitizenSearchItem]
    total: int
    message: str


@router.get("/search", response_model=CitizenSearchResponse)
async def search_citizens_endpoint(
    request: Request,
    q: str = Query(..., min_length=2, description="Nombre o CUIL/DNI (parcial)"),
    limit: int = Query(MAX_SEARCH_LIMIT, ge=1, le=MAX_SEARCH_LIMIT),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    try:
        results = await search_citizens(q, schema_name=schema_name, limit=limit)
        return {
            "success": True,
            "data": results,
            "total": len(results),
            "message": f"{len(results)} ciudadano(s) encontrado(s)",
        }
    except Exception as exc:
        logger.error(f"Error buscando ciudadanos (q={q!r}): {exc}")
        raise exception_to_http_exception(exc)
