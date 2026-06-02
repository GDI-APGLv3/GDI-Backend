"""
Endpoint unificado para firma de documentos.
Detecta automaticamente si el usuario es firmante comun o numerador.
Soporta firma electronica (Fase 1) y firma digital AutoFirma (Fase 2).
MIGRADO: Fase 6 asyncpg
"""
from uuid import UUID
from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request, Body
from typing import Dict, Any
from models.documents.unified_signing import SuperSignRequest, SuperSignResponse
from models.tags import Tags
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.documents.signing.unified_signing import super_sign_document
from services.documents.core.queries import get_user_info_for_signing_query
from database import fetch_one, fetch_one as db_fetch_one, get_conn
from fastapi import HTTPException
from shared.exceptions import (
    DocumentNotFoundError, DocumentStateError, ValidationError,
    AuthorizationError, ExternalServiceError, exception_to_http_exception
)
from shared.validation import validate_uuid
from shared.dependencies import get_tenant_schema

# === CONFIGURACIÓN ===
logger = get_logger("super_sign")

router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.post(
    "/documents/{document_id}/super-sign",
    response_model=SuperSignResponse,
    summary="Firma unificada de documento",
    description="Endpoint unificado que detecta automáticamente si el usuario es firmante común o numerador y ejecuta la lógica correspondiente",
    dependencies=[Depends(get_current_user)]
)
async def super_sign(
    request: Request,
    document_id: UUID = Path(..., description="UUID del documento a firmar"),
    body: SuperSignRequest = Body(default_factory=SuperSignRequest),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> Dict[str, Any]:
    """
    Firma unificada de documento con detección automática del rol.
    """
    document_id = str(document_id)

    try:
        logger.info(f"Usuario {request.state.tenant_user_id[:8]}... iniciando super firma para documento {document_id[:8]}...")

        # Buscar información del usuario autenticado en BD
        user_data = await fetch_one(
            get_user_info_for_signing_query(),
            request.state.tenant_user_id,
            schema_name=schema_name
        )

        if not user_data:
            logger.warning(f"Usuario {request.state.tenant_user_id} no encontrado en BD")
            raise ValidationError("Usuario no encontrado en el sistema")

        db_user_id = str(user_data['user_id'])

        logger.debug(f"Usuario validado: {user_data['full_name']}")

        # Validar formato UUID usando utilidad compartida
        if not validate_uuid(document_id):
            raise ValidationError("document_id debe ser un UUID válido")
        if not validate_uuid(db_user_id):
            raise ValidationError("user_id debe ser un UUID válido")

        # Leer signature_policy del tipo de documento
        signature_policy = 'electronic'
        is_numerator_check = False

        pol_row = await fetch_one(
            """
            SELECT dt.signature_policy, ds.is_numerator
            FROM document_signers ds
            JOIN document_draft dd ON ds.document_id = dd.id
            JOIN document_types dt ON dd.document_type_id = dt.id
            WHERE dd.id = $1 AND ds.user_id = $2
            """,
            document_id, db_user_id,
            schema_name=schema_name,
        )
        if pol_row:
            signature_policy = pol_row['signature_policy'] or 'electronic'
            is_numerator_check = bool(pol_row['is_numerator'])

        # Reglas de flujo:
        # - digital_all: SIEMPRE digital, sin excepción
        # - digital_num + numerador: SIEMPRE digital, sin excepción
        # - digital_num + NO numerador: usuario elige (default electrónico)
        # - electronic: SIEMPRE electrónico
        force_digital = (
            signature_policy == 'digital_all'
            or (signature_policy == 'digital_num' and is_numerator_check)
        )
        can_choose = signature_policy == 'digital_num' and not is_numerator_check

        if force_digital:
            use_digital = True
        elif can_choose:
            use_digital = (body.provider_name == "autofirma")
            logger.info(f"digital_num no-numerador: usuario eligió provider_name={body.provider_name!r}")
        else:
            use_digital = False

        if use_digital:
            logger.info(
                f"Firma digital AutoFirma (policy={signature_policy}, "
                f"is_numerator={is_numerator_check})"
            )
            from services.documents.signing.dispatcher import dispatch_digital_signing
            ip_addr = request.client.host if request.client else None
            ua = request.headers.get("user-agent")
            result = await dispatch_digital_signing(
                document_id, db_user_id,
                schema_name=schema_name,
                ip_address=ip_addr,
                user_agent=ua,
            )
        else:
            # Llamar al servicio unificado electronico (Fase 1)
            logger.info("Ejecutando lógica de firma electronica unificada...")
            result = await super_sign_document(document_id, db_user_id, schema_name=schema_name)

        logger.info(
            f"Firma iniciada/completada. flow={result.get('flow', 'electronic')}, "
            f"is_numerator={result.get('is_numerator')}"
        )

        # Formatear respuesta usando el modelo Pydantic
        return SuperSignResponse(**result)

    except ValidationError as e:
        # Caso especial: lock R2 activo → 409 Conflict
        if "document_already_signing" in str(e) or "lock R2 activo" in str(e):
            logger.warning(f"Lock R2 activo para documento {document_id[:8]}... → 409")
            raise HTTPException(
                status_code=409,
                detail="document_already_signing",
            )
        logger.warning(f"ValidationError en super firma: {e}")
        raise exception_to_http_exception(e)

    except (DocumentNotFoundError, DocumentStateError,
            AuthorizationError, ExternalServiceError) as e:
        logger.warning(f"Error conocido en super firma: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)

    except Exception as e:
        logger.error(f"Error inesperado en super firma: {e}")
        raise exception_to_http_exception(ExternalServiceError(f"Error interno del servidor: {str(e)}"))
