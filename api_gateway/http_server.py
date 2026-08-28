import os
import sys
import json
import logging
from pathlib import Path
from shared.logging import get_logger
from shared.version import VERSION, GIT_SHA
import asyncio
import uuid
from typing import Any, Dict

backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(backend_root, ".env"))

from contextlib import asynccontextmanager
import time as _time

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from api_gateway.rate_limiter import rate_limiter, get_client_ip, RateLimitExceeded
from api_gateway.gateway_audit import log_mcp_tool_call
from api_gateway.gateway_middleware import GatewayMiddleware

from api_gateway.auth_mcp import (
    validate_mcp_jwt,
    extract_email_from_token,
    find_user_all_tenants,
    MultiTenantSelectionRequired
)
from api_gateway.tools import cases, documents, system, notes, records, memos, search
from api_gateway.tool_schemas import TOOL_SCHEMAS
from shared.exceptions import ValidationError, GDIBaseException

from api_gateway.rest_api import (
    api_search_cases, api_get_case, api_get_case_history,
    api_get_case_documents, api_get_case_permissions,
    api_get_case_by_number,
    api_prepare_assignment, api_assign_case, api_close_assignment,
    api_create_case, api_transfer_case, api_link_document,
    api_propose_document, api_prepare_transfer,
    api_accept_proposal, api_reject_proposal,
    api_search_documents, api_get_document, api_get_document_content,
    api_get_pending_signatures, api_get_document_url,
    api_create_document, api_save_document, api_start_signing,
    api_sign_document, api_reject_document, api_delete_document,
    api_async_poll,
    api_get_document_types, api_get_user_info, api_get_document_states,
    api_get_sectors, api_get_case_templates, api_search_users,
    api_get_notes,
    api_get_sector_users,
    api_search_document_by_number,
    api_get_signature_details,
    api_import_document,
    api_replace_imported_pdf,
    api_check_signer_permissions,
    api_list_all_users,
    api_get_sent_notes,
    api_get_archived_notes,
    api_archive_note,
    api_get_note_detail,
    api_get_memos,
    api_get_sent_memos,
    api_get_archived_memos,
    api_get_memo_detail,
    api_sync_schema,
    api_sync_data,
    api_sync_documents,
    api_semantic_search,
    api_search_records,
    api_get_record,
    api_create_record,
    api_get_registry_families,
    api_update_record,
    api_update_record_field,
    api_verify_record_field,
    api_get_record_history,
    api_generate_record_report,
    api_get_record_relations,
    api_create_record_relation,
    api_delete_record_relation,
    api_get_record_cases,
    api_link_record_case,
    api_unlink_record_case,
    api_get_record_documents,
    api_link_record_document,
    api_unlink_record_document,
    api_get_case_responsibles,
    api_add_case_responsible,
    api_remove_case_responsible,
    api_get_assignments,
    api_get_assignable_users,
    api_get_available_responsibles_rest,
    api_update_task,
    api_close_task,
    api_subsanar_document,
    api_get_case_movements,
)

from api_gateway.rest_api_public import (
    api_public_search,
    api_public_registries,
    api_public_list_records,
    api_public_get_record,
    api_public_get_document_content,
)

from api_gateway.rest_api_tad import (
    api_tad_create_citizen,
    api_tad_get_citizen,
    api_tad_patch_citizen,
    api_tad_get_document_types,
    api_tad_get_document_type_fields,
    api_tad_create_document,
    api_tad_get_document,
    api_tad_get_case_templates,
    api_tad_create_case,
    api_tad_get_cases,
    api_tad_get_case_detail,
    api_tad_propose_document,
    api_tad_webhook_test,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr
)
logger = get_logger(__name__)

MCP_PROTOCOL_VERSION = "2025-03-26"

MCP_IP_LIMIT = 60
MCP_USER_LIMIT = 30


@asynccontextmanager
async def lifespan(app):
    from database import init_pool, close_pool

    await init_pool()
    logger.info("Gateway: asyncpg pool inicializado")

    async def _cleanup_loop():
        while True:
            await asyncio.sleep(300)
            rate_limiter.cleanup()

    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()
    await close_pool()
    logger.info("Gateway: asyncpg pool cerrado")

sessions: Dict[str, Dict[str, Any]] = {}


def create_jsonrpc_response(id: Any, result: Any) -> Dict:
    return {
        "jsonrpc": "2.0",
        "id": id,
        "result": result
    }


def create_jsonrpc_error(id: Any, code: int, message: str, data: Any = None) -> Dict:
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": id,
        "error": error
    }


async def handle_initialize(request_id: Any, params: Dict) -> Dict:
    return create_jsonrpc_response(request_id, {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {
            "tools": {}
        },
        "serverInfo": {
            "name": "gdi-backend",
            "version": "1.0.0"
        }
    })


async def handle_list_tools(request_id: Any) -> Dict:
    return create_jsonrpc_response(request_id, {"tools": TOOL_SCHEMAS})


async def handle_list_my_tenants(request_id: Any, authorization_header: str) -> Dict:
    if not authorization_header or not authorization_header.startswith("Bearer "):
        return create_jsonrpc_error(request_id, -32001, "Autenticación requerida. Usa OAuth para conectar.")

    token = authorization_header[7:]
    email = await extract_email_from_token(token)

    if not email:
        return create_jsonrpc_error(request_id, -32001, "No se pudo obtener email del token")

    tenants = await find_user_all_tenants(email)

    result = {
        "tenants": [
            {"tenant_id": str(t["municipality_id"]), "name": t["municipality_name"]}
            for t in tenants
        ],
        "total": len(tenants),
        "hint": "Usa tenant_id en tu próxima llamada para trabajar en esa municipalidad"
    }

    return create_jsonrpc_response(request_id, {
        "content": [{
            "type": "text",
            "text": json.dumps(result, ensure_ascii=False, indent=2)
        }],
        "isError": False
    })


