import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse

from shared.logging import get_logger
from api_gateway.rest_common import _success_response, _error_response
from api_gateway.auth_rest import validate_public_api_key, PublicAuthError
from api_gateway.public_info.rate_limit import check_public_rate_limit, get_public_client_ip
from api_gateway.public_info.records import (
    list_registries_public,
    list_records_public,
    get_record_public,
    get_public_families,
)
from api_gateway.public_info.documents import get_document_content_public
from api_gateway.public_info.search import public_search

logger = get_logger(__name__)

PUBLIC_IP_SEARCH_LIMIT = 30
PUBLIC_IP_LIST_LIMIT = 60


async def _authenticate_public(request: Request):
    muni = (request.path_params.get("muni") or "").strip().lower()
    if not muni:
        return None, _error_response("Municipio no encontrado", status_code=404)
    api_key = request.headers.get("X-API-Key")
    try:
        schema_name = await validate_public_api_key(api_key, muni)
    except PublicAuthError as e:
        return None, _error_response(e.message, status_code=e.status_code)
    return schema_name, None


def _with_cache_headers(resp: JSONResponse) -> JSONResponse:
    resp.headers["Cache-Control"] = "private, max-age=60"
    resp.headers["Vary"] = "X-API-Key"
    return resp


async def api_public_search(request: Request) -> JSONResponse:
    ip = get_public_client_ip(request)
    check_public_rate_limit(ip, PUBLIC_IP_SEARCH_LIMIT, window_seconds=60)

    schema_name, err = await _authenticate_public(request)
    if err:
        return err

    q = (request.query_params.get("q") or "").strip()
    if len(q) < 2:
        return _error_response("q requerido (min 2 caracteres)", status_code=400)

    muni = (request.path_params.get("muni") or "").strip().lower()
    try:
        result = await public_search(q, schema_name=schema_name, muni=muni)
        return _with_cache_headers(_success_response(result))
    except Exception:
        logger.exception("[PublicInfo] Error en api_public_search")
        return _error_response("Busqueda temporalmente no disponible", status_code=503)


async def api_public_registries(request: Request) -> JSONResponse:
    ip = get_public_client_ip(request)
    check_public_rate_limit(ip, PUBLIC_IP_LIST_LIMIT, window_seconds=60)

    schema_name, err = await _authenticate_public(request)
    if err:
        return err

    try:
        result = await list_registries_public(schema_name=schema_name)
        return _with_cache_headers(_success_response(result))
    except Exception:
        logger.exception("[PublicInfo] Error en api_public_registries")
        return _error_response("Error interno del servidor", status_code=500)


async def api_public_list_records(request: Request) -> JSONResponse:
    ip = get_public_client_ip(request)
    check_public_rate_limit(ip, PUBLIC_IP_LIST_LIMIT, window_seconds=60)

    schema_name, err = await _authenticate_public(request)
    if err:
        return err

    code = request.path_params.get("code", "")
    params = request.query_params
    try:
        page = int(params.get("page", 1))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = int(params.get("page_size", 20))
    except (ValueError, TypeError):
        page_size = 20

    try:
        families = await get_public_families(schema_name=schema_name, code=code)
        if not families:
            return _error_response("Familia no encontrada", status_code=404)

        result = await list_records_public(
            schema_name=schema_name,
            registry_code=code,
            search=params.get("search"),
            page=page,
            page_size=page_size,
        )
        return _with_cache_headers(_success_response(result))
    except Exception:
        logger.exception("[PublicInfo] Error en api_public_list_records")
        return _error_response("Error interno del servidor", status_code=500)


async def api_public_get_record(request: Request) -> JSONResponse:
    ip = get_public_client_ip(request)
    check_public_rate_limit(ip, PUBLIC_IP_LIST_LIMIT, window_seconds=60)

    schema_name, err = await _authenticate_public(request)
    if err:
        return err

    record_number = request.path_params.get("record_number", "")
    muni = (request.path_params.get("muni") or "").strip().lower()
    try:
        result = await get_record_public(schema_name=schema_name, record_number=record_number, muni=muni)
        if result is None:
            return _error_response("Legajo no encontrado", status_code=404)
        return _with_cache_headers(_success_response(result))
    except Exception:
        logger.exception("[PublicInfo] Error en api_public_get_record")
        return _error_response("Error interno del servidor", status_code=500)


async def api_public_get_document_content(request: Request) -> JSONResponse:
    ip = get_public_client_ip(request)
    check_public_rate_limit(ip, PUBLIC_IP_LIST_LIMIT, window_seconds=60)

    schema_name, err = await _authenticate_public(request)
    if err:
        return err

    document_id = request.path_params.get("document_id", "")
    try:
        uuid.UUID(document_id)
    except (ValueError, AttributeError, TypeError):
        return _error_response("Documento no encontrado", status_code=404)

    try:
        result = await get_document_content_public(schema_name=schema_name, document_id=document_id)
        if result is None:
            return _error_response("Documento no encontrado", status_code=404)
        return _with_cache_headers(_success_response(result))
    except Exception:
        logger.exception("[PublicInfo] Error en api_public_get_document_content")
        return _error_response("Error interno del servidor", status_code=500)
