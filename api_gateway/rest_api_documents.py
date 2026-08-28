from fastapi.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from api_gateway.auth_rest import validate_rest_api_key
from api_gateway.rest_common import (
    rest_endpoint,
    _error_response,
    _success_response,
    _get_api_key,
    _get_user_id,
    _is_valid_uuid,
)
from api_gateway.tools import documents, system, search
from shared.exceptions import (
    ValidationError,
    DatabaseBusyError,
    AuthorizationError,
    NotFoundError,
    DocumentNotFoundError,
    DocumentStateError,
)
from shared.logging import get_logger

logger = get_logger(__name__)


@rest_endpoint(require_user=False)
async def api_search_documents(request: Request, ctx, user_id: str) -> dict:
    params = request.query_params
    _min_signers_raw = params.get("min_signers")
    return await documents.search_documents(
        ctx=ctx,
        user_id=user_id,
        page=int(params.get("page", 1)),
        page_size=int(params.get("page_size", 20)),
        search=params.get("search"),
        status=params.get("status"),
        document_type=params.get("document_type"),
        case_id=params.get("case_id"),
        min_signers=int(_min_signers_raw) if _min_signers_raw is not None else None,
        sector_filter=params.get("sector_filter"),
        text_search=params.get("text_search"),
        date_filter=params.get("date_filter"),
    )


@rest_endpoint(require_user=False, not_found_message="Documento no encontrado")
async def api_get_document(request: Request, ctx, user_id: str):
    document_id = request.path_params.get("document_id")

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)
    if not _is_valid_uuid(document_id):
        return _error_response("document_id inválido (se espera UUID)", status_code=400)

    try:
        return await documents.get_document(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id
        )
    except ValueError as e:
        msg = str(e).lower()
        if "permisos" in msg:
            return _error_response(str(e), status_code=403)
        return _error_response(str(e), status_code=400)


@rest_endpoint(require_user=False, not_found_message="Documento no encontrado")
async def api_get_document_content(request: Request, ctx, user_id: str):
    document_id = request.path_params.get("document_id")

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        return await documents.get_document_content(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id
        )
    except ValueError as e:
        msg = str(e).lower()
        if "permisos" in msg:
            return _error_response(str(e), status_code=403)
        return _error_response(str(e), status_code=400)


@rest_endpoint(require_user=True)
async def api_get_pending_signatures(request: Request, ctx, user_id: str):
    return await documents.get_pending_signatures(
        ctx=ctx,
        user_id=user_id
    )


async def api_get_document_url(request: Request) -> JSONResponse:
    from database import fetch_one
    from services.storage.cloudflare import get_tenant_r2_client

    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = await validate_rest_api_key(api_key, user_id, request=request)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    if ctx.auth_source != "service":
        from services.documents.permissions import can_user_view_document
        if not await can_user_view_document(document_id, user_id, schema_name=ctx.schema_name):
            return _error_response(
                "No tiene permisos para acceder a este documento",
                status_code=403
            )

    try:
        result = await fetch_one(
            """
            SELECT official_number, pdf_location
            FROM official_documents
            WHERE id = $1
              AND signed_at IS NOT NULL
            """,
            document_id,
            schema_name=ctx.schema_name
        )

        if not result:
            return _error_response(
                "Documento no encontrado o no es oficial",
                status_code=404
            )

        official_number = result["official_number"]
        pdf_location = result.get("pdf_location") or "oficial"

        r2_client = await get_tenant_r2_client(schema_name=ctx.schema_name)
        pdf_url = await run_in_threadpool(r2_client.get_oficial_url, official_number, pdf_location)

        if not pdf_url:
            return _error_response(
                "No se pudo generar URL del documento",
                status_code=500
            )

        return _success_response({
            "document_id": document_id,
            "official_number": official_number,
            "pdf_url": pdf_url,
            "expires_in": 600
        })

    except DatabaseBusyError as e:
        logger.warning(f"[REST API] BD saturada en {'get_document_url'}: {e}")
        return _error_response("Servidor ocupado, reintente en unos segundos",
                               status_code=503, retry_after=5)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_document_url")
        return _error_response("Error interno del servidor", status_code=500)


@rest_endpoint(require_user=False)
async def api_create_document(request: Request, ctx, user_id: str):
    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    document_type_acronym = body.get("document_type_acronym")
    reference = body.get("reference")
    case_id = body.get("case_id")

    if not document_type_acronym:
        return _error_response("document_type_acronym es requerido", status_code=400)
    if not reference:
        return _error_response("reference es requerido", status_code=400)

    result = await documents.create_document(
        ctx=ctx,
        document_type_acronym=document_type_acronym,
        reference=reference,
        user_id=user_id,
        case_id=case_id,
        recipients=body.get("recipients")
    )
    return JSONResponse(result, status_code=201)