async def _tool_search_cases(ctx, arguments):
    return await cases.search_cases(
        ctx=ctx,
        page=int(arguments.get("page", 1)),
        page_size=int(arguments.get("page_size", 20)),
        search=arguments.get("search"),
        status=arguments.get("status"),
        date_filter=arguments.get("date_filter"),
        sector_filter=arguments.get("sector_filter"),
        user_id=arguments["user_id"]
    )


async def _tool_get_case(ctx, arguments):
    return await cases.get_case(
        ctx=ctx,
        case_id=arguments["case_id"],
        user_id=arguments["user_id"],
        include_documents=arguments.get("include_documents", False),
        include_movements=arguments.get("include_movements", False),
    )


async def _tool_get_case_history(ctx, arguments):
    return await cases.get_case_history(
        ctx=ctx,
        case_id=arguments["case_id"],
        user_id=arguments["user_id"]
    )


async def _tool_get_case_documents(ctx, arguments):
    return await cases.get_case_documents(
        ctx=ctx,
        case_id=arguments["case_id"],
        user_id=arguments["user_id"]
    )


async def _tool_get_case_permissions(ctx, arguments):
    return await cases.get_case_permissions(
        ctx=ctx,
        case_id=arguments["case_id"],
        user_id=arguments["user_id"]
    )


async def _tool_search_documents(ctx, arguments):
    _min_signers_raw = arguments.get("min_signers")
    return await documents.search_documents(
        ctx=ctx,
        user_id=arguments["user_id"],
        page=int(arguments.get("page", 1)),
        page_size=int(arguments.get("page_size", 20)),
        search=arguments.get("search"),
        status=arguments.get("status"),
        document_type=arguments.get("document_type"),
        case_id=arguments.get("case_id"),
        min_signers=int(_min_signers_raw) if _min_signers_raw is not None else None,
    )


async def _tool_get_document(ctx, arguments):
    return await documents.get_document(
        ctx=ctx,
        document_id=arguments["document_id"],
        user_id=arguments["user_id"]
    )


async def _tool_get_document_types(ctx, arguments):
    return await system.get_document_types(ctx=ctx)


async def _tool_get_user_info(ctx, arguments):
    return await system.get_user_info(
        ctx=ctx,
        user_id=arguments["user_id"]
    )


async def _tool_get_pending_signatures(ctx, arguments):
    return await documents.get_pending_signatures(
        ctx=ctx,
        user_id=arguments["user_id"]
    )


async def _tool_get_document_content(ctx, arguments):
    return await documents.get_document_content(
        ctx=ctx,
        document_id=arguments["document_id"],
        user_id=arguments["user_id"]
    )


async def _tool_create_document(ctx, arguments):
    return await documents.create_document(
        ctx=ctx,
        document_type_acronym=arguments["document_type_acronym"],
        reference=arguments["reference"],
        user_id=arguments["user_id"],
        case_id=arguments.get("case_id"),
        recipients=arguments.get("recipients")
    )


async def _tool_save_document(ctx, arguments):
    return await documents.save_document(
        ctx=ctx,
        document_id=arguments["document_id"],
        user_id=arguments["user_id"],
        content=arguments.get("content"),
        reference=arguments.get("reference"),
        signers=arguments.get("signers"),
        recipients=arguments.get("recipients"),
        proposed_case_ids=arguments.get("proposed_case_ids")
    )


async def _tool_start_signing(ctx, arguments):
    return await documents.start_signing(
        ctx=ctx,
        document_id=arguments["document_id"],
        user_id=arguments["user_id"]
    )


async def _tool_get_case_by_number(ctx, arguments):
    return await cases.get_case_by_number(
        ctx=ctx,
        case_number=arguments["case_number"],
        user_id=arguments["user_id"]
    )


async def _tool_prepare_assignment(ctx, arguments):
    return await cases.prepare_assignment(
        ctx=ctx,
        case_id=arguments["case_id"],
        user_id=arguments["user_id"]
    )


async def _tool_assign_case(ctx, arguments):
    return await cases.assign_case(
        ctx=ctx,
        case_id=arguments["case_id"],
        target_sector_id=arguments["target_sector_id"],
        reason=arguments["reason"],
        user_id=arguments["user_id"],
        assigned_user_id=arguments.get("assigned_user_id"),
        create_official_doc=arguments.get("create_official_doc", False)
    )


async def _tool_get_document_states(ctx, arguments):
    return await system.get_document_states(ctx=ctx)


async def _tool_reject_document(ctx, arguments):
    return await documents.reject_document(
        ctx=ctx,
        document_id=arguments["document_id"],
        user_id=arguments["user_id"],
        reason=arguments["reason"]
    )


async def _tool_propose_document(ctx, arguments):
    return await cases.propose_document(
        ctx=ctx,
        case_id=arguments["case_id"],
        document_draft_id=arguments["document_draft_id"],
        user_id=arguments["user_id"]
    )


async def _tool_reject_proposal(ctx, arguments):
    return await cases.reject_proposal(
        ctx=ctx,
        case_id=arguments["case_id"],
        proposed_id=arguments["proposed_id"],
        user_id=arguments["user_id"]
    )


async def _tool_get_case_templates(ctx, arguments):
    return await system.get_case_templates(
        ctx=ctx,
        user_id=arguments["user_id"]
    )


async def _tool_get_sectors(ctx, arguments):
    return await system.get_sectors(ctx=ctx)


async def _tool_search_users(ctx, arguments):
    return await system.search_users(
        ctx=ctx,
        search=arguments["search"],
        limit=int(arguments.get("limit", 10))
    )


async def _tool_get_notes(ctx, arguments):
    return await notes.get_notes(
        ctx=ctx,
        user_id=arguments["user_id"],
        page=int(arguments.get("page", 1)),
        page_size=int(arguments.get("page_size", 20)),
        unread_only=arguments.get("unread_only", False),
        search=arguments.get("search")
    )


async def _tool_search_document_by_number(ctx, arguments):
    return await documents.search_document_by_number(
        ctx=ctx,
        doc_number=arguments.get("doc_number", ""),
        user_id=arguments["user_id"]
    )


