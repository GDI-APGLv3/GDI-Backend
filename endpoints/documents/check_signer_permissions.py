from shared.logging import get_logger
from fastapi import APIRouter, Request, Depends, Query
from shared.exceptions import exception_to_http_exception, DatabaseError
from models.tags import Tags
from shared.dependencies import get_tenant_schema
from auth import get_current_user
from database import fetch_one

logger = get_logger("check_signer_permissions")

router = APIRouter(tags=[Tags.DOCUMENTOS])


def _build_fail_closed_response(document_type_acronym: str, detail: str = "") -> dict:
    msg = (
        detail
        or f"No se encontraron datos para el tipo de documento '{document_type_acronym}'. "
        f"Verificar que exista."
    )
    return {
        "can_sign": False,
        "has_rank_permission": False,
        "has_sector_permission": False,
        "user_rank": None,
        "required_rank": None,
        "document_type": None,
        "message": msg,
    }


@router.get(
    "/check-signer-permissions",
    summary="Validar permisos de firmante numerador",
    description="""Verifica si un usuario puede numerar un tipo de documento dado.

    **Uso en frontend:**
    - Al guardar documento, antes de enviar a firma
    - Validacion preventiva para mostrar advertencias al usuario

    **Logica (validador único compartido):**
    - Titularidad: el usuario debe ser titular (head_user_id) de una reparticion
      cuyo rango este habilitado para el tipo de documento. Sin restriccion de rango
      configurada → cualquier usuario pasa.
    - Sector: el sector del usuario debe estar habilitado para ese tipo de documento.
      Sin sectores configurados → cualquier usuario pasa.
    - Fail-closed: si no se encuentra el tipo o usuario, retorna can_sign=false.

    **Diferencia con la firma real:**
    - Este endpoint NO necesita un document_draft existente.
    - Solo necesita document_type_acronym + user_id.
    """,
)
async def check_signer_permissions(
    request: Request,
    document_type_acronym: str = Query(
        ..., description="Acronimo del tipo de documento (ej: DEC, IF, RES)"
    ),
    user_id: str = Query(
        ..., description="UUID del firmante numerador a validar"
    ),
    schema_name: str = Depends(get_tenant_schema),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Valida si un usuario puede numerar un tipo de documento.

    Returns:
        dict con can_sign, has_rank_permission, has_sector_permission,
        user_rank (null), required_rank (null), document_type, message.
    """
    try:
        logger.info(
            f"Validando permisos de numeracion: user={user_id}, "
            f"doc_type={document_type_acronym}, schema={schema_name}"
        )

        type_row = await fetch_one(
            "SELECT id, name FROM document_types WHERE acronym = $1",
            document_type_acronym,
            schema_name=schema_name,
        )

        if not type_row:
            logger.warning(
                f"Tipo de documento '{document_type_acronym}' no encontrado. "
                f"Fail-closed: can_sign=false"
            )
            return _build_fail_closed_response(document_type_acronym)

        document_type_id: int = type_row["id"]
        document_type_name: str = type_row["name"]

        from services.documents.signing.numbering_permissions import (
            can_user_number_document_type,
        )

        has_rank, has_sector, reason = await can_user_number_document_type(
            user_id,
            document_type_id,
            schema_name=schema_name,
        )

        can_sign = has_rank and has_sector

        logger.info(
            f"Resultado validacion: can_sign={can_sign}, "
            f"rank={has_rank}, sector={has_sector}"
        )

        return {
            "can_sign": can_sign,
            "has_rank_permission": has_rank,
            "has_sector_permission": has_sector,
            "user_rank": None,
            "required_rank": None,
            "document_type": document_type_name,
            "message": reason,
        }

    except DatabaseError as e:
        logger.error(f"Error de base de datos al validar permisos: {e}")
        raise exception_to_http_exception(e)
