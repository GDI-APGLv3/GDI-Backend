from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse

from api_gateway.auth_rest import validate_backup_api_key, BackupAuthError, check_and_log_sync_access
from api_gateway.tools import sync
from api_gateway.rest_common import _error_response, _success_response


def _backup_error_response(e: BackupAuthError) -> JSONResponse:
    headers = {}
    if e.retry_after:
        headers["Retry-After"] = str(e.retry_after)
    return JSONResponse({"error": e.message}, status_code=e.status_code, headers=headers)


async def api_sync_schema(request: Request) -> JSONResponse:
    try:
        backup_ctx = await validate_backup_api_key(request)
    except BackupAuthError as e:
        return _backup_error_response(e)

    result = await sync.get_sync_catalog(schema_name=backup_ctx["schema_name"])
    return _success_response(result)


async def api_sync_data(request: Request) -> JSONResponse:
    try:
        backup_ctx = await validate_backup_api_key(request)
    except BackupAuthError as e:
        return _backup_error_response(e)

    table = request.query_params.get("table")
    since = request.query_params.get("since")
    if not table or not since:
        return _error_response("Parámetros 'table' y 'since' son requeridos", 400)

    from api_gateway.tools.sync import SYNC_TABLES
    if table not in SYNC_TABLES:
        return _error_response(f"Tabla '{table}' no es sincronizable", 400)

    try:
        datetime.fromisoformat(since.replace('Z', '+00:00'))
    except ValueError:
        return _error_response("Formato de 'since' inválido. Use ISO 8601.", 400)

    try:
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 100)), 100)
    except (ValueError, TypeError):
        return _error_response("page y page_size deben ser enteros", 400)

    rate = backup_ctx.get("rate_limit_per_minute") or 1
    retry_after = await check_and_log_sync_access(
        api_key_id=backup_ctx["api_key_id"],
        schema_name=backup_ctx["schema_name"],
        action="sync_data",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent", ""),
        rate_limit_per_minute=rate
    )
    if retry_after is not None:
        return _backup_error_response(BackupAuthError("Rate limit", 429, retry_after))

    try:
        result = await sync.get_sync_data(table, since, page, page_size, schema_name=backup_ctx["schema_name"])
    except ValueError as e:
        return _error_response(str(e), 400)

    return _success_response(result)


async def api_sync_documents(request: Request) -> JSONResponse:
    try:
        backup_ctx = await validate_backup_api_key(request)
    except BackupAuthError as e:
        return _backup_error_response(e)

    since = request.query_params.get("since")
    if not since:
        return _error_response("Parámetro 'since' es requerido", 400)

    try:
        datetime.fromisoformat(since.replace('Z', '+00:00'))
    except ValueError:
        return _error_response("Formato de 'since' inválido. Use ISO 8601.", 400)

    try:
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 100)), 100)
    except (ValueError, TypeError):
        return _error_response("page y page_size deben ser enteros", 400)

    rate = backup_ctx.get("rate_limit_per_minute") or 1
    retry_after = await check_and_log_sync_access(
        api_key_id=backup_ctx["api_key_id"],
        schema_name=backup_ctx["schema_name"],
        action="sync_documents",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent", ""),
        rate_limit_per_minute=rate
    )
    if retry_after is not None:
        return _backup_error_response(BackupAuthError("Rate limit", 429, retry_after))

    result = await sync.get_sync_documents(since, page, page_size, schema_name=backup_ctx["schema_name"])
    return _success_response(result)
