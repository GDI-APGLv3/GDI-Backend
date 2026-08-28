from starlette.requests import Request

from api_gateway.rest_common import _error_response, rest_endpoint
from api_gateway.tools import records
from shared.logging import get_logger

logger = get_logger(__name__)


@rest_endpoint(require_user=False)
async def api_search_records(request: Request, ctx, user_id: str) -> dict:
    params = request.query_params
    return await records.search_records(
        ctx=ctx,
        family_code=params.get("family_code"),
        search=params.get("search"),
        state=params.get("state"),
        page=int(params.get("page", 1)),
        page_size=int(params.get("page_size", 20)),
    )


@rest_endpoint(require_user=False)
async def api_get_record(request: Request, ctx, user_id: str) -> dict:
    record_id = request.path_params.get("record_id", "")
    return await records.get_record_detail(
        ctx=ctx,
        record_id=record_id,
    )


@rest_endpoint(require_user=False)
async def api_create_record(request: Request, ctx, user_id: str):
    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    registry_code = body.get("registry_code")
    if not registry_code:
        return _error_response("registry_code es requerido", status_code=400)

    display_name = body.get("display_name")
    if not display_name:
        return _error_response("display_name es requerido", status_code=400)

    from services.rlm.records import create_record
    result = await create_record(
        registry_code=registry_code,
        data=body.get("data") or {},
        display_name=display_name,
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
    )

    try:
        from services.shared.resume_trigger import enqueue_record_resume_fire_and_forget
        enqueue_record_resume_fire_and_forget(result['id'], ctx.schema_name)
    except Exception:
        pass

    return result


@rest_endpoint(require_user=False)
async def api_get_registry_families(request: Request, ctx, user_id: str) -> dict:
    return await records.get_registry_families(ctx=ctx)


@rest_endpoint(require_user=False)
async def api_update_record(request: Request, ctx, user_id: str):
    record_id = request.path_params.get("record_id", "")

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    state = body.get("state")
    display_name = body.get("display_name")
    if not state and not display_name:
        return _error_response("Debe enviar al menos 'state' o 'display_name'", status_code=400)

    from services.rlm.records import update_record
    return await update_record(
        record_id=record_id,
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
        new_state=state,
        new_display_name=display_name,
        reason=body.get("reason"),
    )


@rest_endpoint(require_user=False)
async def api_update_record_field(request: Request, ctx, user_id: str):
    record_id = request.path_params.get("record_id", "")
    field_name = request.path_params.get("field_name", "")

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    from services.rlm.fields import update_field
    return await update_field(
        record_id=record_id,
        field_name=field_name,
        user_id=ctx.user_id,
        value=body.get("value"),
        expiration_date=body.get("expiration_date"),
        document_id=body.get("document_id"),
        notes=body.get("notes"),
        document_reference=body.get("document_reference"),
        document_resume=body.get("document_resume"),
        schema_name=ctx.schema_name,
    )


@rest_endpoint(require_user=False)
async def api_verify_record_field(request: Request, ctx, user_id: str):
    record_id = request.path_params.get("record_id", "")
    field_name = request.path_params.get("field_name", "")

    try:
        body = await request.json()
    except Exception:
        body = {}

    document_id = body.get("document_id")
    if not document_id:
        return _error_response("document_id es obligatorio para verificar un campo", status_code=400)

    from services.rlm.fields import verify_field
    return await verify_field(
        record_id=record_id,
        field_name=field_name,
        user_id=ctx.user_id,
        document_id=document_id,
        notes=body.get("notes"),
        schema_name=ctx.schema_name,
    )


@rest_endpoint(require_user=False)
async def api_get_record_history(request: Request, ctx, user_id: str) -> dict:
    record_id = request.path_params.get("record_id", "")
    params = request.query_params

    from services.rlm.history import get_history
    return await get_history(
        record_id=record_id,
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
        page=int(params.get("page", 1)),
        page_size=int(params.get("page_size", 20)),
    )


