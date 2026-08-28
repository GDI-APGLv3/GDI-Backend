from starlette.requests import Request
from starlette.responses import JSONResponse

from api_gateway.auth_rest import validate_rest_api_key
from api_gateway.rest_common import _error_response, _success_response, _get_api_key, _get_user_id, _is_valid_uuid
from api_gateway.tools import documents
from services.documents.signing.async_poll_status import get_async_poll_status
from shared.exceptions import (
    ValidationError,
    AuthorizationError,
    DocumentNotFoundError,
    DocumentStateError,
    SpecialLaneBusyError,
    SignerTurnPendingError,
    TransientLookupError,
)
from shared.logging import get_logger

logger = get_logger(__name__)


def _rewrite_poll_url(result):
    if isinstance(result, dict):
        poll = result.get("poll_url")
        if isinstance(poll, str) and poll.startswith("/signing/async-poll/"):
            result["poll_url"] = f"/api/v1{poll}"
    return result


async def api_start_signing(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = await validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)
    if not _is_valid_uuid(document_id):
        return _error_response("document_id inválido (se espera UUID)", status_code=400)

    try:
        result = await documents.start_signing(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id
        )
        return _success_response(_rewrite_poll_url(result))

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except ValidationError as e:
        return _error_response(str(e), status_code=400)
    except DocumentNotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except DocumentStateError as e:
        return _error_response(str(e), status_code=409)
    except Exception as e:
        error_msg = str(e).lower()
        if "no autorizado" in error_msg or "unauthorized" in error_msg:
            return _error_response("Usuario no autorizado", status_code=403)
        if "usuario" in error_msg and "no encontrado" in error_msg:
            return _error_response("Usuario no encontrado", status_code=404)
        if "documento" in error_msg and "no encontrado" in error_msg:
            return _error_response("Documento no encontrado", status_code=404)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response(str(e), status_code=404)
        if "estado" in error_msg or "state" in error_msg:
            return _error_response("Documento no puede firmarse en su estado actual", status_code=409)
        logger.exception(f"[REST API] Error en start_signing")
        return _error_response("Error interno del servidor", status_code=500)


async def api_sign_document(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = await validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)
    if not _is_valid_uuid(document_id):
        return _error_response("document_id inválido (se espera UUID)", status_code=400)

    try:
        from services.documents.signing.lookup_guard import resolve_signature_policy
        policy, is_numerator = await resolve_signature_policy(
            document_id, user_id,
            schema_name=ctx.schema_name,
            context="rest_api.sign_document",
        )
        requires_digital = policy == "digital_all" or (policy == "digital_num" and is_numerator)
        if requires_digital:
            return _error_response(
                "Este documento requiere firma con token físico. "
                "Firmá desde el portal web.",
                status_code=422,
            )
    except TransientLookupError as e:
        return _error_response(str(e), status_code=503)
    except ValidationError as e:
        msg = str(e)
        if "signature_policy es NULL" in msg or "política de firma" in msg.lower() and "no tiene configurada" in msg.lower():
            return _error_response(msg, status_code=422)
        return _error_response(msg, status_code=403)
    except Exception:
        logger.exception("[REST API] sign_document policy_check error")
        return _error_response("Error interno al verificar permisos de firma", status_code=500)

    try:
        result = await documents.sign_document(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id
        )
        return _success_response(_rewrite_poll_url(result))

    except SpecialLaneBusyError as e:
        return _error_response(str(e), status_code=409)
    except SignerTurnPendingError as e:
        return _error_response(str(e), status_code=409)
    except TransientLookupError as e:
        return _error_response(str(e), status_code=503)
    except Exception as e:
        error_msg = str(e).lower()
        if "no autorizado" in error_msg or "unauthorized" in error_msg or "permiso" in error_msg:
            return _error_response("Usuario no autorizado para firmar este documento", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Documento no encontrado", status_code=404)
        if "estado" in error_msg or "state" in error_msg:
            return _error_response("Documento no puede firmarse en su estado actual", status_code=409)
        logger.exception(f"[REST API] Error en sign_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_reject_document(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = await validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)
    if not _is_valid_uuid(document_id):
        return _error_response("document_id inválido (se espera UUID)", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    reason = body.get("reason")

    if not reason or not reason.strip():
        return _error_response("reason es requerido y no puede estar vacío", status_code=400)

    try:
        result = await documents.reject_document(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id,
            reason=reason
        )
        return _success_response(result)

    except Exception as e:
        error_msg = str(e).lower()
        if "no autorizado" in error_msg or "unauthorized" in error_msg or "permiso" in error_msg:
            return _error_response("Usuario no autorizado para rechazar este documento", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Documento no encontrado", status_code=404)
        if "estado" in error_msg or "state" in error_msg:
            return _error_response("Documento no puede rechazarse en su estado actual", status_code=409)
        logger.exception(f"[REST API] Error en reject_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_async_poll(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    session_id = request.path_params.get("session_id")

    try:
        ctx = await validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not session_id:
        return _error_response("session_id es requerido", status_code=400)
    if not _is_valid_uuid(session_id):
        return _error_response("session_id inválido (se espera UUID)", status_code=400)

    try:
        result = await get_async_poll_status(
            session_id,
            user_id,
            schema_name=ctx.schema_name,
        )
    except Exception:
        logger.exception("[REST API] Error en async_poll")
        return _error_response("Error interno del servidor", status_code=500)

    if result is None:
        return _error_response("Sesión de firma no encontrada", status_code=404)

    return _success_response(result)