async def _tool_get_signature_details(ctx, arguments):
    return await documents.get_signature_details(
        ctx=ctx,
        document_id=arguments.get("document_id", ""),
        user_id=arguments["user_id"]
    )


async def _tool_get_sent_notes(ctx, arguments):
    return await notes.get_sent_notes(
        ctx=ctx,
        user_id=arguments["user_id"],
        page=int(arguments.get("page", 1)),
        page_size=int(arguments.get("page_size", 20)),
        search=arguments.get("search")
    )


async def _tool_get_archived_notes(ctx, arguments):
    return await notes.get_archived_notes(
        ctx=ctx,
        user_id=arguments["user_id"],
        page=int(arguments.get("page", 1)),
        page_size=int(arguments.get("page_size", 20)),
        search=arguments.get("search")
    )


async def _tool_get_note_detail(ctx, arguments):
    return await notes.get_note_detail(
        ctx=ctx,
        note_id=arguments.get("note_id", ""),
        user_id=arguments["user_id"]
    )


async def _tool_get_memos(ctx, arguments):
    return await memos.get_memos(
        ctx=ctx,
        user_id=arguments["user_id"],
        page=int(arguments.get("page", 1)),
        page_size=int(arguments.get("page_size", 20)),
        search=arguments.get("search")
    )


async def _tool_get_sent_memos(ctx, arguments):
    return await memos.get_sent_memos_tool(
        ctx=ctx,
        user_id=arguments["user_id"],
        page=int(arguments.get("page", 1)),
        page_size=int(arguments.get("page_size", 20)),
        search=arguments.get("search")
    )


async def _tool_get_archived_memos(ctx, arguments):
    return await memos.get_archived_memos_tool(
        ctx=ctx,
        user_id=arguments["user_id"],
        page=int(arguments.get("page", 1)),
        page_size=int(arguments.get("page_size", 20)),
        search=arguments.get("search")
    )


async def _tool_get_memo_detail(ctx, arguments):
    return await memos.get_memo_detail(
        ctx=ctx,
        memo_id=arguments.get("memo_id", ""),
        user_id=arguments["user_id"]
    )


async def _tool_search_records(ctx, arguments):
    return await records.search_records(
        ctx=ctx,
        family_code=arguments.get("family_code"),
        search=arguments.get("search"),
        state=arguments.get("state"),
        page=int(arguments.get("page", 1)),
        page_size=int(arguments.get("page_size", 20)),
    )


async def _tool_get_record(ctx, arguments):
    return await records.get_record_detail(
        ctx=ctx,
        record_id=arguments.get("record_id", ""),
    )


async def _tool_get_registry_families(ctx, arguments):
    return await records.get_registry_families(ctx=ctx)


async def _tool_semantic_search(ctx, arguments):
    return await search.semantic_search_tool(
        ctx=ctx,
        query=arguments["query"],
        limit=int(arguments.get("limit", 20)),
        source="mcp",
    )


async def _tool_get_case_responsibles(ctx, arguments):
    return await cases.get_case_responsibles_list(
        ctx=ctx,
        case_id=arguments["case_id"],
        user_id=arguments["user_id"],
    )


async def _tool_add_case_responsible(ctx, arguments):
    return await cases.add_case_responsible(
        ctx=ctx,
        case_id=arguments["case_id"],
        user_id=arguments["user_id"],
        responsible_user_id=arguments["responsible_user_id"],
        responsible_type=arguments["responsible_type"],
        sector_id=arguments["sector_id"],
        reason=arguments.get("reason", "Asignación de responsable"),
    )


async def _tool_remove_case_responsible(ctx, arguments):
    return await cases.remove_case_responsible(
        ctx=ctx,
        case_id=arguments["case_id"],
        responsible_id=arguments["responsible_id"],
        user_id=arguments["user_id"],
        reason=arguments.get("reason", "Remoción de responsable"),
    )


TOOL_HANDLERS = {
    "search_cases": _tool_search_cases,
    "get_case": _tool_get_case,
    "get_case_history": _tool_get_case_history,
    "get_case_documents": _tool_get_case_documents,
    "get_case_permissions": _tool_get_case_permissions,
    "search_documents": _tool_search_documents,
    "get_document": _tool_get_document,
    "get_document_types": _tool_get_document_types,
    "get_user_info": _tool_get_user_info,
    "get_pending_signatures": _tool_get_pending_signatures,
    "get_document_content": _tool_get_document_content,
    "create_document": _tool_create_document,
    "save_document": _tool_save_document,
    "start_signing": _tool_start_signing,
    "get_case_by_number": _tool_get_case_by_number,
    "prepare_assignment": _tool_prepare_assignment,
    "assign_case": _tool_assign_case,
    "get_document_states": _tool_get_document_states,
    "reject_document": _tool_reject_document,
    "propose_document": _tool_propose_document,
    "reject_proposal": _tool_reject_proposal,
    "get_case_templates": _tool_get_case_templates,
    "get_sectors": _tool_get_sectors,
    "search_users": _tool_search_users,
    "get_notes": _tool_get_notes,
    "search_document_by_number": _tool_search_document_by_number,
    "get_signature_details": _tool_get_signature_details,
    "get_sent_notes": _tool_get_sent_notes,
    "get_archived_notes": _tool_get_archived_notes,
    "get_note_detail": _tool_get_note_detail,
    "get_memos": _tool_get_memos,
    "get_sent_memos": _tool_get_sent_memos,
    "get_archived_memos": _tool_get_archived_memos,
    "get_memo_detail": _tool_get_memo_detail,
    "search_records": _tool_search_records,
    "get_record": _tool_get_record,
    "get_registry_families": _tool_get_registry_families,
    "semantic_search": _tool_semantic_search,
    "get_case_responsibles": _tool_get_case_responsibles,
    "add_case_responsible": _tool_add_case_responsible,
    "remove_case_responsible": _tool_remove_case_responsible,
}