@rest_endpoint(require_user=False)
async def api_generate_record_report(request: Request, ctx, user_id: str) -> dict:
    record_id = request.path_params.get("record_id", "")

    from services.rlm.report import generate_ifrlm
    return await generate_ifrlm(
        record_id=record_id,
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
        is_initial=False,
    )


@rest_endpoint(require_user=False)
async def api_get_record_relations(request: Request, ctx, user_id: str) -> dict:
    record_id = request.path_params.get("record_id", "")
    params = request.query_params

    from services.rlm.relations import get_relations
    return await get_relations(
        record_id,
        ctx.user_id,
        schema_name=ctx.schema_name,
        page=int(params.get("page", 1)),
        page_size=int(params.get("page_size", 20)),
    )


@rest_endpoint(require_user=False)
async def api_create_record_relation(request: Request, ctx, user_id: str):
    record_id = request.path_params.get("record_id", "")

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    target_record_id = body.get("target_record_id")
    relation_type = body.get("relation_type")
    if not target_record_id or not relation_type:
        return _error_response("target_record_id y relation_type son requeridos", status_code=400)

    from services.rlm.relations import create_relation
    return await create_relation(
        record_id=record_id,
        target_record_id=target_record_id,
        relation_type=relation_type,
        user_id=ctx.user_id,
        notes=body.get("notes"),
        schema_name=ctx.schema_name,
    )


@rest_endpoint(require_user=False)
async def api_delete_record_relation(request: Request, ctx, user_id: str) -> dict:
    record_id = request.path_params.get("record_id", "")
    relation_id = request.path_params.get("relation_id", "")

    from services.rlm.relations import delete_relation
    return await delete_relation(
        record_id=record_id,
        relation_id=relation_id,
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
    )


@rest_endpoint(require_user=False)
async def api_get_record_cases(request: Request, ctx, user_id: str) -> dict:
    record_id = request.path_params.get("record_id", "")
    params = request.query_params

    from services.rlm.links import get_linked_cases
    return await get_linked_cases(
        record_id,
        ctx.user_id,
        schema_name=ctx.schema_name,
        page=int(params.get("page", 1)),
        page_size=int(params.get("page_size", 20)),
    )


@rest_endpoint(require_user=False)
async def api_link_record_case(request: Request, ctx, user_id: str):
    record_id = request.path_params.get("record_id", "")

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    case_id = body.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    from services.rlm.links import link_case
    return await link_case(
        record_id=record_id,
        case_id=case_id,
        user_id=ctx.user_id,
        notes=body.get("notes"),
        schema_name=ctx.schema_name,
    )


@rest_endpoint(require_user=False)
async def api_unlink_record_case(request: Request, ctx, user_id: str) -> dict:
    record_id = request.path_params.get("record_id", "")
    link_id = request.path_params.get("link_id", "")

    from services.rlm.links import unlink_case
    return await unlink_case(
        record_id=record_id,
        link_id=link_id,
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
    )


@rest_endpoint(require_user=False)
async def api_get_record_documents(request: Request, ctx, user_id: str) -> dict:
    record_id = request.path_params.get("record_id", "")
    params = request.query_params

    from services.rlm.links import get_linked_documents
    return await get_linked_documents(
        record_id,
        ctx.user_id,
        schema_name=ctx.schema_name,
        page=int(params.get("page", 1)),
        page_size=int(params.get("page_size", 20)),
    )


@rest_endpoint(require_user=False)
async def api_link_record_document(request: Request, ctx, user_id: str):
    record_id = request.path_params.get("record_id", "")

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    document_id = body.get("document_id")
    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    from services.rlm.links import link_document
    return await link_document(
        record_id=record_id,
        document_id=document_id,
        user_id=ctx.user_id,
        field_name=body.get("field_name"),
        notes=body.get("notes"),
        schema_name=ctx.schema_name,
    )


@rest_endpoint(require_user=False)
async def api_unlink_record_document(request: Request, ctx, user_id: str) -> dict:
    record_id = request.path_params.get("record_id", "")
    link_id = request.path_params.get("link_id", "")

    from services.rlm.links import unlink_document
    return await unlink_document(
        record_id=record_id,
        link_id=link_id,
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
    )
