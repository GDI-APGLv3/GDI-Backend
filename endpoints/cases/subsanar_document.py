
from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.cases.subsanacion import subsanar_document_service
from shared.validation import validate_uuid
from shared.exceptions import (
    ValidationError,
    NotFoundError,
    AuthorizationError,
    exception_to_http_exception
)
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from config.constants import (
    SUBSANAR_SUCCESS,
    SUBSANAR_SAME_DOCUMENT_ERROR
)
from shared.logging import get_logger
logger = get_logger(__name__)

router = APIRouter(tags=["expedientes"])


class SubsanarDocumentRequest(BaseModel):
    official_document_id_erroneo: str = Field(
        ...,
        description="UUID del documento oficial erróneo que se desactivará"
    )
    official_document_id_justifica: str = Field(
        ...,
        description="UUID del documento oficial correcto que justifica la subsanación"
    )


class SubsanarDocumentResponse(BaseModel):
    success: bool
    data: dict
    message: str


@router.post("/{case_id}/subsanar", response_model=SubsanarDocumentResponse)
async def subsanar_document(
    request: Request,
    body: SubsanarDocumentRequest,
    case_id: str = Path(..., description="UUID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Subsanar documento oficial erróneo en expediente

    Solo usuarios **ADMIN** del expediente pueden ejecutar esta acción.
    (Mismo permiso requerido que para transferir expedientes)

    **Proceso:**
    1. Desactiva el documento oficial erróneo (marca is_active=false)
    2. Vincula el documento oficial que justifica la subsanación
    3. Registra auditoría completa (quién, cuándo, qué)

    **Validaciones:**
    - Usuario debe tener rol ADMIN sobre el expediente
    - Documento erróneo debe estar activo (no subsanado previamente)
    - Documento erróneo debe estar vinculado al expediente
    - Documento que justifica no debe estar duplicado en el expediente

    **Args:**
    - **case_id**: UUID del expediente
    - **official_document_id_erroneo**: UUID del documento a desactivar
    - **official_document_id_justifica**: UUID del documento correcto

    **Returns:**
    ```json
    {
      "success": true,
      "data": {
        "deactivated_document": {
          "id": "uuid",
          "official_number": "DOC-2025-00001-SMG-DEPT",
          "deactivated_at": "2025-10-21T21:30:00",
          "deactivated_by_user_id": "uuid"
        },
        "linked_document": {
          "link_id": "uuid",
          "official_number": "DOC-2025-00002-SMG-DEPT",
          "order_number": 3,
          "linking_date": "2025-10-21T21:30:00"
        }
      },
      "message": "Documento subsanado exitosamente"
    }
    ```

    **Errores:**
    - **400**: Validación fallida (documento ya subsanado, duplicado, etc)
    - **403**: Usuario sin permisos ADMIN
    - **404**: Expediente o documento no encontrado
    - **500**: Error interno del servidor

    ARQUITECTURA:
    Este endpoint sigue el Patrón A (Clean Architecture):
    - Validaciones simples (UUIDs)
    - Llama a servicio de negocio (subsanar_document_service)
    - Retorna respuesta HTTP

    Ver: services/cases/subsanacion.py para la lógica completa
    """
    try:
        logger.info(f"Starting document subsanation for case {case_id[:8]}...")
        logger.info(f"User: {request.state.tenant_user_id[:8]}...")
        logger.info(f"Erroneous document: {body.official_document_id_erroneo[:8]}...")
        logger.info(f"Justifying document: {body.official_document_id_justifica[:8]}...")

        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        validate_uuid(case_id)
        validate_uuid(body.official_document_id_erroneo)
        validate_uuid(body.official_document_id_justifica)

        if body.official_document_id_erroneo == body.official_document_id_justifica:
            logger.error("Same document provided as erroneous and justifying")
            raise ValidationError(SUBSANAR_SAME_DOCUMENT_ERROR)

        logger.info("Calling subsanar_document_service...")

        result = await subsanar_document_service(
            case_id=case_id,
            official_document_id_erroneo=body.official_document_id_erroneo,
            official_document_id_justifica=body.official_document_id_justifica,
            user_id=db_user_id,
            schema_name=schema_name
        )

        logger.info(f"Service completed successfully")
        logger.info(f"Deactivated: {result['deactivated_document']['official_number']}")
        logger.info(f"Linked: {result['linked_document']['official_number']}")

        return SubsanarDocumentResponse(
            success=True,
            data=result,
            message=f"{SUBSANAR_SUCCESS}: {result['deactivated_document']['official_number']} → {result['linked_document']['official_number']}"
        )

    except (ValidationError, NotFoundError, AuthorizationError) as e:
        logger.error(f"Error in subsanar_document: {type(e).__name__}: {str(e)}")
        raise exception_to_http_exception(e)

    except Exception as e:
        logger.error(f"Unexpected error in subsanar_document: {type(e).__name__}: {str(e)}")
        raise exception_to_http_exception(e)
