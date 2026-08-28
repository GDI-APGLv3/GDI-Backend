from fastapi import APIRouter, Query, Depends, Request
from typing import Optional

from auth import get_current_user
from models import schemas
from models.users.user_documents import UserDocumentsResponse
from models.tags import Tags
from shared.dependencies import get_tenant_schema

from endpoints.users.get_documents import get_user_documents as _get_user_documents_handler

router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.get(
    "/api/v1/documents/search",
    response_model=UserDocumentsResponse,
    summary="Buscar documentos del usuario autenticado",
    description=(
        "Busca documentos donde el usuario es creador, firmante o numerador, con "
        "filtros y paginación. URL canónica equivalente al endpoint del Gateway "
        "(SET B) `GET /api/v1/documents/search`. Reusa el mismo service que "
        "`GET /api/v1/documents/users/documents`."
    ),
)
async def search_documents(
    request: Request,
    current_user: schemas.AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
    search: Optional[str] = Query(
        None,
        description="Búsqueda parcial en referencia, número de documento y contenido (min 2 caracteres)",
    ),
    status: Optional[str] = Query(
        None,
        description="Estado visual: En edición, En proceso de firma, Firmar ahora, A mi firma, Firmado",
    ),
    document_type: Optional[str] = Query(
        None, description="Acrónimo del tipo de documento (IF, ME, OF, etc.)"
    ),
    doc_number: Optional[str] = Query(
        None, description="Búsqueda exacta por número de documento (ej: ME-2023-001)"
    ),
    min_signers: Optional[int] = Query(
        None, description="Filtrar documentos con mínimo N firmantes", ge=1
    ),
    date_filter: Optional[str] = Query(
        None, description="Filtro de fecha: hoy, ayer, ultimos_7_dias, ultimos_30_dias"
    ),
    date_from: Optional[str] = Query(None, description="Fecha de inicio (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Fecha de fin (YYYY-MM-DD)"),
    sector_filter: Optional[str] = Query(
        None, description="Origen: 'mine' (propios) o 'sector' (documentos de mi sector)"
    ),
    page: int = Query(1, description="Número de página (inicia en 1)", ge=1),
    page_size: int = Query(20, description="Documentos por página", ge=1, le=100),
):
    """
    Busca documentos del usuario autenticado.

    Delega al handler `get_user_documents` (mismo service y mismo formateo de
    respuesta). El parámetro de query `status` mapea a `status_filter`.
    """
    return await _get_user_documents_handler(
        request=request,
        current_user=current_user,
        schema_name=schema_name,
        status_filter=status,
        date_filter=date_filter,
        date_from=date_from,
        date_to=date_to,
        document_type=document_type,
        doc_number=doc_number,
        search=search,
        min_signers=min_signers,
        sector_filter=sector_filter,
        page=page,
        page_size=page_size,
    )
