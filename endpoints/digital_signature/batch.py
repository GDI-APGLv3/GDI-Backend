from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.exceptions import ValidationError, SpecialLaneBusyError
from shared.logging import get_logger

from services.documents.signing.firmador_version import aviso_para_usuario
from services.documents.signing.batch_digital import (
    MAX_DOCUMENTOS_POR_TANDA,
    abrir_tanda,
    cancelar_tanda,
    estado_de_tanda,
)

log = get_logger(__name__)
router = APIRouter()


class AbrirTandaRequest(BaseModel):
    document_ids: list[str] = Field(..., min_length=1, max_length=MAX_DOCUMENTOS_POR_TANDA)


class CancelarTandaRequest(BaseModel):
    batch_id: str
    reason: str | None = None


@router.post("/documents/batch-digital-sign")
async def abrir_tanda_endpoint(
    body: AbrirTandaRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> dict:
    """
    Prepara los N documentos y devuelve UNA URI para abrir el firmador una sola
    vez. Todo o nada: si uno falla al prepararse, no se abre nada.
    """
    user_id = str(request.state.tenant_user_id)

    try:
        resultado = await abrir_tanda(
            body.document_ids,
            user_id,
            schema_name=schema_name,
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
        )
    except SpecialLaneBusyError as e:
        log.info("tanda.carril_ocupado user=%s: %s", user_id[:8], e)
        raise HTTPException(
            status_code=409,
            detail=(
                "Hay una numeración en curso para uno de estos tipos de documento "
                "en tu repartición. Reintentá en unos segundos."
            ),
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    resultado["update_disponible"] = await aviso_para_usuario(user_id)

    return resultado


@router.get("/digital-signature/batch-poll/{batch_id}")
async def poll_de_tanda(
    batch_id: str,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> dict:
    """
    Estado del conjunto. No ejecuta trabajo: cada documento lo cierra su propio
    poll, y la promoción al bucket oficial la dispara la tanda cuando está
    completa. Acá solo se informa.
    """
    from endpoints.digital_signature.poll import _poll_rate_limit_ok

    user_id = str(request.state.tenant_user_id)
    if not _poll_rate_limit_ok(str(current_user.user_id), f"batch:{batch_id}"):
        raise HTTPException(status_code=429, detail="too_many_poll_requests")

    estado = await estado_de_tanda(batch_id, schema_name=schema_name, user_id=user_id)
    if estado is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    estado["update_disponible"] = await aviso_para_usuario(user_id)
    return estado


@router.post("/digital-signature/batch-cancel")
async def cancelar_tanda_endpoint(
    body: CancelarTandaRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> dict:
    """
    Baja la tanda entera: ningún documento queda firmado, los números vuelven
    al circuito juntos y consecutivos, y los PDF que estaban esperando se
    borran.
    """
    user_id = str(request.state.tenant_user_id)
    try:
        return await cancelar_tanda(
            body.batch_id,
            schema_name=schema_name,
            motivo=(body.reason or "cancelled_by_user")[:200],
            user_id=user_id,
        )
    except ValidationError as e:
        raise HTTPException(status_code=403, detail=str(e))
