from fastapi import APIRouter, Depends, File, UploadFile, Request, HTTPException

from models import schemas
from models.tags import Tags
from auth import get_current_user
from shared.dependencies import get_tenant_schema
from shared.exceptions import GDIBaseException, ExternalServiceError, exception_to_http_exception
from shared.logging import get_logger
from services.users.avatar import upload_profile_picture, MAX_AVATAR_BYTES

logger = get_logger(__name__)

router = APIRouter(tags=[Tags.USERS])


@router.post(
    "/users/profile/picture",
    summary="Subir foto de perfil (avatar)",
    description=(
        "Sube y reemplaza la foto de perfil del usuario autenticado. Se "
        "recorta a cuadrado, se reescala a 512px y se convierte a WEBP. "
        "La foto subida a BD pisa a la de Google/Auth0 en toda la app."
    ),
    responses={
        200: {"description": "Foto de perfil actualizada exitosamente"},
        400: {"description": "Formato no soportado o imagen inválida"},
        401: {"description": "No autenticado"},
        413: {"description": "Imagen supera el tamaño máximo permitido (~2MB)"},
    },
)
async def upload_profile_picture_endpoint(
    request: Request,
    picture: UploadFile = File(..., description="Archivo de imagen (PNG/JPEG/WEBP)"),
    current_user: schemas.AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    try:
        data = await picture.read(MAX_AVATAR_BYTES + 1)
        if len(data) > MAX_AVATAR_BYTES:
            raise HTTPException(status_code=413, detail="La imagen supera el tamaño máximo permitido (~2MB)")

        url = await upload_profile_picture(
            request.state.tenant_user_id, data, schema_name=schema_name
        )

        logger.info(f"Foto de perfil actualizada para {request.state.tenant_user_id}")
        return {"success": True, "data": {"profile_picture_url": url}}

    except HTTPException:
        raise
    except GDIBaseException as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error inesperado subiendo foto de perfil: {type(e).__name__} - {e}", exc_info=True)
        raise ExternalServiceError("Error al subir la foto de perfil")
