from uuid import UUID
from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request, Body
from typing import Dict, Any
from models.documents.unified_signing import SuperSignRequest, SuperSignResponse
from models.tags import Tags
from auth import get_current_user
from database import fetch_one
from models.schemas import AuthenticatedUser
from services.documents.signing.unified_signing import super_sign_document
from services.documents.signing.lookup_guard import (
    confirm_user_missing, resolve_signature_policy,
)
from services.documents.core.queries import get_user_info_for_signing_query
from fastapi import HTTPException
from shared.exceptions import (
    DocumentNotFoundError, DocumentStateError, ValidationError,
    AuthorizationError, ExternalServiceError, exception_to_http_exception,
    SpecialLaneBusyError, NotaryBreakerOpenError, EscriQueueFullError,
    SignerTurnPendingError, TransientLookupError,
)
from shared.validation import validate_uuid
from shared.dependencies import get_tenant_schema

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

        user_data = await fetch_one(
            get_user_info_for_signing_query(),
            request.state.tenant_user_id,
            schema_name=schema_name
        )

        if not user_data:
            logger.warning(f"Usuario {request.state.tenant_user_id} no encontrado en BD — confirmando")
            await confirm_user_missing(
                request.state.tenant_user_id,
                schema_name=schema_name,
                context="super_sign.user_info",
            )
            raise ValidationError("Usuario no encontrado en el sistema")

        db_user_id = str(user_data['user_id'])

        logger.debug(f"Usuario validado: {user_data['full_name']}")

        if not validate_uuid(document_id):
            raise ValidationError("document_id debe ser un UUID válido")
        if not validate_uuid(db_user_id):
            raise ValidationError("user_id debe ser un UUID válido")

        signature_policy, is_numerator_check = await resolve_signature_policy(
            document_id, db_user_id,
            schema_name=schema_name,
            context="super_sign.signature_policy",
        )

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
                f"Firma digital iniciada (FirmadorGDI) (policy={signature_policy}, "
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
            logger.info("Ejecutando lógica de firma electronica unificada...")
            result = await super_sign_document(document_id, db_user_id, schema_name=schema_name)

        logger.info(
            f"Firma iniciada/completada. flow={result.get('flow', 'electronic')}, "
            f"is_numerator={result.get('is_numerator')}"
        )

        return SuperSignResponse(**result)

    except NotaryBreakerOpenError as e:
        logger.warning(
            f"Notary breaker OPEN para documento {document_id[:8]}...: "
            f"retry_after={e.retry_after}s"
        )
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": str(e.retry_after)},
            detail={
                "message": e.message,
                "type": "NotaryBreakerOpenError",
            },
        )

    except EscriQueueFullError as e:
        logger.warning(
            f"Cola de firma async saturada para documento {document_id[:8]}...: "
            f"reason={e.reason} retry_after={e.retry_after}s"
        )
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": str(e.retry_after)},
            detail={
                "message": e.message,
                "type": "EscriQueueFullError",
                "reason": e.reason,
            },
        )

    except SignerTurnPendingError as e:
        logger.info(
            f"Firma fuera de turno para documento {document_id[:8]}...: "
            f"{e.pending_common_signers} firma(s) anterior(es) en proceso"
        )
        raise HTTPException(
            status_code=409,
            headers={"Retry-After": "5"},
            detail={
                "message": e.message,
                "type": "SignerTurnPendingError",
                "pending_common_signers": e.pending_common_signers,
            },
        )

    except TransientLookupError as e:
        logger.warning(f"Lectura transitoria fallida para documento {document_id[:8]}...: {e.message}")
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": "5"},
            detail={
                "message": e.message,
                "type": "TransientLookupError",
            },
        )

    except SpecialLaneBusyError as e:
        logger.warning(
            f"Carril SPECIAL ocupado para documento {document_id[:8]}...: {e.message}"
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": e.message,
                "type": "SpecialLaneBusyError",
            },
        )

    except ValidationError as e:
        if "document_already_signing" in str(e) or "lock R2 activo" in str(e):
            logger.info(f"Lock R2 activo para documento {document_id[:8]}... → 409")
            raise HTTPException(
                status_code=409,
                headers={"Retry-After": "5"},
                detail={
                    "message": (
                        "Este documento ya se está firmando en este momento. "
                        "Esperá unos segundos y volvé a intentar."
                    ),
                    "type": "DocumentAlreadySigningError",
                    "code": "document_already_signing",
                },
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
