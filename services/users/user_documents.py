"""
Servicios para obtener documentos de usuarios con filtros y paginación.
Versión refactorizada con responsabilidades separadas.
"""

import hashlib
from typing import Dict, Any, Optional
from shared.config import PaginationConfig
from .document_filters import DocumentFilters
from .document_queries import DocumentQueries
from .document_mappers import map_documents_list
from services.cache import get_cached
from config.constants import CACHE_TTL_COUNTS

def get_user_documents(
    user_id: str,
    status_filter: Optional[str] = None,
    date_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    document_type: Optional[str] = None,
    page: int = 1,
    page_size: int = PaginationConfig.DEFAULT_PAGE_SIZE,
    doc_number: Optional[str] = None,
    sector_filter: Optional[bool] = False,
    case_id: Optional[str] = None,
    min_signers: Optional[int] = None,
    schema_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Obtiene documentos de un usuario con filtros y paginación.

    Args:
        user_id: UUID del usuario
        status_filter: Filtro por estado visual (opcional)
        date_filter: Filtro predefinido de fechas (opcional)
        date_from: Fecha de inicio personalizada (opcional)
        date_to: Fecha de fin personalizada (opcional)
        document_type: Acrónimo del tipo de documento (opcional)
        page: Página a obtener (1-based)
        page_size: Cantidad de resultados por página
        doc_number: Búsqueda exacta por número (opcional)
        sector_filter: Si true, filtra solo documentos del sector del usuario
        case_id: UUID del expediente para filtrar documentos vinculados (opcional)
        min_signers: Cantidad mínima de firmantes (opcional, ej: 2 para docs con 2+ firmas)
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con documentos paginados y metadata
    """
    # Validar paginación
    page_size = min(max(page_size, PaginationConfig.MIN_PAGE_SIZE), PaginationConfig.MAX_PAGE_SIZE)
    offset = (page - 1) * page_size

    # Construir filtros
    filters = (DocumentFilters(user_id)
              .add_base_access_filter(doc_number)
              .add_sector_filter(sector_filter)
              .add_status_filter(status_filter)
              .add_date_filter(date_filter)
              .add_date_range_filter(date_from, date_to)
              .add_document_type_filter(document_type)
              .add_case_filter(case_id)
              .add_min_signers_filter(min_signers))

    where_clause, filter_params = filters.build()

    # Ejecutar count con cache (TTL 30s)
    filter_hash = hashlib.md5(f"{user_id}:{where_clause}:{filter_params}".encode()).hexdigest()[:8]
    cache_key = f"docs_count:{schema_name}:{filter_hash}"

    total_count = get_cached(
        cache_key,
        lambda: DocumentQueries.execute_count_query(user_id, where_clause, filter_params, schema_name),
        ttl=CACHE_TTL_COUNTS
    )

    # Si total es 0, retornar vacío sin ejecutar query principal
    if total_count == 0:
        return {
            "documents": [],
            "pagination": {
                "total": 0, "page": page, "page_size": page_size,
                "total_pages": 0, "has_next": False, "has_previous": False
            },
            "filters_applied": {
                "status": status_filter, "date_filter": date_filter,
                "date_from": date_from, "date_to": date_to,
                "document_type": document_type, "doc_number": doc_number,
                "sector_filter": sector_filter, "case_id": case_id,
                "min_signers": min_signers
            }
        }

    # Ejecutar query principal de documentos
    documents_data = DocumentQueries.execute_documents_query(
        user_id, where_clause, filter_params, page_size, offset, schema_name
    )

    # Mapear documentos
    documents = map_documents_list(documents_data, schema_name=schema_name)

    # Calcular paginación
    total_pages = (total_count + page_size - 1) // page_size

    return {
        "documents": documents,
        "pagination": {
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1
        },
        "filters_applied": {
            "status": status_filter,
            "date_filter": date_filter,
            "date_from": date_from,
            "date_to": date_to,
            "document_type": document_type,
            "doc_number": doc_number,
            "sector_filter": sector_filter,
            "case_id": case_id,
            "min_signers": min_signers
        }
    }