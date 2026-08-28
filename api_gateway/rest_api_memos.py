from starlette.requests import Request
from starlette.responses import JSONResponse

from api_gateway.auth_rest import validate_rest_api_key
from api_gateway.tools import memos
from api_gateway.rest_common import (
    _error_response,
    _success_response,
    _get_api_key,
    _get_user_id,
    _is_valid_uuid,
    rest_endpoint,
)
from shared.logging import get_logger

logger = get_logger(__name__)


async def api_get_memos(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = await validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    params = request.query_params
    try:
        page = int(params.get("page", 1))
        page_size = min(int(params.get("page_size", 20)), 100)
    except (ValueError, TypeError):
        return _error_response("page y page_size deben ser numeros enteros", status_code=400)
    search = params.get("search")

    try:
        result = await memos.get_memos(
            ctx=ctx,
            user_id=user_id,
            page=page,
            page_size=page_size,
            search=search
        )
        return _success_response(result)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_memos")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_sent_memos(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = await validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    params = request.query_params
    try:
        page = int(params.get("page", 1))
        page_size = min(int(params.get("page_size", 20)), 100)
    except (ValueError, TypeError):
        return _error_response("page y page_size deben ser numeros enteros", status_code=400)
    search = params.get("search")

    try:
        result = await memos.get_sent_memos_tool(
            ctx=ctx,
            user_id=user_id,
            page=page,
            page_size=page_size,
            search=search
        )
        return _success_response(result)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_sent_memos")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_archived_memos(request: Request) -> JSONResponse:
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = await validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    params = request.query_params
    try:
        page = int(params.get("page", 1))
        page_size = min(int(params.get("page_size", 20)), 100)
    except (ValueError, TypeError):
        return _error_response("page y page_size deben ser numeros enteros", status_code=400)
    search = params.get("search")

    try:
        result = await memos.get_archived_memos_tool(
            ctx=ctx,
            user_id=user_id,
            page=page,
            page_size=page_size,
            search=search
        )
        return _success_response(result)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_archived_memos")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False, value_error_status=400)
async def api_get_memo_detail(request: Request, ctx, user_id: str):
    memo_id = request.path_params.get("memo_id")

    if not memo_id:
        return _error_response("memo_id es requerido", status_code=400)
    if not _is_valid_uuid(memo_id):
        return _error_response("memo_id inválido (se espera UUID)", status_code=400)

    return await memos.get_memo_detail(
        ctx=ctx,
        memo_id=memo_id,
        user_id=user_id
    )
