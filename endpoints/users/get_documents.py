
from shared.logging import get_logger
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from typing import Optional
from models import schemas
from models.users.user_documents import UserDocumentsResponse
from services import document_service
from auth import get_current_user
from models.tags import Tags
from shared.exceptions import exception_to_http_exception
from shared.dependencies import get_tenant_schema

logger = get_logger(__name__)
router = APIRouter(tags=[Tags.USERS])

@router.get("/users/documents",
            response_model=UserDocumentsResponse,
            summary="Obtener documentos del usuario autenticado",
            description="Recupera todos los documentos creados o donde el usuario es firmante con paginación",
            responses={
                200: {
                    "description": "Documentos obtenidos exitosamente",
                    "content": {
                        "application/json": {
                            "example": {
                                "total": None,
                                "page": 1,
                                "page_size": 10,
                                "total_pages": None,
                                "has_next": False,
                                "has_previous": False,
                                "documents": [
                                    {
                                        "id": "0a684c54-03d1-4f9c-aa3f-054c4a55c557",
                                        "reference": "Informe Técnico Q4 2025",
                                        "display_status": "Firmar ahora",
                                        "updated_at": "2025-12-02T10:30:00",
                                        "document_type": {
                                            "name": "Documento",
                                            "acronym": "IF"
                                        },
                                        "user_role": "signer",
                                        "last_editor_name": "Juan Pérez",
                                        "last_editor_profile_picture_url": "https://...",
                                        "official_number": "IF-2025-123"
                                    }
                                ]
                            }
                        }
                    }
                },
                400: {
                    "description": "Error de validación en parámetros",
                    "content": {
                        "application/json": {
                            "example": {
                                "detail": "status_filter debe ser uno de: En edición, En proceso de firma, Firmar ahora, Firmado"
                            }
                        }
                    }
                },
                401: {
                    "description": "No autenticado - token inválido o expirado",
                    "content": {
                        "application/json": {
                            "example": {
                                "detail": "No autenticado"
                            }
                        }
                    }
                },
                500: {
                    "description": "Error interno del servidor",
                    "content": {
                        "application/json": {
                            "example": {
                                "detail": "Error interno: [detalles del error]"
                            }
                        }
                    }
                }
            })
