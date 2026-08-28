from starlette.requests import Request

from api_gateway.tools import notes
from api_gateway.rest_common import (
    _error_response,
    _is_valid_uuid,
    rest_endpoint,
)
from shared.logging import get_logger

logger = get_logger(__name__)


@rest_endpoint(require_user=False, value_error_status=400)
async def api_get_notes(request: Request, ctx, user_id: str):
    params = request.query_params
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 20))
    unread_only = params.get("unread_only", "").lower() == "true"
    search = params.get("search")

    return await notes.get_notes(
        ctx=ctx,
        user_id=user_id,
        page=page,
        page_size=page_size,
        unread_only=unread_only,
        search=search
    )


@rest_endpoint(require_user=False, value_error_status=400)
async def api_get_sent_notes(request: Request, ctx, user_id: str):
    params = request.query_params
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 20))
    search = params.get("search")

    return await notes.get_sent_notes(
        ctx=ctx,
        user_id=user_id,
        page=page,
        page_size=page_size,
        search=search
    )


@rest_endpoint(require_user=False, value_error_status=400)
async def api_get_archived_notes(request: Request, ctx, user_id: str):
    params = request.query_params
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 20))
    search = params.get("search")

    return await notes.get_archived_notes(
        ctx=ctx,
        user_id=user_id,
        page=page,
        page_size=page_size,
        search=search
    )


@rest_endpoint(require_user=False, value_error_status=400)
async def api_archive_note(request: Request, ctx, user_id: str):
    note_id = request.path_params.get("note_id")

    if not note_id:
        return _error_response("note_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    archived = body.get("archived")
    sector_id = body.get("sector_id")

    if archived is None:
        return _error_response("archived es requerido", status_code=400)
    if not sector_id:
        return _error_response("sector_id es requerido", status_code=400)

    from services.notes import toggle_note_archive

    return await toggle_note_archive(
        document_id=note_id,
        sector_id=sector_id,
        archived=archived,
        schema_name=ctx.schema_name
    )


@rest_endpoint(require_user=False, value_error_status=400)
async def api_get_note_detail(request: Request, ctx, user_id: str):
    note_id = request.path_params.get("note_id")

    if not note_id:
        return _error_response("note_id es requerido", status_code=400)
    if not _is_valid_uuid(note_id):
        return _error_response("note_id inválido (se espera UUID)", status_code=400)

    return await notes.get_note_detail(
        ctx=ctx,
        note_id=note_id,
        user_id=user_id
    )
