from starlette.requests import Request
from starlette.responses import JSONResponse

from api_gateway.auth_rest import validate_rest_api_key
from api_gateway.tools import cases, system
from api_gateway.rest_common import (
    _error_response,
    _success_response,
    _get_api_key,
    _get_user_id,
    rest_endpoint,
)
from shared.logging import get_logger

logger = get_logger(__name__)


@rest_endpoint(require_user=False)
async def api_get_document_types(request: Request, ctx, user_id: str):
    return await system.get_document_types(ctx=ctx)


async def api_get_user_info(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    auth_user_id = _get_user_id(request)
    target_user_id = request.path_params.get("user_id")

    try:
        ctx = await validate_rest_api_key(api_key, auth_user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not target_user_id:
        return _error_response("user_id path param es requerido", status_code=400)

    if target_user_id != auth_user_id:
        return _error_response(
            "Solo puede consultar su propia informacion de usuario",
            status_code=403
        )

    try:
        result = await system.get_user_info(ctx=ctx, user_id=target_user_id)
        return _success_response(result)

    except ValueError as e:
        if "no encontrado" in str(e).lower():
            return _error_response(str(e), status_code=404)
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_user_info")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False)
async def api_get_document_states(request: Request, ctx, user_id: str):
    return await system.get_document_states(ctx=ctx)


@rest_endpoint(require_user=False)
async def api_get_sectors(request: Request, ctx, user_id: str):
    return await system.get_sectors(ctx=ctx)


async def api_get_case_templates(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = await validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    try:
        result = await system.get_case_templates(ctx=ctx, user_id=user_id)
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en get_case_templates")
        return _error_response("Error interno del servidor", status_code=500)


async def api_search_users(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = await validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    params = request.query_params
    q = params.get("q", "")
    limit = int(params.get("limit", 10))

    if not q or len(q) < 2:
        return _error_response("El parámetro 'q' es requerido y debe tener al menos 2 caracteres", status_code=400)

    try:
        result = await system.search_users(ctx=ctx, search=q, limit=limit)
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en search_users")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_sector_users(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    sector_id = request.path_params.get("sector_id")

    try:
        ctx = await validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not sector_id:
        return _error_response("sector_id es requerido", status_code=400)

    try:
        result = await cases.get_sector_users_list(ctx=ctx, sector_id=sector_id)
        return _success_response(result)

    except Exception as e:
        if "no encontrado" in str(e).lower() or "not found" in str(e).lower():
            return _error_response("Sector no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en get_sector_users")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False)
async def api_list_all_users(request: Request, ctx, user_id: str):
    return await system.list_all_users(ctx=ctx)
