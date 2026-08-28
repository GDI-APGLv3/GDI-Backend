from starlette.requests import Request
from starlette.responses import JSONResponse

from api_gateway.tools import cases
from shared.exceptions import (
    NotFoundError,
    DatabaseBusyError,
    AuthorizationError,
    ValidationError,
    ConflictError,
    BusinessLogicError,
    IsLastTaskError,
)
from api_gateway.rest_common import _error_response, logger, rest_endpoint


@rest_endpoint(require_user=False, value_error_status=400)
async def api_search_cases(request: Request, ctx, user_id: str):
    params = request.query_params

    return await cases.search_cases(
        ctx=ctx,
        user_id=user_id,
        page=int(params.get("page", 1)),
        page_size=int(params.get("page_size", 20)),
        search=params.get("search"),
        status=params.get("status"),
        date_filter=params.get("date_filter"),
        sector_filter=params.get("sector_filter")
    )


@rest_endpoint(require_user=True, value_error_status=400)
async def api_get_case(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    params = request.query_params
    include_documents = params.get("include_documents", "").lower() == "true"

    result = await cases.get_case(
        ctx=ctx,
        case_id=case_id,
        user_id=user_id,
        include_documents=include_documents
    )

    if not result:
        return _error_response("Expediente no encontrado o sin permisos", status_code=404)

    return result


@rest_endpoint(require_user=True, value_error_status=403, not_found_message="Expediente no encontrado")
async def api_get_case_history(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    return await cases.get_case_history(ctx=ctx, case_id=case_id, user_id=user_id)


@rest_endpoint(require_user=True, value_error_status=403)
async def api_get_case_documents(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    return await cases.get_case_documents(
        ctx=ctx,
        case_id=case_id,
        user_id=user_id
    )


@rest_endpoint(require_user=False)
async def api_get_case_permissions(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        return await cases.get_case_permissions(
            ctx=ctx,
            case_id=case_id,
            user_id=user_id
        )
    except Exception as e:
        if "no encontrado" in str(e).lower():
            return _error_response("Expediente no encontrado", status_code=404)
        logger.exception("[REST API] Error en get_case_permissions")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=True)
async def api_get_case_by_number(request: Request, ctx, user_id: str):
    case_number = request.path_params.get("case_number")
    if not case_number:
        return _error_response("case_number es requerido", status_code=400)

    try:
        result = await cases.get_case_by_number(
            ctx=ctx,
            case_number=case_number,
            user_id=user_id
        )

        if not result:
            return _error_response("Expediente no encontrado", status_code=404)

        return result

    except Exception as e:
        logger.exception("[REST API] Error en get_case_by_number")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False)
async def api_prepare_assignment(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        return await cases.prepare_assignment(
            ctx=ctx,
            case_id=case_id,
            user_id=user_id
        )
    except Exception as e:
        logger.exception("[REST API] Error en prepare_assignment")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False, value_error_status=400)
async def api_assign_case(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    target_sector_id = body.get("target_sector_id")
    reason = body.get("reason")

    if not target_sector_id:
        return _error_response("target_sector_id es requerido", status_code=400)
    if not reason:
        return _error_response("reason es requerido", status_code=400)

    return await cases.assign_case(
        ctx=ctx,
        case_id=case_id,
        target_sector_id=target_sector_id,
        reason=reason,
        user_id=user_id,
        assigned_user_id=body.get("assigned_user_id"),
        create_official_doc=body.get("create_official_doc", False)
    )


@rest_endpoint(require_user=False)
async def api_close_assignment(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    movement_id = body.get("movement_id") or body.get("assignment_id")
    reason = body.get("reason")

    if not movement_id:
        return _error_response("movement_id (o assignment_id) es requerido", status_code=400)
    if not reason:
        return _error_response("reason es requerido", status_code=400)

    try:
        return await cases.close_assignment(
            ctx=ctx,
            case_id=case_id,
            movement_id=movement_id,
            reason=reason,
            user_id=user_id
        )

    except AuthorizationError as e:
        return _error_response(str(e) or "Sin permisos para cerrar asignación", status_code=403)
    except NotFoundError as e:
        return _error_response(str(e) or "Movimiento no encontrado", status_code=404)
    except (BusinessLogicError, ValidationError, ConflictError) as e:
        return _error_response(str(e), status_code=409)
    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en close_assignment")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False)
async def api_get_case_responsibles(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    return await cases.get_case_responsibles_list(ctx=ctx, case_id=case_id, user_id=user_id)


@rest_endpoint(require_user=False)
async def api_add_case_responsible(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    responsible_user_id = body.get("user_id")
    responsible_type = body.get("type")
    sector_id = body.get("sector_id")
    reason = body.get("reason", "Asignación de responsable")

    if not responsible_user_id:
        return _error_response("user_id es requerido", status_code=400)
    if not responsible_type:
        return _error_response("type es requerido (ADMIN o ADDITIONAL)", status_code=400)
    if not sector_id:
        return _error_response("sector_id es requerido", status_code=400)

    try:
        return await cases.add_case_responsible(
            ctx=ctx,
            case_id=case_id,
            user_id=user_id,
            responsible_user_id=responsible_user_id,
            responsible_type=responsible_type,
            sector_id=sector_id,
            reason=reason,
        )
    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except AuthorizationError:
        return _error_response("Sin permisos para modificar este expediente", status_code=403)
    except Exception:
        logger.exception("[REST API] Error en add_case_responsible")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False)
async def api_remove_case_responsible(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    responsible_id = request.path_params.get("responsible_id")
    reason = request.query_params.get("reason", "Remoción de responsable")

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)
    if not responsible_id:
        return _error_response("responsible_id es requerido", status_code=400)

    try:
        from fastapi import HTTPException as FastAPIHTTPException
        return await cases.remove_case_responsible(
            ctx=ctx,
            case_id=case_id,
            responsible_id=responsible_id,
            user_id=user_id,
            reason=reason,
        )
    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except AuthorizationError:
        return _error_response("Sin permisos para modificar este expediente", status_code=403)
    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except FastAPIHTTPException as e:
        return _error_response(
            e.detail.get("message", "No encontrado") if isinstance(e.detail, dict) else str(e.detail),
            status_code=e.status_code
        )
    except Exception:
        logger.exception("[REST API] Error en remove_case_responsible")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False)
async def api_get_case_movements(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        from shared.utils import get_authenticated_user
        from services.case_service import CaseService

        db_user_id = await get_authenticated_user(user_id, schema_name=ctx.schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=ctx.schema_name):
            return _error_response("Expediente no encontrado", status_code=404)

        movements = await CaseService.get_case_movements(case_id, schema_name=ctx.schema_name)

        return {
            "success": True,
            "data": {
                "movements": movements,
                "total": len(movements),
            },
            "message": "Movimientos obtenidos exitosamente",
        }

    except ValidationError as e:
        return _error_response(str(e), status_code=401)
    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError:
        return _error_response("Expediente no encontrado", status_code=404)
    except DatabaseBusyError as e:
        logger.warning(f"[REST API] BD saturada en {'get_case_movements'}: {e}")
        return _error_response("Servidor ocupado, reintente en unos segundos",
                               status_code=503, retry_after=5)
    except Exception:
        logger.exception("[REST API] Error en get_case_movements")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False)
async def api_create_case(request: Request, ctx, user_id: str):
    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    case_template_id = body.get("case_template_id")
    reference = body.get("reference")
    owner_sector_id = body.get("owner_sector_id")

    if not case_template_id:
        return _error_response("case_template_id es requerido", status_code=400)
    if not reference:
        return _error_response("reference es requerido", status_code=400)

    try:
        result = await cases.create_case(
            ctx=ctx,
            case_template_id=case_template_id,
            reference=reference,
            user_id=user_id,
            owner_sector_id=owner_sector_id
        )
        return JSONResponse(result, status_code=201)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en create_case")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False, value_error_status=400)
async def api_transfer_case(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    target_sector_id = body.get("target_sector_id")
    reason = body.get("reason")

    if not target_sector_id:
        return _error_response("target_sector_id es requerido", status_code=400)
    if not reason:
        return _error_response("reason es requerido", status_code=400)

    return await cases.transfer_case(
        ctx=ctx,
        case_id=case_id,
        target_sector_id=target_sector_id,
        reason=reason,
        user_id=user_id,
        assigned_user_id=body.get("assigned_user_id"),
        create_official_doc=body.get("create_official_doc", False)
    )


@rest_endpoint(require_user=False)
async def api_link_document(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    official_document_id = body.get("official_document_id")

    if not official_document_id:
        return _error_response("official_document_id es requerido", status_code=400)

    try:
        return await cases.link_document_to_case(
            ctx=ctx,
            case_id=case_id,
            official_document_id=official_document_id,
            user_id=user_id
        )

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        error_msg = str(e).lower()
        if "permiso" in error_msg or "authorization" in error_msg:
            return _error_response("Sin permisos para vincular documento", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Expediente o documento no encontrado", status_code=404)
        logger.exception("[REST API] Error en link_document")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False)
async def api_propose_document(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    document_draft_id = body.get("document_draft_id")

    if not document_draft_id:
        return _error_response("document_draft_id es requerido", status_code=400)

    try:
        return await cases.propose_document(
            ctx=ctx,
            case_id=case_id,
            document_draft_id=document_draft_id,
            user_id=user_id
        )

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en propose_document")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False)
async def api_prepare_transfer(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        return await cases.prepare_transfer(
            ctx=ctx,
            case_id=case_id,
            user_id=user_id
        )

    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except Exception as e:
        error_msg = str(e).lower()
        if "permiso" in error_msg or "authorization" in error_msg:
            return _error_response("Sin permisos para transferir este expediente", status_code=403)
        logger.exception("[REST API] Error en prepare_transfer")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False, value_error_status=400, not_found_message="Propuesta no encontrada")
async def api_accept_proposal(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    proposed_id = body.get("proposed_id")

    if not proposed_id:
        return _error_response("proposed_id es requerido", status_code=400)

    return await cases.accept_proposal(
        ctx=ctx,
        case_id=case_id,
        proposed_id=proposed_id,
        user_id=user_id
    )


@rest_endpoint(require_user=False)
async def api_reject_proposal(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    proposed_id = body.get("proposed_id")

    if not proposed_id:
        return _error_response("proposed_id es requerido", status_code=400)

    try:
        return await cases.reject_proposal(
            ctx=ctx,
            case_id=case_id,
            proposed_id=proposed_id,
            user_id=user_id
        )

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        error_msg = str(e).lower()
        if "permiso" in error_msg or "authorization" in error_msg:
            return _error_response("Sin permisos para rechazar propuesta", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Propuesta no encontrada", status_code=404)
        logger.exception("[REST API] Error en reject_proposal")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False, value_error_status=400)
async def api_get_assignments(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    return await cases.get_assignments_with_tasks_wrapper(
        ctx=ctx,
        case_id=case_id,
        user_id=user_id,
    )


@rest_endpoint(require_user=False, value_error_status=400)
async def api_get_assignable_users(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    q = request.query_params.get("q", "")
    sector_id = request.query_params.get("sector_id") or None

    return await cases.get_assignable_users_wrapper(
        ctx=ctx,
        case_id=case_id,
        user_id=user_id,
        q=q,
        sector_id=sector_id,
    )


@rest_endpoint(require_user=False, value_error_status=400)
async def api_get_available_responsibles_rest(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    responsible_type = request.query_params.get("type", "")
    if not responsible_type:
        return _error_response("type es requerido (ADMIN o ADDITIONAL)", status_code=400)

    sector_id = request.query_params.get("sector_id") or None

    return await cases.get_available_responsibles_wrapper(
        ctx=ctx,
        case_id=case_id,
        user_id=user_id,
        responsible_type=responsible_type,
        sector_id=sector_id,
    )


@rest_endpoint(require_user=False, value_error_status=400)
async def api_update_task(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    task_id = request.path_params.get("task_id")

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)
    if not task_id:
        return _error_response("task_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    assigned_user_id = body.get("assigned_user_id")

    return await cases.update_task_wrapper(
        ctx=ctx,
        case_id=case_id,
        task_id=task_id,
        user_id=user_id,
        assigned_user_id=assigned_user_id,
    )


@rest_endpoint(require_user=False)
async def api_close_task(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")
    task_id = request.path_params.get("task_id")

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)
    if not task_id:
        return _error_response("task_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        body = {}

    closing_reason = body.get("closing_reason")
    create_official_doc = bool(body.get("create_official_doc", False))

    try:
        return await cases.close_task_wrapper(
            ctx=ctx,
            case_id=case_id,
            task_id=task_id,
            user_id=user_id,
            closing_reason=closing_reason,
            create_official_doc=create_official_doc,
        )

    except IsLastTaskError:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "is_last_task": True,
                "message": (
                    "Es la última tarea abierta del sector. "
                    "Incluí closing_reason (min 5 caracteres) para confirmar el cierre del sector."
                ),
            },
        )
    except (ValueError, ValidationError) as e:
        return _error_response(str(e), status_code=400)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except Exception as e:
        logger.exception("[REST API] Error en close_task")
        return _error_response("Error interno del servidor", status_code=500)