async def get_user_documents(
    request: Request,
    current_user: schemas.AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
    status_filter: Optional[str] = Query(
        None,
        description="Filtrar por estado visual: En edición, En proceso de firma, Firmar ahora, A mi firma, Firmado"
    ),
    date_filter: Optional[str] = Query(
        None,
        description="Filtro predefinido de fechas: hoy, ayer, ultimos_7_dias, ultimos_30_dias"
    ),
    date_from: Optional[str] = Query(
        None,
        description="Fecha de inicio para filtrado personalizado (formato YYYY-MM-DD)"
    ),
    date_to: Optional[str] = Query(
        None,
        description="Fecha de fin para filtrado personalizado (formato YYYY-MM-DD)"
    ),
    document_type: Optional[str] = Query(
        None,
        description="Acrónimo del tipo de documento para filtrar (IF, ME, OF, etc.)"
    ),
    doc_number: Optional[str] = Query(
        None,
        description="Búsqueda exacta por número de documento (ej: ME-2023-001)"
    ),
    search: Optional[str] = Query(
        None,
        description="Búsqueda parcial en referencia, número de documento y contenido (min 2 caracteres)"
    ),
    min_signers: Optional[int] = Query(
        None,
        description="Filtrar documentos con mínimo N firmantes (ej: 2 para docs con 2+ firmas)",
        ge=1
    ),
    sector_filter: Optional[str] = Query(
        None,
        description="Filtrar por origen: 'mine' = solo propios (creator/signer/numerator), 'sector' = solo documentos de mi sector"
    ),
    exclude_reserved: bool = Query(
        False,
        description="GDI-069: si true, excluye documentos cuyo tipo es reservado. Lo usa el modal Vincular documento cuando el expediente destino NO es reservado."
    ),
    signature_mode: str = Query(
        "electronic",
        description=(
            "GDI-167: solo afecta a status_filter='Firmar ahora' (el PortaFirma). "
            "'electronic' (default) = lo que se firma SIN token, igual que siempre. "
            "'digital' = la solapa nueva: lo que EXIGE token. Los dos conjuntos son "
            "complementarios y no se pisan."
        ),
    ),
    page: int = Query(
        1,
        description="Número de página (inicia en 1)",
        ge=1
    ),
    page_size: int = Query(
        20,
        description="Número de documentos por página",
        ge=1,
        le=100
    )
):
    """
    Obtiene todos los documentos del usuario autenticado.

    Incluye tanto borradores como documentos oficiales donde el usuario:
    - Es el creador del documento
    - Es un firmante del documento
    - Es el numerador del documento

    ## Autenticación requerida:

    - Token JWT válido de Auth0 en el header Authorization: Bearer <token>

    ## Respuesta

    La respuesta incluye:
    - `total`: `null` desde GDI-369 -- dejamos de contar el universo para no retener
      conexiones del pool. Usar `has_next` / `has_previous` para paginar.
    - `page`: Número de página actual
    - `page_size`: Número de documentos por página
    - `total_pages`: `null` desde GDI-369. Ver nota de `total`.
    - `has_next`: si hay pagina siguiente (calculado con LIMIT page_size + 1).
    - `has_previous`: si `page > 1`.
    - `documents`: Lista paginada de documentos con información detallada

    Cada documento incluye:
    - Datos básicos (ID, referencia, estado)
    - Fechas de modificación
    - Información del editor
    - Rol del usuario en cada documento
    - Estado de las firmas

    ## Parámetros de filtrado

    - `status_filter`: Filtrar documentos por su estado visual (En edición, En proceso de firma, Firmar ahora, Firmado)
    - `date_filter`: Filtro predefinido de fechas (hoy, ayer, ultimos_7_dias, ultimos_30_dias)
    - `date_from`: Fecha de inicio para filtrado personalizado (formato YYYY-MM-DD)
    - `date_to`: Fecha de fin para filtrado personalizado (formato YYYY-MM-DD)
    - `document_type`: Filtrar documentos por tipo de documento usando su acrónimo (IF, ME, OF, etc.)
    - `doc_number`: Búsqueda exacta por número de documento (ej: ME-2023-001)

    ## Parámetros de paginación

    - `page`: Número de página que se desea mostrar (comienza en 1)
    - `page_size`: Cantidad de documentos por página (entre 1 y 100)

    Los documentos se devuelven paginados según los parámetros `page` y `page_size`.
    GDI-369: la respuesta ya no incluye conteo total; usar `has_next` para saber
    si hay pagina siguiente.

    ## Notas:

    - Solo se muestran documentos del usuario autenticado
    - No se pueden ver documentos de otros usuarios
    """
    logger.info(f"GET /users/documents - User: {request.state.tenant_user_id}")

    logger.info(f"GET /users/documents - User: {request.state.tenant_user_id}")

    try:
        user_id = request.state.tenant_user_id

        if status_filter and status_filter not in ["En edición", "En proceso de firma", "Firmar ahora", "A mi firma", "Firmado"]:
            logger.warning(f"Invalid status_filter: {status_filter}")
            raise HTTPException(
                status_code=400,
                detail="status_filter debe ser uno de: En edición, En proceso de firma, Firmar ahora, A mi firma, Firmado"
            )

        if date_filter and date_filter not in ["hoy", "ayer", "ultimos_7_dias", "ultimos_30_dias"]:
            logger.warning(f"Invalid date_filter: {date_filter}")
            raise HTTPException(
                status_code=400,
                detail="date_filter debe ser uno de: hoy, ayer, ultimos_7_dias, ultimos_30_dias"
            )

        logger.debug(f"Calling document_service.get_user_documents with filters: status={status_filter}, date={date_filter}, search={search}")
        response_data = await document_service.get_user_documents(
            user_id,
            status_filter,
            date_filter,
            date_from,
            date_to,
            document_type,
            page,
            page_size,
            doc_number,
            search=search,
            min_signers=min_signers,
            sector_filter=sector_filter,
            exclude_reserved=exclude_reserved,
            signature_mode=signature_mode,
            schema_name=schema_name
        )

        page = response_data["page"]
        page_size = response_data["page_size"]
        documents = response_data["documents"]
        has_next = response_data["has_next"]
        has_previous = response_data["has_previous"]

        formatted_documents = []
        for doc in documents:
            user_role_mapping = {
                'creador': 'creator',
                'firmante': 'signer',
                'numerador': 'numerator',
                'sector': 'sector_viewer',
                'otro': 'viewer'
            }
            user_role = user_role_mapping.get(doc.get("rol_usuario", ""), "creator")

            formatted_documents.append({
                "id": str(doc["document_id"]),
                "reference": doc["reference"],
                "display_status": doc["display_status"],
                "updated_at": doc["last_modified_at"].isoformat() if doc["last_modified_at"] else None,
                "document_type": {
                    "name": "Documento",
                    "acronym": doc["acronym"],
                    "is_reserved": bool(doc.get("document_type_is_reserved")),
                    "is_public": doc.get("document_type_visibility") == "publico",
                },
                "user_role": user_role,
                "last_editor_name": doc["last_editor_full_name"],
                "last_editor_profile_picture_url": doc.get("last_editor_profile_picture_url"),
                "last_editor_citizen_id": str(doc["last_editor_citizen_id"]) if doc.get("last_editor_citizen_id") else None,
                "last_editor_citizen_country_id": doc.get("last_editor_citizen_country_id"),
                "official_number": doc["official_number"],
                "creator_sector": f"{doc.get('creator_dept_acronym', '')}#{doc.get('creator_sector_acronym', '')}" if doc.get('creator_sector_acronym') else None,
                "sent_by_name": doc.get("sent_by_name"),
                "short_resume": doc.get("short_resume"),
                "resume": doc.get("resume"),
                "linked_cases": doc.get("linked_cases") or [],
                "linked_records": doc.get("linked_records") or [],
                "signers": doc.get("signers") or [],
            })

        logger.info(
            f"Returning {len(formatted_documents)} documents "
            f"(page {page}, has_next={has_next})"
        )

        return {
            "total": None,
            "page": page,
            "page_size": page_size,
            "total_pages": None,
            "has_next": has_next,
            "has_previous": has_previous,
            "documents": formatted_documents,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_user_documents: {str(e)}", exc_info=True)
        raise exception_to_http_exception(e)