async def handle_call_tool(request_id: Any, params: Dict, authorization_header: str = None, correlation_id: str = None) -> Dict:
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    logger.info(f"Tool llamado: {tool_name}")

    if tool_name == "get_agent_guide":
        from pathlib import Path
        guide_path = Path(__file__).parent / "GUIA_AGENTE_IA.md"
        if guide_path.exists():
            guide_content = guide_path.read_text(encoding="utf-8")
            result = {
                "guide": guide_content,
                "version": "3.2",
                "tools_count": 43,
                "last_updated": "2026-07-15"
            }
        else:
            result = {"error": "Guía no encontrada", "path": str(guide_path)}

        return create_jsonrpc_response(request_id, {
            "content": [{
                "type": "text",
                "text": json.dumps(result, indent=2, default=str)
            }],
            "isError": False
        })

    if tool_name == "list_my_tenants":
        return await handle_list_my_tenants(request_id, authorization_header)

    tenant_id = arguments.get("tenant_id")

    try:
        ctx = None
        jwt_user_id = None

        if authorization_header and authorization_header.startswith("Bearer "):
            try:
                ctx, jwt_user_id = await validate_mcp_jwt(authorization_header, tenant_id=tenant_id)
                logger.info(f"[Auth0] Autenticación exitosa: user_id={jwt_user_id[:8]}..., schema={ctx.schema_name}")
            except MultiTenantSelectionRequired as e:
                logger.info(f"[Auth0] Usuario multi-tenant sin selección: {len(e.tenants)} tenants")
                return create_jsonrpc_response(request_id, {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "error": "multi_tenant_selection_required",
                            "message": f"Tienes acceso a {len(e.tenants)} municipalidades. Especifica tenant_id en tu próxima llamada.",
                            "available_tenants": [
                                {
                                    "tenant_id": str(t["municipality_id"]),
                                    "name": t["municipality_name"],
                                }
                                for t in e.tenants
                            ],
                            "example": f'Usa tenant_id="{e.tenants[0]["municipality_id"]}" para {e.tenants[0]["municipality_name"]}',
                            "hint": "Puedes usar la tool list_my_tenants para ver tus municipalidades disponibles"
                        }, ensure_ascii=False, indent=2)
                    }],
                    "isError": True
                })
            except ValueError as e:
                logger.warning(f"[Auth0] Falló autenticación: {e}")
                ctx = None

        if ctx is None:
            return create_jsonrpc_error(
                request_id,
                -32001,
                "Autenticación OAuth requerida. Usa Authorization: Bearer <jwt>"
            )

        if jwt_user_id:
            arguments["user_id"] = jwt_user_id
            arguments["municipality_id"] = ctx.municipality_id
            ctx.user_id = jwt_user_id
            logger.info(f"[Auth0] user_id inyectado desde JWT: {jwt_user_id[:8]}...")

            try:
                from shared.utils import get_authenticated_user
                await get_authenticated_user(jwt_user_id, schema_name=ctx.schema_name)
            except ValidationError as e:
                logger.warning(f"[Auth0] user_id {jwt_user_id[:8]}... inválido o inactivo: {e}")
                return create_jsonrpc_error(
                    request_id,
                    -32001,
                    "Usuario inválido o inactivo."
                )
            except Exception as e:
                logger.error(f"[Auth0] Error de infraestructura validando usuario {jwt_user_id[:8]}...: {e}")
                return create_jsonrpc_error(
                    request_id,
                    -32603,
                    "Error interno validando usuario. Intente nuevamente."
                )
        else:
            logger.warning("[Auth0] JWT valido pero sin user_id extraible")
            return create_jsonrpc_error(request_id, -32001, "Token sin user_id válido.")

        try:
            rate_limiter.check(f"mcp_user:{jwt_user_id}:{ctx.schema_name}", MCP_USER_LIMIT)
        except RateLimitExceeded as e:
            log_mcp_tool_call(
                cid=correlation_id or "", user_id=jwt_user_id, schema=ctx.schema_name,
                tool=tool_name, status="rate_limited", duration_ms=0,
            )
            return create_jsonrpc_error(request_id, -32029, f"Rate limit exceeded. Retry after {e.retry_after}s")

        _tool_start = _time.time()
        result = None

        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            _tool_ms = int((_time.time() - _tool_start) * 1000)
            log_mcp_tool_call(cid=correlation_id or "", user_id=jwt_user_id, schema=ctx.schema_name,
                              tool=tool_name, status="unknown_tool", duration_ms=_tool_ms)
            return create_jsonrpc_error(request_id, -32601, f"Tool desconocido: {tool_name}")

        result = await handler(ctx, arguments)

        _tool_ms = int((_time.time() - _tool_start) * 1000)
        log_mcp_tool_call(cid=correlation_id or "", user_id=jwt_user_id, schema=ctx.schema_name,
                          tool=tool_name, status="ok", duration_ms=_tool_ms)
        return create_jsonrpc_response(request_id, {
            "content": [{
                "type": "text",
                "text": json.dumps(result, indent=2, default=str)
            }],
            "isError": False
        })

    except ValueError as e:
        _tool_ms = int((_time.time() - _tool_start) * 1000) if '_tool_start' in locals() else 0
        log_mcp_tool_call(cid=correlation_id or "", user_id=locals().get('jwt_user_id'),
                          schema=ctx.schema_name if 'ctx' in dir() and ctx else None,
                          tool=tool_name, status="validation_error", duration_ms=_tool_ms, error=str(e))
        logger.error(f"Error de validación en tool {tool_name}: {e}")
        return create_jsonrpc_response(request_id, {
            "content": [{"type": "text", "text": "Error de validación"}],
            "isError": True
        })
    except GDIBaseException as e:
        _tool_ms = int((_time.time() - _tool_start) * 1000) if '_tool_start' in locals() else 0
        log_mcp_tool_call(cid=correlation_id or "", user_id=locals().get('jwt_user_id'),
                          schema=ctx.schema_name if 'ctx' in dir() and ctx else None,
                          tool=tool_name, status="business_error", duration_ms=_tool_ms, error=str(e))
        logger.error(f"Error de negocio en tool {tool_name}: {e}")
        error_data = {
            "error_type": type(e).__name__,
            "message": e.message,
        }
        if e.details:
            error_data["details"] = e.details
        if hasattr(e, "current_state") and e.current_state:
            error_data["current_state"] = e.current_state
        if hasattr(e, "required_state") and e.required_state:
            error_data["required_state"] = e.required_state
        if hasattr(e, "document_id"):
            error_data["document_id"] = e.document_id
        return create_jsonrpc_response(request_id, {
            "content": [{"type": "text", "text": json.dumps(error_data, ensure_ascii=False)}],
            "isError": True
        })
    except Exception as e:
        _tool_ms = int((_time.time() - _tool_start) * 1000) if '_tool_start' in locals() else 0
        log_mcp_tool_call(cid=correlation_id or "", user_id=locals().get('jwt_user_id'),
                          schema=ctx.schema_name if 'ctx' in dir() and ctx else None,
                          tool=tool_name, status="error", duration_ms=_tool_ms, error=str(e))
        logger.exception(f"Error ejecutando tool {tool_name}")
        return create_jsonrpc_response(request_id, {
            "content": [{"type": "text", "text": "Error interno del servidor"}],
            "isError": True
        })


