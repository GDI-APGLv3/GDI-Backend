import json
from datetime import datetime, date
from functools import wraps
from typing import Optional
from uuid import UUID

from starlette.requests import Request
from starlette.responses import JSONResponse

from api_gateway.auth_rest import validate_rest_api_key
from shared.logging import get_logger
from shared.exceptions import (
    GDIBaseException,
    ValidationError,
    AuthorizationError,
    NotFoundError,
    ConflictError,
    BusinessLogicError,
    DatabaseError,
    DatabaseBusyError,
    TransientLookupError,
    ExternalServiceError,
    NotaryBreakerOpenError,
    EscriQueueFullError,
    causada_por_pool_saturado,
)

logger = get_logger(__name__)


def _get_api_key(request: Request) -> Optional[str]:
    return request.headers.get("X-API-Key")


def _get_user_id(request: Request) -> Optional[str]:
    return request.headers.get("X-User-ID")


def _json_serializer(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _error_response(message: str, status_code: int = 400, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse({"error": message}, status_code=status_code, headers=headers)


def _success_response(
    data: dict, status_code: int = 200, headers: dict[str, str] | None = None
) -> JSONResponse:
    json_str = json.dumps(data, default=_json_serializer)
    return JSONResponse(
        content=json.loads(json_str), status_code=status_code, headers=headers,
    )


def _is_valid_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def map_exception_to_response(exc: Exception, endpoint_name: str,
                              not_found_message: Optional[str] = None) -> JSONResponse:
    if isinstance(exc, TransientLookupError):
        return _error_response(str(exc), status_code=503, retry_after=5)
    if isinstance(exc, DatabaseBusyError):
        return _error_response("Servidor ocupado, reintente en unos segundos",
                               status_code=503, retry_after=1)
    if causada_por_pool_saturado(exc):
        logger.warning("[GDI-372] %s en %s enmascaraba un pool saturado; se responde 503",
                       type(exc).__name__, endpoint_name)
        return _error_response("Servidor ocupado, reintente en unos segundos",
                               status_code=503, retry_after=1)
    if isinstance(exc, ValidationError):
        return _error_response(str(exc), status_code=400)
    if isinstance(exc, AuthorizationError):
        return _error_response(str(exc), status_code=403)
    if isinstance(exc, NotFoundError):
        return _error_response(not_found_message or str(exc), status_code=404)
    if isinstance(exc, EscriQueueFullError):
        return _error_response(str(exc), status_code=429, retry_after=exc.retry_after)
    if isinstance(exc, ConflictError):
        return _error_response(str(exc), status_code=409)
    if isinstance(exc, BusinessLogicError):
        return _error_response(str(exc), status_code=422)
    if isinstance(exc, NotaryBreakerOpenError):
        return _error_response(str(exc), status_code=503, retry_after=exc.retry_after)
    if isinstance(exc, ExternalServiceError):
        return _error_response(str(exc), status_code=502)
    if isinstance(exc, DatabaseError):
        logger.exception(f"[REST API] Error de BD en {endpoint_name}")
        return _error_response("Error interno del servidor", status_code=500)

    logger.exception(f"[REST API] Error en {endpoint_name}")
    return _error_response("Error interno del servidor", status_code=500)


def rest_endpoint(require_user: bool = True,
                  value_error_status: int = 400,
                  not_found_message: Optional[str] = None):
    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request) -> JSONResponse:
            endpoint_name = func.__name__
            api_key = _get_api_key(request)
            user_id = _get_user_id(request)

            if require_user and not user_id:
                return _error_response("X-User-ID es requerido", status_code=401)

            try:
                ctx = await validate_rest_api_key(api_key, user_id)
            except GDIBaseException as e:
                return map_exception_to_response(e, endpoint_name,
                                                 not_found_message=not_found_message)
            except ValueError as e:
                return _error_response(str(e), status_code=401)

            try:
                result = await func(request, ctx, user_id)
                if isinstance(result, JSONResponse):
                    return result
                return _success_response(result)
            except ValueError as e:
                return _error_response(str(e), status_code=value_error_status)
            except GDIBaseException as e:
                return map_exception_to_response(e, endpoint_name,
                                                 not_found_message=not_found_message)
            except Exception as e:
                logger.exception(f"[REST API] Error en {endpoint_name}")
                return _error_response("Error interno del servidor", status_code=500)

        return wrapper
    return decorator
