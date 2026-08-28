
from fastapi import APIRouter, Path, Depends
from typing import Any, Dict, List
from shared.logging import get_logger
from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.exceptions import DocumentNotFoundError, exception_to_http_exception
from database import fetch_one
from models.tags import Tags

logger = get_logger(__name__)
router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.get(
    "/documents/types/{type_id}/fields",
    summary="Obtener campos de un tipo con formulario controlado",
    description=(
        "Retorna la definicion de campos (field_definitions) de un tipo de documento "
        "que tenga formulario controlado (fila en document_type_fields). "
        "Necesario para que el frontend renderice el panel de formulario al crear o editar "
        "un documento de ese tipo. "
        "Retorna 404 si el tipo no existe o no tiene formulario controlado configurado."
    ),
    response_model=Dict[str, Any],
    responses={
        200: {"description": "Definicion de campos obtenida exitosamente"},
        404: {"description": "Tipo de documento no encontrado o sin formulario controlado"},
    },
)
async def get_document_type_fields(
    type_id: int = Path(..., description="ID numerico del tipo de documento"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> Dict[str, Any]:
    """
    Obtiene la definicion de campos de un tipo de documento con formulario controlado.

    Un tipo tiene formulario controlado si existe una fila en document_type_fields
    para su document_type_id. El tipo base (HTML, NOTA, MEMO) es irrelevante para
    este endpoint — lo que determina si hay formulario es la presencia de esa fila.

    Args:
        type_id: ID (int) del tipo de documento.
        current_user: Usuario autenticado.
        schema_name: Schema del tenant.

    Returns:
        Dict con type_id, type_name, type_acronym y field_definitions.

    Raises:
        HTTPException 404: Si el tipo no existe o no tiene formulario controlado.
    """
    try:
        type_row = await fetch_one(
            "SELECT id, name, acronym, type FROM document_types WHERE id = $1",
            type_id,
            schema_name=schema_name,
        )
        if not type_row:
            raise DocumentNotFoundError(f"Tipo de documento {type_id} no encontrado")

        fd_row = await fetch_one(
            "SELECT field_definitions FROM document_type_fields WHERE document_type_id = $1",
            type_id,
            schema_name=schema_name,
        )
        if not fd_row:
            raise DocumentNotFoundError(
                f"El tipo de documento {type_id} ('{type_row['type']}') "
                "no tiene formulario controlado configurado"
            )

        field_definitions: List = fd_row['field_definitions'] if fd_row['field_definitions'] else []

        logger.info(
            f"field_definitions obtenidas para tipo {type_id} ({type_row['type']}) "
            f"({len(field_definitions)} campos, schema={schema_name})"
        )

        return {
            "type_id": type_id,
            "type_name": type_row['name'],
            "type_acronym": type_row['acronym'],
            "field_definitions": field_definitions,
        }

    except DocumentNotFoundError as e:
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Error obteniendo fields del tipo {type_id}: {e}")
        raise exception_to_http_exception(e)
