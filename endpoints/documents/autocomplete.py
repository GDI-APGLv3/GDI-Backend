"""
Endpoint para autocompletado de documentos oficiales.

Este módulo proporciona funcionalidad de autocompletado para búsqueda de documentos
oficiales por número. Implementa:
- Búsqueda incremental con mínimo 2 caracteres
- Paginación para grandes conjuntos de resultados
- Filtrado automático de documentos internos del sistema
- Validación de entrada y manejo centralizado de errores
- Logging completo de operaciones y errores

Example:
    GET /api/v1/documents/autocomplete?q=ANEXO-2025&limit=20&page=1
    
    Response:
    {
        "documents": [
            {
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "official_number": "ANEXO-2025-000001-SMG-ADGEN",
                "reference": "Solicitud de presupuesto"
            }
        ],
        "total": 1,
        "query": "ANEXO-2025",
        "page": 1
    }
"""

from fastapi import APIRouter, Query, status, Depends, Request
from shared.logging import get_logger
import re

from auth import get_current_user
from models.schemas import AuthenticatedUser, AutocompleteDocumentsResponse
from models.tags import Tags
from database import execute_query
from services.documents.core.queries import autocomplete_official_documents_query
from shared.exceptions import (
    ValidationError,
    DatabaseError,
    exception_to_http_exception,
    handle_database_error
)
from shared.dependencies import get_tenant_schema

# Configurar logger específico para este módulo
logger = get_logger("endpoints.documents.autocomplete")

router = APIRouter(tags=[Tags.DOCUMENTOS])

# Constantes para validación
QUERY_MIN_LENGTH = 2
QUERY_MAX_LENGTH = 50
LIMIT_MAX = 50
LIMIT_DEFAULT = 50


def validate_query_string(q: str) -> None:
    """
    Valida que el string de búsqueda contenga solo caracteres permitidos.
    
    Args:
        q: String de búsqueda a validar
        
    Raises:
        ValidationError: Si el string contiene caracteres no permitidos
        
    Note:
        Permite: letras, números, guiones, espacios y caracteres especiales comunes en números de documento
    """
    # Patrón: alfanuméricos, guiones, espacios, punto, coma
    pattern = r'^[a-zA-Z0-9\s\-.,áéíóúÁÉÍÓÚñÑüÜ]+$'
    
    if not re.match(pattern, q):
        raise ValidationError(
            "El texto de búsqueda contiene caracteres no permitidos. "
            "Solo se permiten letras, números, guiones, espacios y puntos."
        )


@router.get(
    "/api/v1/documents/autocomplete",
    response_model=AutocompleteDocumentsResponse,
    status_code=status.HTTP_200_OK,
    summary="Autocompletado de documentos oficiales",
    description="""
    Busca documentos oficiales cuyo número oficial comience con el texto proporcionado.
    
    **Funcionalidades:**
    - Búsqueda a partir de 2 caracteres
    - Paginación para manejar grandes conjuntos de resultados
    - Exclusión automática de documentos internos del sistema (CAEX, PV)
    - Resultados ordenados alfabéticamente por número oficial
    
    **Casos de uso:**
    - Autocompletado en campos de búsqueda
    - Selección de documentos relacionados
    
    **Notas:**
    - La búsqueda es case-insensitive
    - Solo devuelve documentos oficiales (no borradores)
    - Requiere autenticación
    """,
    responses={
        200: {
            "description": "Búsqueda exitosa",
            "content": {
                "application/json": {
                    "example": {
                        "documents": [
                            {
                                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                                "official_number": "ANEXO-2025-000001-SMG-ADGEN",
                                "reference": "Solicitud de presupuesto para obras"
                            },
                            {
                                "document_id": "550e8400-e29b-41d4-a716-446655440001",
                                "official_number": "ANEXO-2025-000002-SMG-ADGEN",
                                "reference": "Informe de auditoría"
                            }
                        ],
                        "total": 2,
                        "query": "ANEXO-2025",
                        "page": 1
                    }
                }
            }
        },
        400: {
            "description": "Error de validación - Query inválido",
            "content": {
                "application/json": {
                    "example": {
                        "message": "El texto de búsqueda contiene caracteres no permitidos",
                        "type": "ValidationError"
                    }
                }
            }
        },
        500: {
            "description": "Error interno del servidor",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Error interno del servidor",
                        "type": "DatabaseError"
                    }
                }
            }
        }
    }
)
async def autocomplete_official_documents(
    request: Request,
    q: str = Query(
        ...,
        min_length=QUERY_MIN_LENGTH,
        max_length=QUERY_MAX_LENGTH,
        description=f"Texto para autocompletado (mínimo {QUERY_MIN_LENGTH} caracteres, máximo {QUERY_MAX_LENGTH})",
        examples=["ANEXO-2025"]
    ),
    limit: int = Query(
        default=LIMIT_DEFAULT,
        ge=1,
        le=LIMIT_MAX,
        description=f"Límite de resultados por página (máximo {LIMIT_MAX})",
        examples=[20]
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="Número de página para paginación (inicia en 1)",
        examples=[1]
    ),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> AutocompleteDocumentsResponse:
    """
    Autocompletado de documentos oficiales por número.
    
    Este endpoint proporciona funcionalidad de autocompletado para búsqueda de documentos
    oficiales. Busca documentos cuyo número oficial comience con el texto proporcionado.
    
    Args:
        q: Texto de búsqueda (mínimo 2 caracteres, máximo 50)
        limit: Número máximo de resultados por página (máximo 50)
        page: Número de página para paginación
        current_user: Usuario autenticado (inyectado por dependencia)
        
    Returns:
        AutocompleteDocumentsResponse: Objeto con lista de documentos encontrados
        
    Raises:
        HTTPException 400: Si el query contiene caracteres no permitidos
        HTTPException 500: Si hay error de base de datos o error interno
        
    Example:
        >>> # GET /api/v1/documents/autocomplete?q=ANEXO-2025&limit=20&page=1
        >>> {
        ...     "documents": [
        ...         {
        ...             "document_id": "550e8400-e29b-41d4-a716-446655440000",
        ...             "official_number": "ANEXO-2025-000001-SMG-ADGEN",
        ...             "reference": "Solicitud de presupuesto"
        ...         }
        ...     ],
        ...     "total": 1,
        ...     "query": "ANEXO-2025",
        ...     "page": 1
        ... }
    """
    try:
        validate_query_string(q)
        offset = (page - 1) * limit
        query_sql = autocomplete_official_documents_query()
        search_pattern = f"{q}%"
        results = execute_query(query_sql, (search_pattern, limit, offset), schema_name=schema_name)

        return AutocompleteDocumentsResponse(
            documents=results,
            total=len(results),
            query=q,
            page=page
        )

    except ValidationError as e:
        logger.warning(f"Validación fallida - usuario {request.state.tenant_user_id}, query='{q}': {e.message}")
        raise exception_to_http_exception(e)

    except Exception as e:
        logger.error(
            f"Error en autocompletado - usuario {request.state.tenant_user_id}, query='{q}': {str(e)}",
            exc_info=True
        )
        db_error = handle_database_error(e, "autocompletado de documentos")
        raise exception_to_http_exception(db_error)