async def process_jsonrpc_request(body: Dict, authorization_header: str = None, correlation_id: str = None) -> Dict:
    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")

    logger.info(f"JSON-RPC method: {method}")

    if method == "initialize":
        return await handle_initialize(request_id, params)

    elif method == "notifications/initialized":
        return None

    elif method == "tools/list":
        return await handle_list_tools(request_id)

    elif method == "tools/call":
        return await handle_call_tool(request_id, params, authorization_header, correlation_id)

    elif method == "ping":
        return create_jsonrpc_response(request_id, {})

    else:
        return create_jsonrpc_error(request_id, -32601, f"Method not found: {method}")


_OPENAPI_SPEC_PATH = Path(__file__).parent / "openapi.json"
try:
    with open(_OPENAPI_SPEC_PATH, "r", encoding="utf-8") as _f:
        _OPENAPI_SPEC_TEMPLATE = _f.read()
except OSError:
    _OPENAPI_SPEC_TEMPLATE = None
    logging.getLogger(__name__).exception("openapi.json no encontrado en %s", _OPENAPI_SPEC_PATH)

_openapi_spec_cache: dict = {}


async def openapi_spec(request: Request) -> JSONResponse:
    if _OPENAPI_SPEC_TEMPLATE is None:
        return JSONResponse({"error": "OpenAPI spec no disponible"}, status_code=503)

    base_url = os.getenv("MCP_RESOURCE_URI", "http://localhost:8005")
    auth0_domain = os.getenv("AUTH0_DOMAIN", "")

    cache_key = (base_url, auth0_domain)
    spec = _openapi_spec_cache.get(cache_key)
    if spec is None:
        text = _OPENAPI_SPEC_TEMPLATE.replace(
            "__OPENAPI_BASE_URL__", json.dumps(base_url)[1:-1]
        ).replace("__OPENAPI_AUTH0_DOMAIN__", json.dumps(auth0_domain)[1:-1])
        spec = json.loads(text)
        _openapi_spec_cache[cache_key] = spec

    return JSONResponse(spec)


async def mcp_manifest(request: Request) -> JSONResponse:
    resource_uri = os.getenv("MCP_RESOURCE_URI", "http://localhost:8005")

    return JSONResponse({
        "name": "gdi-mcp-server",
        "version": "1.0.0",
        "description": "GDI Backend MCP Server - Sistema de Gestión Documental Inteligente para gobiernos latinoamericanos",
        "protocol_version": MCP_PROTOCOL_VERSION,
        "transport": {
            "type": "http",
            "url": f"{resource_uri}/mcp"
        },
        "capabilities": {
            "tools": True,
            "resources": False,
            "prompts": False
        },
        "authentication": {
            "type": "oauth2",
            "oauth_protected_resource": f"{resource_uri}/.well-known/oauth-protected-resource",
            "oauth_authorization_server": f"{resource_uri}/.well-known/oauth-authorization-server"
        }
    })


async def root_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({
        "service": "gdi-mcp-server",
        "status": "ok",
        "version": VERSION,
        "commit": GIT_SHA,
        "transport": "streamable-http",
        "mcp_endpoint": "/mcp",
        "health": "/health",
        "docs": "/.well-known/mcp.json"
    })


async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "service": "gdi-mcp-server",
        "version": VERSION,
        "commit": GIT_SHA,
        "transport": "streamable-http",
        "protocol_version": MCP_PROTOCOL_VERSION,
        "rest_api_version": "v1",
        "endpoints": {
            "mcp": "/mcp",
            "rest_api": "/api/v1/",
            "openapi_spec": "/.well-known/openapi.json",
            "mcp_manifest": "/.well-known/mcp.json",
            "oauth_protected_resource": "/.well-known/oauth-protected-resource",
            "oauth_authorization_server": "/.well-known/oauth-authorization-server"
        }
    })


async def oauth_protected_resource_metadata(request: Request) -> JSONResponse:
    auth0_domain = os.getenv("AUTH0_DOMAIN", "")
    resource_uri = os.getenv("MCP_RESOURCE_URI", "")

    if not resource_uri:
        logger.warning(
            "[OAuth] MCP_RESOURCE_URI no configurado. "
            "Los clientes MCP no podrán obtener un token con audience correcto. "
            "Setear MCP_RESOURCE_URI en los secrets de Fly.io."
        )

    return JSONResponse({
        "resource": resource_uri,
        "authorization_servers": [f"https://{auth0_domain}"],
        "scopes_supported": ["openid", "profile", "email", "offline_access"],
        "bearer_methods_supported": ["header"]
    })


