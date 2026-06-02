"""
Endpoint para listar todos los usuarios del sistema - Clean Code Implementation.
Aplicando principios SOLID con endpoint delgado y servicio dedicado.
"""

from shared.logging import get_logger
from fastapi import APIRouter, Depends
from typing import List
from models.tags import Tags
from models.users.list import UserListItem, UserListResponse
from services.users.list import list_all_active_users
from shared.exceptions import exception_to_http_exception
from auth import get_current_user
from shared.dependencies import get_tenant_schema

logger = get_logger(__name__)
router = APIRouter(tags=[Tags.USERS])

@router.get(
    "/users",
    response_model=UserListResponse,
    summary="Listar todos los usuarios activos",
    dependencies=[Depends(get_current_user)],
    description="""
    **Endpoint refactorizado aplicando principios SOLID.**
    
    Obtiene una lista completa de todos los usuarios activos del sistema.
    
    **Funcionalidades:**
    - Lista usuarios con estado activo (estado = 1)
    - Resultados ordenados alfabéticamente por nombre completo
    - Útil para dropdowns, selectores y asignaciones
    
    **Respuesta incluye:**
    - `user_id`: UUID único del usuario
    - `full_name`: Nombre completo del usuario
    - `email`: Correo electrónico del usuario
    
    **Casos de uso:**
    - Llenar dropdowns de usuarios
    - Asignar firmantes a documentos
    - Selección de usuarios para permisos
    - Directorio general del sistema
    """,
    responses={
        200: {
            "description": "Lista de usuarios obtenida exitosamente",
            "content": {
                "application/json": {
                    "example": {
                        "users": [
                            {
                                "user_id": "550e8400-e29b-41d4-a716-446655440000",
                                "full_name": "Juan Pérez",
                                "email": "juan.perez@ejemplo.com"
                            },
                            {
                                "user_id": "660f9511-f39c-52e5-b827-557766551111",
                                "full_name": "María García",
                                "email": "maria.garcia@ejemplo.com"
                            }
                        ],
                        "total": 2
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
                        "detail": "Error al obtener lista de usuarios: [detalles del error]"
                    }
                }
            }
        }
    }
)
async def list_all_users(schema_name: str = Depends(get_tenant_schema)) -> UserListResponse:
    """
    Lista todos los usuarios activos aplicando Clean Architecture.

    Endpoint delgado que delega toda la lógica al servicio.
    Sigue el mismo patrón que profile y search refactorizados.
    """
    logger.info("GET /users - Listing all active users")

    try:
        users = await list_all_active_users(schema_name=schema_name)

        return UserListResponse(
            users=users,
            total=len(users)
        )

    except Exception as e:
        logger.error(f"Error in list_all_users endpoint: {str(e)}", exc_info=True)
        raise exception_to_http_exception(e)