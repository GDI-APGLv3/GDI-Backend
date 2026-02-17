"""
Endpoint para búsqueda de usuarios - Arquitectura modular.
Aplicando principios SOLID: Endpoint con separación total de responsabilidades.

Siguiendo el patrón de endpoints especializados:
- Endpoint de 2-3 líneas máximo
- Toda la lógica delegada al servicio  
- Manejo de errores centralizado
"""

from fastapi import APIRouter, Query, Depends
from typing import Optional

from models.users.search import UserSearchResponse
from models.tags import Tags
from auth import get_current_user
from services.users.search import search_users_for_autocomplete, search_or_create_user_by_email, is_email
from shared.exceptions import exception_to_http_exception
from shared.dependencies import get_tenant_schema

router = APIRouter()

@router.get(
    "/users/search",
    tags=[Tags.USERS],
    response_model=UserSearchResponse,
    summary="Buscar usuarios por nombre",
    dependencies=[Depends(get_current_user)],
    description="""
    **Endpoint refactorizado aplicando principios SOLID.**
    
    Busca usuarios por nombre completo para autocompletado y selección.
    **NUEVA FUNCIONALIDAD: Invitación de usuarios por email.**
    
    **⚠️ IMPORTANTE PARA FRONTEND:**
    - **MÍNIMO 2 CARACTERES** requeridos para búsqueda
    - **MÁXIMO 100 CARACTERES** permitidos
    - **MÁXIMO 100 RESULTADOS** por consulta
    
    **Funcionalidades:**
    - **Búsqueda por nombre:** inicio o contenido del nombre completo (case-insensitive)
    - **Búsqueda/invitación por email:** Si q es un email válido, busca usuario existente o crea uno inactivo
    - Información completa del usuario disponible
    - Resultados ordenados alfabéticamente por nombre
    - Límite configurable de resultados
    
    **Respuesta incluye:**
    - `user_id`: UUID único del usuario
    - `full_name`: Nombre completo del usuario (o "Usuario Invitado (email)" para nuevos)
    - `department_acronym`: Acrónimo del departamento (ej: ADGEN, OBPU)
    - `seal_name`: Nombre del sello asignado
    - `profile_picture_url`: URL de la foto de perfil
    - `is_active`: Estado activo del usuario (false para usuarios recién invitados)
    
    **Casos de uso:**
    - Barra de búsqueda con autocompletado (esperar ≥2 caracteres)
    - Selección de firmantes para documentos
    - **Invitación de usuarios externos por email**
    - Asignación de usuarios a tareas
    - Directorio de usuarios del sistema
    
    **Ejemplos de búsqueda:**
    - `q=Ma` → Encuentra "María Elena Rodríguez"
    - `q=Rod` → Encuentra usuarios con apellido Rodríguez
    - `q=admin` → Encuentra usuarios con "admin" en el nombre
    - **`q=juan@empresa.com` → Busca usuario con ese email o lo crea como inactivo**
    """,
    responses={
        200: {
            "description": "Búsqueda exitosa",
            "content": {
                "application/json": {
                    "examples": {
                        "search_by_name": {
                            "summary": "Búsqueda por nombre",
                            "value": {
                                "search_query": "Ma",
                                "users": [
                                    {
                                        "user_id": "550e8400-e29b-41d4-a716-446655440000",
                                        "full_name": "María Elena Rodríguez",
                                        "email": "maria.rodriguez@ejemplo.com",
                                        "department_acronym": "ADGEN",
                                        "seal_name": "Sello General",
                                        "profile_picture_url": "https://storage.com/profile.jpg",
                                        "is_active": True
                                    }
                                ],
                                "total_found": 1
                            }
                        },
                        "search_by_email": {
                            "summary": "Búsqueda/invitación por email",
                            "value": {
                                "search_query": "nuevo@ejemplo.com",
                                "users": [
                                    {
                                        "user_id": None,
                                        "full_name": "Usuario Invitado (nuevo@ejemplo.com)",
                                        "email": "nuevo@ejemplo.com",
                                        "department_acronym": None,
                                        "seal_name": None,
                                        "profile_picture_url": None,
                                        "is_active": False
                                    }
                                ],
                                "total_found": 1
                            }
                        }
                    }
                }
            }
        },
        400: {
            "description": "Error de validación - parámetros inválidos",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "El parámetro 'q' debe tener al menos 2 caracteres"
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
        422: {
            "description": "Error de validación - formato de email inválido",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "El formato del email no es válido"
                    }
                }
            }
        },
        500: {
            "description": "Error interno del servidor",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Error interno del servidor"
                    }
                }
            }
        }
    }
)
async def search_users_endpoint(
    q: str = Query(
        ...,
        min_length=2,
        max_length=100,
        description="⚠️ MÍNIMO 2 CARACTERES: Texto para buscar en nombres completos de usuarios O email para invitar",
        examples=["Ma"]
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=100,
        description="Límite de resultados (opcional, máx. 100). Sin límite por defecto"
    ),
    schema_name: str = Depends(get_tenant_schema)
) -> UserSearchResponse:
    """
    Busca usuarios aplicando principios de arquitectura modular.

    Endpoint que delega toda la lógica al servicio correspondiente.
    Sigue el mismo patrón que editor-details refactorizado.

    **NUEVA FUNCIONALIDAD:**
    - Si q es un email válido → Busca usuario por email o lo crea como inactivo
    - Si q es texto normal → Búsqueda normal por nombre
    """
    try:
        # Detectar si es búsqueda por email o por nombre
        if is_email(q):
            # Búsqueda/creación por email
            result = search_or_create_user_by_email(email=q, schema_name=schema_name)
        else:
            # Búsqueda normal por nombre - Single Responsibility
            result = search_users_for_autocomplete(search_query=q, limit=limit, schema_name=schema_name)

        # Respuesta directa usando **unpacking como en editor-details
        return UserSearchResponse(
            search_query=q,
            **result
        )

    except Exception as e:
        # Manejo centralizado de errores
        raise exception_to_http_exception(e)