@rest_endpoint(require_user=False)
async def api_save_document(request: Request, ctx, user_id: str):
    document_id = request.path_params.get("document_id")

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    try:
        result = await documents.save_document(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id,
            content=body.get("content"),
            reference=body.get("reference"),
            signers=body.get("signers"),
            recipients=body.get("recipients"),
            proposed_case_ids=body.get("proposed_case_ids")
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except DocumentNotFoundError:
        return _error_response("Documento no encontrado", status_code=404)
    except DocumentStateError as e:
        return _error_response(str(e), status_code=409)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except ValidationError as e:
        return _error_response(str(e), status_code=400)


@rest_endpoint(require_user=False)
async def api_subsanar_document(request: Request, ctx, user_id: str):
    case_id = request.path_params.get("case_id")

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    doc_erroneo = body.get("official_document_id_erroneo")
    doc_justifica = body.get("official_document_id_justifica")

    if not doc_erroneo:
        return _error_response("official_document_id_erroneo es requerido", status_code=400)
    if not doc_justifica:
        return _error_response("official_document_id_justifica es requerido", status_code=400)
    if doc_erroneo == doc_justifica:
        return _error_response(
            "official_document_id_erroneo y official_document_id_justifica no pueden ser iguales",
            status_code=400
        )

    try:
        from shared.utils import get_authenticated_user
        from services.cases.subsanacion import subsanar_document_service

        db_user_id = await get_authenticated_user(user_id, schema_name=ctx.schema_name)

        result = await subsanar_document_service(
            case_id=case_id,
            official_document_id_erroneo=doc_erroneo,
            official_document_id_justifica=doc_justifica,
            user_id=db_user_id,
            schema_name=ctx.schema_name,
        )
        return _success_response({
            "success": True,
            "data": result,
            "message": "Documento subsanado exitosamente",
        })

    except AuthorizationError:
        return _error_response("Sin permisos ADMIN sobre este expediente", status_code=403)
    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except ValidationError as e:
        return _error_response(str(e), status_code=400)


@rest_endpoint(require_user=False)
async def api_delete_document(request: Request, ctx, user_id: str):
    document_id = request.path_params.get("document_id")

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        result = await documents.delete_document(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id
        )
        return _success_response(result)

    except Exception as e:
        error_msg = str(e).lower()
        if "no autorizado" in error_msg or "unauthorized" in error_msg or "permiso" in error_msg:
            return _error_response("Usuario no autorizado para eliminar este documento", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Documento no encontrado", status_code=404)
        if "estado" in error_msg or "state" in error_msg:
            return _error_response("Solo se pueden eliminar documentos en estado draft o rejected", status_code=409)
        raise


@rest_endpoint(require_user=False, not_found_message="Documento no encontrado")
async def api_search_document_by_number(request: Request, ctx, user_id: str):
    doc_number = request.path_params.get("doc_number")

    if not doc_number:
        return _error_response("doc_number es requerido", status_code=400)

    try:
        return await documents.search_document_by_number(
            ctx=ctx,
            doc_number=doc_number,
            user_id=user_id
        )
    except ValueError as e:
        msg = str(e).lower()
        if "permisos" in msg:
            return _error_response(str(e), status_code=403)
        return _error_response(str(e), status_code=400)


@rest_endpoint(require_user=False, not_found_message="Documento no encontrado")
async def api_get_signature_details(request: Request, ctx, user_id: str):
    document_id = request.path_params.get("document_id")

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)
    if not _is_valid_uuid(document_id):
        return _error_response("document_id inválido (se espera UUID)", status_code=400)

    return await documents.get_signature_details(
        ctx=ctx,
        document_id=document_id,
        user_id=user_id
    )


@rest_endpoint(require_user=False)
async def api_import_document(request: Request, ctx, user_id: str):
    form = await request.form()
    document_type_acronym = form.get("document_type_acronym")
    reference = form.get("reference")
    pdf_file = form.get("pdf_file")

    if not all([document_type_acronym, reference, pdf_file]):
        return _error_response(
            "document_type_acronym, reference y pdf_file son requeridos",
            status_code=400
        )

    from services.documents.importing.import_service import create_imported_document

    result = await create_imported_document(
        user_id=user_id,
        document_type_acronym=document_type_acronym,
        reference=reference,
        pdf_file=pdf_file,
        schema_name=ctx.schema_name
    )
    return JSONResponse(result, status_code=201)


@rest_endpoint(require_user=False)
async def api_replace_imported_pdf(request: Request, ctx, user_id: str):
    document_id = request.path_params.get("document_id")

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        form = await request.form()
        pdf_file = form.get("pdf_file")

        if not pdf_file:
            return _error_response("pdf_file es requerido", status_code=400)

        from services.documents.importing.import_service import replace_imported_pdf

        result = await replace_imported_pdf(
            document_id=document_id,
            pdf_file=pdf_file,
            user_id=user_id,
            schema_name=ctx.schema_name
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        error_msg = str(e).lower()
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Documento no encontrado", status_code=404)
        if "permiso" in error_msg or "authorization" in error_msg:
            return _error_response("Sin permisos para reemplazar PDF", status_code=403)
        if "estado" in error_msg or "state" in error_msg:
            return _error_response("Solo se puede reemplazar PDF en documentos draft", status_code=409)
        raise


@rest_endpoint(require_user=False, not_found_message="Usuario o tipo de documento no encontrado")
async def api_check_signer_permissions(request: Request, ctx, user_id: str):
    params = request.query_params
    user_id_param = params.get("user_id")
    doc_type = params.get("document_type_acronym")

    if not user_id_param:
        return _error_response("user_id query param es requerido", status_code=400)
    if not doc_type:
        return _error_response("document_type_acronym query param es requerido", status_code=400)

    return await system.check_signer_permissions(
        ctx=ctx,
        user_id_to_check=user_id_param,
        document_type_acronym=doc_type
    )


@rest_endpoint(require_user=False)
async def api_semantic_search(request: Request, ctx, user_id: str):
    params = request.query_params
    query = params.get("query", "").strip()

    if not query or len(query) < 3:
        return _error_response("query param requerido (min 3 caracteres)", status_code=400)

    try:
        limit = min(int(params.get("limit", 20)), 50)
    except (ValueError, TypeError):
        limit = 20

    try:
        return await search.semantic_search_tool(
            ctx=ctx,
            query=query,
            limit=limit,
            source="api",
        )
    except Exception as e:
        logger.exception("[REST API] Error en semantic_search")
        return _error_response("Busqueda temporalmente no disponible", status_code=503)
