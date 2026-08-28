from fastapi import APIRouter, Depends, Request
from models import schemas
from services.users import profile as profile_service
from auth import get_current_user
from models.tags import Tags
from typing import Dict, Any
from shared.exceptions import ExternalServiceError, exception_to_http_exception, GDIBaseException
from shared.dependencies import get_tenant_schema
from shared.logging import get_logger
router = APIRouter(tags=[Tags.USERS])
logger = get_logger(__name__)


def _format_user_response(user_data: Dict[str, Any]) -> Dict[str, Any]:
    if user_data.get("last_access"):
        user_data["last_access"] = user_data["last_access"].isoformat()
    if user_data.get("created_at"):
        user_data["created_at"] = user_data["created_at"].isoformat()

    user_data["user_id"] = str(user_data["user_id"])
    if user_data.get("sector_id"):
        user_data["sector_id"] = str(user_data["sector_id"])
    if user_data.get("department_id"):
        user_data["department_id"] = str(user_data["department_id"])
    
    return user_data


@router.get(
    "/users/profile",
    response_model=schemas.User,
    summary="Obtener perfil del usuario",
    description="Recupera el perfil completo del usuario autenticado",
    responses={
        200: {
            "description": "Perfil del usuario obtenido exitosamente",
            "content": {
                "application/json": {
                    "example": {
                        "user_id": "550e8400-e29b-41d4-a716-446655440000",
                        "auth_id": "auth0|123456789",
                        "full_name": "Juan Pérez",
                        "email": "juan.perez@example.com",
                        "country_id": "20-12345678-9",
                        "profile_picture_url": "https://cloudflare.com/photo.jpg",
                        "sector_id": "660e8400-e29b-41d4-a716-446655440001",
                        "sector_acronym": "RRHH",
                        "department_id": "770e8400-e29b-41d4-a716-446655440002",
                        "department_name": "Recursos Humanos",
                        "department_acronym": "RRHH",
                        "default_seal_id": 1,
                        "default_seal_name": "Sello Municipal",
                        "default_seal_acronym": "SM",
                        "estado": 1,
                        "created_at": "2024-01-15T10:30:00",
                        "last_access": "2024-12-02T14:45:00"
                    }
                }
            }
        },
        401: {"description": "No autenticado - Token inválido o ausente"},
        404: {"description": "Usuario no encontrado en la base de datos"},
        500: {"description": "Error interno del servidor"}
    }
)
async def get_user_profile(request: Request, current_user: schemas.AuthenticatedUser = Depends(get_current_user), schema_name: str = Depends(get_tenant_schema)) -> Dict[str, Any]:
    """
    Obtiene el perfil completo del usuario autenticado.
    
    Este endpoint retorna toda la información disponible del usuario
    que está actualmente autenticado según su token JWT.
    
    ## Autenticación requerida:
    
    - Token JWT válido de Auth0 en el header Authorization: Bearer <token>
    
    ## Respuesta:
    
    - Información completa del usuario incluyendo:
      - Datos básicos (nombre, email, CUIT)
      - Metadatos (fechas de creación y último acceso)
      - Relaciones (sector, foto de perfil, sello por defecto)
    """
    try:
        logger.info(f"Obteniendo perfil para user_id {request.state.tenant_user_id} en schema {schema_name}")

        user_data = await profile_service.get_user_profile(
            request.state.tenant_user_id,
            schema_name=schema_name
        )

        formatted_user = _format_user_response(user_data)

        logger.info(f"Perfil obtenido exitosamente para {request.state.tenant_user_id}")
        return formatted_user
        
    except GDIBaseException as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error inesperado al obtener perfil de usuario: {str(e)}", exc_info=True)
        raise ExternalServiceError("Error al obtener el perfil del usuario")

@router.put(
    "/users/profile",
    response_model=schemas.User,
    summary="Actualizar perfil del usuario",
    description="Actualiza los datos del perfil del usuario autenticado",
    responses={
        200: {
            "description": "Perfil actualizado exitosamente",
            "content": {
                "application/json": {
                    "example": {
                        "user_id": "550e8400-e29b-41d4-a716-446655440000",
                        "auth_id": "auth0|123456789",
                        "full_name": "Juan Pérez Actualizado",
                        "email": "juan.perez@example.com",
                        "country_id": "20-98765432-1",
                        "profile_picture_url": "https://cloudflare.com/new_photo.jpg",
                        "sector_id": "660e8400-e29b-41d4-a716-446655440003",
                        "sector_acronym": "IT",
                        "department_id": "770e8400-e29b-41d4-a716-446655440004",
                        "department_name": "Tecnología",
                        "department_acronym": "IT",
                        "default_seal_id": 2,
                        "default_seal_name": "Sello IT",
                        "default_seal_acronym": "SIT",
                        "estado": 1,
                        "created_at": "2024-01-15T10:30:00",
                        "last_access": "2024-12-02T15:00:00"
                    }
                }
            }
        },
        400: {"description": "Solicitud inválida - No se enviaron campos para actualizar"},
        401: {"description": "No autenticado - Token inválido o ausente"},
        404: {"description": "Usuario no encontrado"},
        500: {"description": "Error interno del servidor"}
    }
)
async def update_user_profile(
    request: Request,
    body: schemas.UpdateUserRequest,
    current_user: schemas.AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> Dict[str, Any]:
    """
    Actualiza el perfil del usuario autenticado.
    
    Permite actualizar los campos editables del perfil del usuario.
    Solo se actualizan los campos que se envían en la petición.
    
    ## Autenticación requerida:
    
    - Token JWT válido de Auth0 en el header Authorization: Bearer <token>
    
    ## Parámetros (todos opcionales):
    
    - **full_name**: Nuevo nombre completo del usuario
    - **country_id**: Nuevo identificador nacional (CountryID en BD). En Argentina es el CUIT
    - **profile_picture_url**: URL de la nueva foto de perfil
    - **sector_id**: UUID del nuevo sector
    
    ## Respuesta:
    
    - Datos actualizados del usuario
    
    ## Notas:
    
    - Los campos auth_id y email no se pueden modificar
    - Solo se actualizan los campos proporcionados en la petición
    """
    try:
        logger.info(f"Actualizando perfil para user_id {request.state.tenant_user_id} en schema {schema_name}")

        updated_user = await profile_service.update_user_profile(
            user_id=request.state.tenant_user_id,
            full_name=body.full_name,
            country_id=body.country_id,
            profile_picture_url=body.profile_picture_url,
            sector_id=body.sector_id,
            schema_name=schema_name
        )

        formatted_user = _format_user_response(updated_user)

        logger.info(f"Perfil actualizado exitosamente para {request.state.tenant_user_id}")
        return formatted_user
        
    except GDIBaseException as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error inesperado al actualizar perfil de usuario: {str(e)}", exc_info=True)
        raise ExternalServiceError("Error al actualizar el perfil del usuario")