async def oauth_authorization_server_metadata(request: Request) -> JSONResponse:
    import httpx

    auth0_domain = os.getenv("AUTH0_DOMAIN", "")
    auth0_metadata_url = f"https://{auth0_domain}/.well-known/openid-configuration"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(auth0_metadata_url, timeout=10.0)
            response.raise_for_status()
            auth0_metadata = response.json()

            if "code_challenge_methods_supported" not in auth0_metadata:
                auth0_metadata["code_challenge_methods_supported"] = ["S256", "plain"]

            auth0_metadata["scopes_supported"] = ["openid", "profile", "email", "offline_access"]

            if "registration_endpoint" not in auth0_metadata:
                auth0_metadata["registration_endpoint"] = f"https://{auth0_domain}/oidc/register"

            logger.info(f"[OAuth] Proxeando metadata de Auth0 (enriched): {auth0_metadata_url}")
            return JSONResponse(auth0_metadata)

    except Exception as e:
        logger.error(f"[OAuth] Error obteniendo metadata de Auth0: {e}")
        return JSONResponse({
            "issuer": f"https://{auth0_domain}/",
            "authorization_endpoint": f"https://{auth0_domain}/authorize",
            "token_endpoint": f"https://{auth0_domain}/oauth/token",
            "userinfo_endpoint": f"https://{auth0_domain}/userinfo",
            "jwks_uri": f"https://{auth0_domain}/.well-known/jwks.json",
            "registration_endpoint": f"https://{auth0_domain}/oidc/register",
            "scopes_supported": ["openid", "profile", "email", "offline_access"],
            "response_types_supported": ["code", "token", "id_token", "code token", "code id_token", "token id_token", "code token id_token"],
            "grant_types_supported": ["authorization_code", "implicit", "refresh_token", "client_credentials"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "code_challenge_methods_supported": ["S256", "plain"]
        })


async def mcp_endpoint(request: Request) -> JSONResponse:
    session_id = request.headers.get("mcp-session-id")

    if request.method == "POST":
        client_ip = get_client_ip(request)
        try:
            rate_limiter.check(f"mcp_ip:{client_ip}", MCP_IP_LIMIT)
        except RateLimitExceeded as e:
            return JSONResponse(
                create_jsonrpc_error(None, -32029, f"Rate limit exceeded. Retry after {e.retry_after}s"),
                status_code=429,
                headers={"Retry-After": str(e.retry_after)}
            )

        try:
            body = await request.json()
        except Exception as e:
            return JSONResponse(
                create_jsonrpc_error(None, -32700, f"Parse error: {str(e)}"),
                status_code=400
            )

        authorization_header = request.headers.get("Authorization")
        method = body.get("method")

        tool_name_in_params = body.get("params", {}).get("name", "")
        tools_without_auth = ["get_agent_guide"]

        if method == "tools/call" and not authorization_header and tool_name_in_params not in tools_without_auth:
            resource_uri = os.getenv("MCP_RESOURCE_URI", "http://localhost:8005")
            return JSONResponse(
                create_jsonrpc_error(body.get("id"), -32002, "Authorization required. Use OAuth to authenticate."),
                status_code=401,
                headers={
                    "WWW-Authenticate": f'Bearer scope="openid profile email offline_access" resource_metadata="{resource_uri}/.well-known/oauth-protected-resource"'
                }
            )

        cid = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
        response = await process_jsonrpc_request(body, authorization_header, correlation_id=cid)

        if response is None:
            return JSONResponse({}, status_code=202)

        headers = {
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION
        }

        if body.get("method") == "initialize":
            new_session_id = str(uuid.uuid4())
            sessions[new_session_id] = {"created": True}
            headers["Mcp-Session-Id"] = new_session_id

        return JSONResponse(response, headers=headers)

    elif request.method == "GET":
        return JSONResponse(
            {
                "error": "Este servidor usa transporte Streamable HTTP. Usa POST /mcp para enviar JSON-RPC requests.",
                "transport": "streamable-http",
                "method": "POST",
                "endpoint": "/mcp"
            },
            status_code=405
        )

    elif request.method == "DELETE":
        if session_id and session_id in sessions:
            del sessions[session_id]
            return JSONResponse({"status": "terminated"})
        return JSONResponse({"error": "Session not found"}, status_code=404)

    else:
        return JSONResponse(
            {"error": f"Method {request.method} not allowed"},
            status_code=405
        )


routes = [
    Route("/", root_endpoint, methods=["GET"]),
    Route("/health", health, methods=["GET"]),

    Route("/.well-known/openapi.json", openapi_spec, methods=["GET"]),
    Route("/openapi.json", openapi_spec, methods=["GET"]),

    Route("/.well-known/mcp.json", mcp_manifest, methods=["GET"]),

    Route("/.well-known/oauth-protected-resource/mcp", oauth_protected_resource_metadata, methods=["GET"]),
    Route("/.well-known/oauth-protected-resource", oauth_protected_resource_metadata, methods=["GET"]),

    Route("/.well-known/oauth-authorization-server", oauth_authorization_server_metadata, methods=["GET"]),
    Route("/.well-known/oauth-authorization-server/mcp", oauth_authorization_server_metadata, methods=["GET"]),

    Route("/mcp", mcp_endpoint, methods=["GET", "POST", "DELETE"]),


    Route("/api/v1/cases/search", api_search_cases, methods=["GET"]),
    Route("/api/v1/cases/number/{case_number:path}", api_get_case_by_number, methods=["GET"]),
    Route("/api/v1/cases/{case_id}", api_get_case, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/history", api_get_case_history, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/documents", api_get_case_documents, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/permissions", api_get_case_permissions, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/prepare-assignment", api_prepare_assignment, methods=["GET"]),
    Route("/api/v1/cases/sectors/{sector_id}/users", api_get_sector_users, methods=["GET"]),

    Route("/api/v1/cases/{case_id}/assign", api_assign_case, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/close-assign", api_close_assignment, methods=["POST"]),

    Route("/api/v1/cases/{case_id}/responsibles", api_get_case_responsibles, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/responsibles", api_add_case_responsible, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/responsibles/{responsible_id}", api_remove_case_responsible, methods=["DELETE"]),

    Route("/api/v1/cases/{case_id}/tasks/{task_id}/close", api_close_task, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/tasks/{task_id}", api_update_task, methods=["PATCH"]),
    Route("/api/v1/cases/{case_id}/assignments", api_get_assignments, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/assignable-users", api_get_assignable_users, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/available-responsibles", api_get_available_responsibles_rest, methods=["GET"]),

    Route("/api/v1/cases/{case_id}/subsanar", api_subsanar_document, methods=["POST"]),

    Route("/api/v1/cases/{case_id}/movements", api_get_case_movements, methods=["GET"]),

    Route("/api/v1/cases/", api_create_case, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/transfer", api_transfer_case, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/documents/link", api_link_document, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/documents/propose", api_propose_document, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/prepare-transfer", api_prepare_transfer, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/documents/accept-proposal", api_accept_proposal, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/documents/reject-proposal", api_reject_proposal, methods=["POST"]),

    Route("/api/v1/documents/search", api_search_documents, methods=["GET"]),
    Route("/api/v1/documents/pending-signatures", api_get_pending_signatures, methods=["GET"]),
    Route("/api/v1/documents/search-official/{doc_number:path}", api_search_document_by_number, methods=["GET"]),
    Route("/api/v1/documents/check-signer-permissions", api_check_signer_permissions, methods=["GET"]),
    Route("/api/v1/documents/{document_id}", api_get_document, methods=["GET"]),
    Route("/api/v1/documents/{document_id}/content", api_get_document_content, methods=["GET"]),
    Route("/api/v1/documents/{document_id}/url", api_get_document_url, methods=["GET"]),
    Route("/api/v1/documents/{document_id}/signature-details", api_get_signature_details, methods=["GET"]),

    Route("/api/v1/documents/", api_create_document, methods=["POST"]),
    Route("/api/v1/documents/import", api_import_document, methods=["POST"]),
    Route("/api/v1/documents/{document_id}", api_save_document, methods=["PATCH"]),
    Route("/api/v1/documents/{document_id}", api_delete_document, methods=["DELETE"]),
    Route("/api/v1/documents/{document_id}/imported-pdf", api_replace_imported_pdf, methods=["PUT"]),
    Route("/api/v1/documents/{document_id}/start-signing", api_start_signing, methods=["POST"]),
    Route("/api/v1/documents/{document_id}/sign", api_sign_document, methods=["POST"]),
    Route("/api/v1/documents/{document_id}/reject", api_reject_document, methods=["POST"]),

    Route("/api/v1/signing/async-poll/{session_id}", api_async_poll, methods=["GET"]),

    Route("/api/v1/system/document-types", api_get_document_types, methods=["GET"]),
    Route("/api/v1/system/document-states", api_get_document_states, methods=["GET"]),
    Route("/api/v1/system/sectors", api_get_sectors, methods=["GET"]),
    Route("/api/v1/system/case-templates", api_get_case_templates, methods=["GET"]),
    Route("/api/v1/system/users/list", api_list_all_users, methods=["GET"]),
    Route("/api/v1/system/users/search", api_search_users, methods=["GET"]),
    Route("/api/v1/system/users/{user_id}", api_get_user_info, methods=["GET"]),

    Route("/api/v1/notes/received", api_get_notes, methods=["GET"]),
    Route("/api/v1/notes/sent", api_get_sent_notes, methods=["GET"]),
    Route("/api/v1/notes/archived", api_get_archived_notes, methods=["GET"]),
    Route("/api/v1/notes/{note_id}/archive", api_archive_note, methods=["PATCH"]),
    Route("/api/v1/notes/{note_id}", api_get_note_detail, methods=["GET"]),

    Route("/api/v1/memos/received", api_get_memos, methods=["GET"]),
    Route("/api/v1/memos/sent", api_get_sent_memos, methods=["GET"]),
    Route("/api/v1/memos/archived", api_get_archived_memos, methods=["GET"]),
    Route("/api/v1/memos/{memo_id}", api_get_memo_detail, methods=["GET"]),

    Route("/api/v1/sync/schema", api_sync_schema, methods=["GET"]),
    Route("/api/v1/sync/data", api_sync_data, methods=["GET"]),
    Route("/api/v1/sync/documents", api_sync_documents, methods=["GET"]),
    Route("/api/v1/search/semantic", api_semantic_search, methods=["GET"]),
    Route("/api/v1/records/search", api_search_records, methods=["GET"]),
    Route("/api/v1/records/families", api_get_registry_families, methods=["GET"]),
    Route("/api/v1/records/{record_id}/fields/{field_name}/verify", api_verify_record_field, methods=["POST"]),
    Route("/api/v1/records/{record_id}/fields/{field_name}", api_update_record_field, methods=["PATCH"]),
    Route("/api/v1/records/{record_id}/history", api_get_record_history, methods=["GET"]),
    Route("/api/v1/records/{record_id}/report", api_generate_record_report, methods=["POST"]),
    Route("/api/v1/records/{record_id}/relations/{relation_id}", api_delete_record_relation, methods=["DELETE"]),
    Route("/api/v1/records/{record_id}/relations", api_get_record_relations, methods=["GET"]),
    Route("/api/v1/records/{record_id}/relations", api_create_record_relation, methods=["POST"]),
    Route("/api/v1/records/{record_id}/cases/{link_id}", api_unlink_record_case, methods=["DELETE"]),
    Route("/api/v1/records/{record_id}/cases", api_get_record_cases, methods=["GET"]),
    Route("/api/v1/records/{record_id}/cases", api_link_record_case, methods=["POST"]),
    Route("/api/v1/records/{record_id}/documents/{link_id}", api_unlink_record_document, methods=["DELETE"]),
    Route("/api/v1/records/{record_id}/documents", api_get_record_documents, methods=["GET"]),
    Route("/api/v1/records/{record_id}/documents", api_link_record_document, methods=["POST"]),
    Route("/api/v1/records/{record_id}", api_get_record, methods=["GET"]),
    Route("/api/v1/records/{record_id}", api_update_record, methods=["PATCH"]),
    Route("/api/v1/records", api_create_record, methods=["POST"]),
    Route("/api/v1/registries", api_get_registry_families, methods=["GET"]),

    Route("/api/v1/public/{muni}/search", api_public_search, methods=["GET"]),
    Route("/api/v1/public/{muni}/registries", api_public_registries, methods=["GET"]),
    Route("/api/v1/public/{muni}/registries/{code}/records", api_public_list_records, methods=["GET"]),
    Route("/api/v1/public/{muni}/records/{record_number}", api_public_get_record, methods=["GET"]),
    Route("/api/v1/public/{muni}/documents/{document_id}/content", api_public_get_document_content, methods=["GET"]),

    Route("/api/v1/tad/citizens", api_tad_create_citizen, methods=["POST"]),
    Route("/api/v1/tad/citizens/{id_or_cuil}", api_tad_get_citizen, methods=["GET"]),
    Route("/api/v1/tad/citizens/{id}", api_tad_patch_citizen, methods=["PATCH"]),
    Route("/api/v1/tad/document-types", api_tad_get_document_types, methods=["GET"]),
    Route("/api/v1/tad/document-types/{id}/fields", api_tad_get_document_type_fields, methods=["GET"]),
    Route("/api/v1/tad/documents", api_tad_create_document, methods=["POST"]),
    Route("/api/v1/tad/documents/{id}", api_tad_get_document, methods=["GET"]),
    Route("/api/v1/tad/case-templates", api_tad_get_case_templates, methods=["GET"]),
    Route("/api/v1/tad/cases", api_tad_create_case, methods=["POST"]),
    Route("/api/v1/tad/cases", api_tad_get_cases, methods=["GET"]),
    Route("/api/v1/tad/cases/{id}", api_tad_get_case_detail, methods=["GET"]),
    Route("/api/v1/tad/cases/{id}/propose", api_tad_propose_document, methods=["POST"]),
    Route("/api/v1/tad/webhook/test", api_tad_webhook_test, methods=["POST"]),
]

_allowed_origins = (
    [f"http://localhost:{port}" for port in range(3000, 3051)] +
    [f"http://127.0.0.1:{port}" for port in range(3000, 3051)] +
    [f"http://localhost:{port}" for port in range(8000, 8051)] +
    [f"http://127.0.0.1:{port}" for port in range(8000, 8051)]
)
_frontend_urls = os.getenv("FRONTEND_URL", "")
for _url in _frontend_urls.split(","):
    _url = _url.strip()
    if _url:
        _allowed_origins.append(_url)

app = Starlette(routes=routes, lifespan=lifespan)

app.add_middleware(GatewayMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id", "MCP-Protocol-Version", "X-Correlation-ID"]
)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8005))

    logger.info("=" * 60)
    logger.info("GDI-Backend MCP Server (HTTP + REST API)")
    logger.info("=" * 60)
    logger.info(f"Transport: Streamable HTTP")
    logger.info(f"Protocol Version: {MCP_PROTOCOL_VERSION}")
    logger.info(f"Port: {port}")
    logger.info(f"")
    logger.info(f"Autenticación MCP (RFC 9728):")
    logger.info(f"  - OAuth 2.0: Claude Code/ChatGPT/Gemini hacen login automático via Auth0")
    logger.info(f"  - Authorization: Bearer <jwt>")
    logger.info(f"")
    logger.info(f"MCP Endpoints:")
    logger.info(f"  - GET  /health  (health check)")
    logger.info(f"  - POST /mcp     (JSON-RPC, OAuth/JWT)")
    logger.info(f"")
    logger.info(f"OAuth 2.0 (DCR nativo en Auth0):")
    logger.info(f"  - GET  /.well-known/oauth-protected-resource    (RFC 9728)")
    logger.info(f"  - GET  /.well-known/oauth-authorization-server  (RFC 8414 - para ChatGPT)")
    logger.info(f"  - GET  /.well-known/oauth-authorization-server/mcp")
    logger.info(f"")
    logger.info(f"OpenAPI Spec (para ChatGPT Actions):")
    logger.info(f"  - GET  /.well-known/openapi.json")
    logger.info(f"  - GET  /openapi.json")
    logger.info(f"")
    logger.info(f"REST API v1 (X-API-Key header):")
    logger.info(f"  - GET /api/v1/cases/search")
    logger.info(f"  - GET /api/v1/cases/{{case_id}}")
    logger.info(f"  - GET /api/v1/cases/{{case_id}}/history")
    logger.info(f"  - GET /api/v1/cases/{{case_id}}/documents")
    logger.info(f"  - GET /api/v1/cases/{{case_id}}/permissions")
    logger.info(f"  - GET /api/v1/documents/search")
    logger.info(f"  - GET /api/v1/documents/pending-signatures")
    logger.info(f"  - GET /api/v1/documents/{{document_id}}")
    logger.info(f"  - GET /api/v1/documents/{{document_id}}/content")
    logger.info(f"  - GET /api/v1/documents/{{document_id}}/url")
    logger.info(f"  - GET /api/v1/system/document-types")
    logger.info(f"  - GET /api/v1/system/sectors")
    logger.info(f"  - GET /api/v1/system/users/{{user_id}}")
    logger.info(f"  - GET /api/v1/system/case-templates")
    logger.info(f"")
    logger.info(f"Backup Sync API (X-API-Key backup, sin X-User-ID):")
    logger.info(f"  - GET /api/v1/sync/schema")
    logger.info(f"  - GET /api/v1/sync/data")
    logger.info(f"  - GET /api/v1/sync/documents")
    logger.info("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=port)
