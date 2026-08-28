
from fastapi import APIRouter, Depends, Request

from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.documents.signing.firmador_version import (
    URL_DESCARGA,
    aviso_para_usuario,
)
from shared.logging import get_logger

log = get_logger(__name__)
router = APIRouter()


@router.get("/digital-signature/firmador-version")
async def version_del_firmador(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """
    Devuelve `update_disponible: null` si está al día o si nunca lo vimos firmar.

    Que sea `null` cuando no hay registro es deliberado: puede ser alguien que
    todavía no instaló el programa, y a ese ya le habla el texto de "si es tu
    primera vez". Decirle "actualizá" a quien nunca instaló nada confunde.
    """
    user_id = str(request.state.tenant_user_id)

    return {
        "update_disponible": await aviso_para_usuario(user_id),
        "url_descarga": URL_DESCARGA,
    }
