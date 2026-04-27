"""
REST API Handlers para GDI-MCP Server.

Endpoints REST públicos con autenticación por API Key.
Cada handler valida la API Key, extrae contexto y llama a los tools existentes.
"""
import os
import sys
import logging
import json
from datetime import datetime, date
from typing import Optional
from uuid import UUID

# Agregar path del backend para imports
backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_root)

from starlette.requests import Request
from starlette.responses import JSONResponse

from api_gateway.auth_rest import validate_rest_api_key, validate_backup_api_key, BackupAuthError, check_and_log_sync_access
from api_gateway.tools import cases, documents, system, notes, sync, records, memos, search
from shared.exceptions import NotFoundError, AuthorizationError, ValidationError, DocumentNotFoundError, DocumentStateError, ConflictError

# Nota: 'system' ya estaba importado pero aseguramos que esté disponible

logger = logging.getLogger(__name__)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _get_api_key(request: Request) -> Optional[str]:
    """Extrae API Key del header X-API-Key."""
    return request.headers.get("X-API-Key")


def _get_user_id(request: Request) -> Optional[str]:
    """Extrae User ID del header X-User-ID."""
    return request.headers.get("X-User-ID")


def _json_serializer(obj):
    """Serializador JSON personalizado para tipos no estándar."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _error_response(message: str, status_code: int = 400) -> JSONResponse:
    """Crea respuesta de error estándar."""
    return JSONResponse({"error": message}, status_code=status_code)


def _success_response(data: dict) -> JSONResponse:
    """Crea respuesta exitosa con serialización de datetime/UUID."""
    # Serializar manualmente para manejar datetime y UUID
    json_str = json.dumps(data, default=_json_serializer)
    return JSONResponse(content=json.loads(json_str))


# ============================================================================
# CASES ENDPOINTS
# ============================================================================

async def api_search_cases(request: Request) -> JSONResponse:
    """
    GET /api/v1/cases/search

    Query params:
        - page: int (default 1)
        - page_size: int (default 20, max 100)
        - search: str (buscar por case_number o reference)
        - status: str (active, inactive, archived)
        - date_filter: str (hoy, ayer, ultimos_7_dias, ultimos_30_dias)
        - sector_filter: str (acronym del sector)

    Response includes: case_number, reference, ai_summary, short_ai_summary

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    params = request.query_params

    try:
        result = cases.search_cases(
            ctx=ctx,
            user_id=user_id,
            page=int(params.get("page", 1)),
            page_size=int(params.get("page_size", 20)),
            search=params.get("search"),
            status=params.get("status"),
            date_filter=params.get("date_filter"),
            sector_filter=params.get("sector_filter")
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except ValidationError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en search_cases")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_case(request: Request) -> JSONResponse:
    """
    GET /api/v1/cases/{case_id}

    Path params:
        - case_id: UUID del expediente

    Query params:
        - include_documents: bool (default false)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    params = request.query_params
    include_documents = params.get("include_documents", "").lower() == "true"

    try:
        result = cases.get_case(
            ctx=ctx,
            case_id=case_id,
            user_id=user_id,
            include_documents=include_documents
        )

        if not result:
            return _error_response("Expediente no encontrado o sin permisos", status_code=404)

        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_case")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_case_history(request: Request) -> JSONResponse:
    """
    GET /api/v1/cases/{case_id}/history

    Path params:
        - case_id: UUID del expediente

    Response includes: ai_summary, short_ai_summary, movements

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        result = cases.get_case_history(ctx=ctx, case_id=case_id, user_id=user_id)
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=403)
    except Exception as e:
        if "no encontrado" in str(e).lower():
            return _error_response("Expediente no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en get_case_history")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_case_documents(request: Request) -> JSONResponse:
    """
    GET /api/v1/cases/{case_id}/documents

    Path params:
        - case_id: UUID del expediente

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        result = cases.get_case_documents(
            ctx=ctx,
            case_id=case_id,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=403)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_case_documents")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_case_permissions(request: Request) -> JSONResponse:
    """
    GET /api/v1/cases/{case_id}/permissions

    Path params:
        - case_id: UUID del expediente

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        result = cases.get_case_permissions(
            ctx=ctx,
            case_id=case_id,
            user_id=user_id
        )
        return _success_response(result)

    except Exception as e:
        if "no encontrado" in str(e).lower():
            return _error_response("Expediente no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en get_case_permissions")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# DOCUMENTS ENDPOINTS
# ============================================================================

async def api_search_documents(request: Request) -> JSONResponse:
    """
    GET /api/v1/documents/search

    Query params:
        - page: int (default 1)
        - page_size: int (default 20, max 100)
        - search: str (buscar por document_number)
        - status: str (pending, sent_to_sign, signed, rejected)
        - document_type: str (acronym, ej: INF, DICT)
        - case_id: str (filtrar por expediente vinculado)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    params = request.query_params

    try:
        result = documents.search_documents(
            ctx=ctx,
            user_id=user_id,
            page=int(params.get("page", 1)),
            page_size=int(params.get("page_size", 20)),
            search=params.get("search"),
            status=params.get("status"),
            document_type=params.get("document_type"),
            case_id=params.get("case_id")
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en search_documents")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_document(request: Request) -> JSONResponse:
    """
    GET /api/v1/documents/{document_id}

    Path params:
        - document_id: UUID del documento

    Response includes: ai_summary, short_resume, state_category, status, details

    Headers:
        - X-API-Key: required
        - X-User-ID: optional (necesario para docs en firma)
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        # get_document es async
        result = await documents.get_document(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        msg = str(e).lower()
        if "permisos" in msg:
            return _error_response(str(e), status_code=403)
        return _error_response(str(e), status_code=400)
    except Exception as e:
        if "no encontrado" in str(e).lower() or "not found" in str(e).lower():
            return _error_response("Documento no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en get_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_document_content(request: Request) -> JSONResponse:
    """
    GET /api/v1/documents/{document_id}/content

    Obtiene el contenido HTML de un documento OFICIAL (firmado).

    Path params:
        - document_id: UUID del documento oficial

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        result = documents.get_document_content(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        msg = str(e).lower()
        if "permisos" in msg:
            return _error_response(str(e), status_code=403)
        return _error_response(str(e), status_code=400)
    except Exception as e:
        if "no encontrado" in str(e).lower() or "not found" in str(e).lower():
            return _error_response("Documento no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en get_document_content")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_pending_signatures(request: Request) -> JSONResponse:
    """
    GET /api/v1/documents/pending-signatures

    Lista documentos pendientes donde es el turno del usuario.

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    try:
        result = documents.get_pending_signatures(
            ctx=ctx,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_pending_signatures")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# SYSTEM ENDPOINTS
# ============================================================================

async def api_get_document_types(request: Request) -> JSONResponse:
    """
    GET /api/v1/system/document-types

    Lista todos los tipos de documentos disponibles.

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    try:
        result = system.get_document_types(ctx=ctx)
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en get_document_types")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_user_info(request: Request) -> JSONResponse:
    """
    GET /api/v1/system/users/{user_id}

    Obtiene información de un usuario.

    Path params:
        - user_id: UUID del usuario a consultar

    Headers:
        - X-API-Key: required
        - X-User-ID: required (usuario que hace la consulta)
    """
    api_key = _get_api_key(request)
    auth_user_id = _get_user_id(request)  # Usuario autenticado
    target_user_id = request.path_params.get("user_id")  # Usuario a consultar

    try:
        ctx = validate_rest_api_key(api_key, auth_user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not target_user_id:
        return _error_response("user_id path param es requerido", status_code=400)

    # SEC-12: Solo permitir consultar la propia info
    if target_user_id != auth_user_id:
        return _error_response(
            "Solo puede consultar su propia informacion de usuario",
            status_code=403
        )

    try:
        result = system.get_user_info(ctx=ctx, user_id=target_user_id)
        return _success_response(result)

    except ValueError as e:
        if "no encontrado" in str(e).lower():
            return _error_response(str(e), status_code=404)
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_user_info")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_document_url(request: Request) -> JSONResponse:
    """
    GET /api/v1/documents/{document_id}/url

    Obtiene URL firmada temporal para descargar el PDF de un documento oficial.

    Path params:
        - document_id: UUID del documento oficial

    Headers:
        - X-API-Key: required
        - X-User-ID: required

    Returns:
        - document_id: UUID del documento
        - official_number: Número oficial del documento
        - pdf_url: URL firmada temporal para descarga
        - expires_in: Tiempo de expiración en segundos (600)
    """
    from database import execute_query
    from services.storage.cloudflare import get_tenant_r2_client

    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id, request=request)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    # SEC-13: Verificar permisos del usuario sobre el documento
    # Service auth (AI Worker) bypasea authz - necesita acceso a todos
    # los documentos del tenant para transcripcion
    if ctx.auth_source != "service":
        from services.documents.permissions import can_user_view_document
        if not can_user_view_document(document_id, user_id, schema_name=ctx.schema_name):
            return _error_response(
                "No tiene permisos para acceder a este documento",
                status_code=403
            )

    try:
        # Buscar documento oficial en BD (solo firmados, signed_at NOT NULL)
        result = execute_query(
            """
            SELECT official_number
            FROM official_documents
            WHERE id = %s
              AND signed_at IS NOT NULL
            """,
            (document_id,),
            fetch_one=True,
            schema_name=ctx.schema_name
        )

        if not result:
            return _error_response(
                "Documento no encontrado o no es oficial",
                status_code=404
            )

        official_number = result["official_number"]

        # Generar URL firmada de R2
        r2_client = get_tenant_r2_client(schema_name=ctx.schema_name)
        pdf_url = r2_client.get_oficial_url(official_number)

        if not pdf_url:
            return _error_response(
                "No se pudo generar URL del documento",
                status_code=500
            )

        # Retornar respuesta
        return _success_response({
            "document_id": document_id,
            "official_number": official_number,
            "pdf_url": pdf_url,
            "expires_in": 600
        })

    except Exception as e:
        logger.exception(f"[REST API] Error en get_document_url")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - DOCUMENTOS (Escritura)
# ============================================================================

async def api_create_document(request: Request) -> JSONResponse:
    """
    POST /api/v1/documents/

    Crea un nuevo documento en estado borrador.

    Body JSON:
        - document_type_acronym: Acrónimo del tipo (INF, DICT, etc.)
        - reference: Descripción del documento
        - case_id: UUID del expediente a vincular (opcional)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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

    try:
        result = documents.create_document(
            ctx=ctx,
            document_type_acronym=document_type_acronym,
            reference=reference,
            user_id=user_id,
            case_id=case_id,
            recipients=body.get("recipients")
        )
        return JSONResponse(result, status_code=201)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except ValidationError as e:
        return _error_response(str(e), status_code=400)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except Exception as e:
        logger.exception(f"[REST API] Error en create_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_save_document(request: Request) -> JSONResponse:
    """
    PATCH /api/v1/documents/{document_id}

    Guarda cambios en un documento borrador.

    Path params:
        - document_id: UUID del documento

    Body JSON:
        - content: Contenido HTML (opcional)
        - reference: Nueva descripción (opcional)
        - signers: Lista de firmantes (opcional)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    try:
        result = documents.save_document(
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
    except DocumentStateError:
        return _error_response("Documento no puede editarse en su estado actual", status_code=409)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except ValidationError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en save_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_start_signing(request: Request) -> JSONResponse:
    """
    POST /api/v1/documents/{document_id}/start-signing

    Inicia el proceso de firma de un documento.

    Path params:
        - document_id: UUID del documento

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        result = await documents.start_signing(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except ValidationError as e:
        return _error_response(str(e), status_code=400)
    except DocumentNotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except DocumentStateError as e:
        return _error_response(str(e), status_code=409)
    except Exception as e:
        error_msg = str(e).lower()
        if "no autorizado" in error_msg or "unauthorized" in error_msg:
            return _error_response("Usuario no autorizado", status_code=403)
        if "usuario" in error_msg and "no encontrado" in error_msg:
            return _error_response("Usuario no encontrado", status_code=404)
        if "documento" in error_msg and "no encontrado" in error_msg:
            return _error_response("Documento no encontrado", status_code=404)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response(str(e), status_code=404)
        if "estado" in error_msg or "state" in error_msg:
            return _error_response("Documento no puede firmarse en su estado actual", status_code=409)
        logger.exception(f"[REST API] Error en start_signing")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - EXPEDIENTES (Operaciones)
# ============================================================================

async def api_get_case_by_number(request: Request) -> JSONResponse:
    """
    GET /api/v1/cases/number/{case_number}

    Obtiene un expediente por su número exacto.

    Path params:
        - case_number: Número exacto del expediente

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    case_number = request.path_params.get("case_number")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not case_number:
        return _error_response("case_number es requerido", status_code=400)

    try:
        result = cases.get_case_by_number(
            ctx=ctx,
            case_number=case_number,
            user_id=user_id
        )

        if not result:
            return _error_response("Expediente no encontrado", status_code=404)

        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en get_case_by_number")
        return _error_response("Error interno del servidor", status_code=500)


async def api_prepare_assignment(request: Request) -> JSONResponse:
    """
    GET /api/v1/cases/{case_id}/prepare-assignment

    Prepara información para asignación de expediente.

    Path params:
        - case_id: UUID del expediente

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        result = cases.prepare_assignment(
            ctx=ctx,
            case_id=case_id,
            user_id=user_id
        )
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en prepare_assignment")
        return _error_response("Error interno del servidor", status_code=500)


async def api_assign_case(request: Request) -> JSONResponse:
    """
    POST /api/v1/cases/{case_id}/assign

    Asigna un expediente a otro sector.

    Path params:
        - case_id: UUID del expediente

    Body JSON:
        - target_sector_id: UUID del sector destino
        - reason: Motivo de la asignación (5-500 chars)
        - assigned_user_id: UUID del usuario asignado (opcional)
        - create_official_doc: Generar documento PV (opcional)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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

    try:
        result = await cases.assign_case(
            ctx=ctx,
            case_id=case_id,
            target_sector_id=target_sector_id,
            reason=reason,
            user_id=user_id,
            assigned_user_id=body.get("assigned_user_id"),
            create_official_doc=body.get("create_official_doc", False)
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        if "permiso" in str(e).lower() or "authorization" in str(e).lower():
            return _error_response("Sin permisos para asignar", status_code=403)
        logger.exception(f"[REST API] Error en assign_case")
        return _error_response("Error interno del servidor", status_code=500)


async def api_close_assignment(request: Request) -> JSONResponse:
    """
    POST /api/v1/cases/{case_id}/close-assign

    Cierra una asignación de expediente.

    Path params:
        - case_id: UUID del expediente

    Body JSON:
        - movement_id: UUID del movimiento a cerrar
        - reason: Razón del cierre (5-500 chars)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    movement_id = body.get("movement_id")
    reason = body.get("reason")

    if not movement_id:
        return _error_response("movement_id es requerido", status_code=400)
    if not reason:
        return _error_response("reason es requerido", status_code=400)

    try:
        result = await cases.close_assignment(
            ctx=ctx,
            case_id=case_id,
            movement_id=movement_id,
            reason=reason,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        if "permiso" in str(e).lower() or "authorization" in str(e).lower():
            return _error_response("Sin permisos para cerrar asignación", status_code=403)
        if "no encontrado" in str(e).lower() or "not found" in str(e).lower():
            return _error_response("Movimiento no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en close_assignment")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - SISTEMA (Catálogos)
# ============================================================================

async def api_get_document_states(request: Request) -> JSONResponse:
    """
    GET /api/v1/system/document-states

    Obtiene catálogo de estados de documentos.

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    try:
        result = system.get_document_states(ctx=ctx)
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en get_document_states")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - DOCUMENTOS (Escritura adicional)
# ============================================================================

async def api_sign_document(request: Request) -> JSONResponse:
    """
    POST /api/v1/documents/{document_id}/sign

    Firma un documento como el usuario actual.

    Path params:
        - document_id: UUID del documento a firmar

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        result = await documents.sign_document(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id
        )
        return _success_response(result)

    except Exception as e:
        error_msg = str(e).lower()
        if "no autorizado" in error_msg or "unauthorized" in error_msg or "permiso" in error_msg:
            return _error_response("Usuario no autorizado para firmar este documento", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Documento no encontrado", status_code=404)
        if "estado" in error_msg or "state" in error_msg:
            return _error_response("Documento no puede firmarse en su estado actual", status_code=409)
        logger.exception(f"[REST API] Error en sign_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_reject_document(request: Request) -> JSONResponse:
    """
    POST /api/v1/documents/{document_id}/reject

    Rechaza un documento proporcionando una razon.

    Path params:
        - document_id: UUID del documento a rechazar

    Body JSON:
        - reason: Motivo del rechazo (requerido)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON inválido", status_code=400)

    reason = body.get("reason")

    if not reason or not reason.strip():
        return _error_response("reason es requerido y no puede estar vacío", status_code=400)

    try:
        result = documents.reject_document(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id,
            reason=reason
        )
        return _success_response(result)

    except Exception as e:
        error_msg = str(e).lower()
        if "no autorizado" in error_msg or "unauthorized" in error_msg or "permiso" in error_msg:
            return _error_response("Usuario no autorizado para rechazar este documento", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Documento no encontrado", status_code=404)
        if "estado" in error_msg or "state" in error_msg:
            return _error_response("Documento no puede rechazarse en su estado actual", status_code=409)
        logger.exception(f"[REST API] Error en reject_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_delete_document(request: Request) -> JSONResponse:
    """
    DELETE /api/v1/documents/{document_id}

    Elimina un documento borrador. Solo se pueden eliminar documentos en estado draft o rejected.

    Path params:
        - document_id: UUID del documento a eliminar

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        result = documents.delete_document(
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
        logger.exception(f"[REST API] Error en delete_document")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - EXPEDIENTES (Operaciones adicionales)
# ============================================================================

async def api_create_case(request: Request) -> JSONResponse:
    """
    POST /api/v1/cases/

    Crea un nuevo expediente con caratula automatica.

    Body JSON:
        - case_template_id: UUID del template de expediente (requerido)
        - reference: Referencia/asunto del expediente (requerido)
        - owner_sector_id: UUID del sector propietario (opcional)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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
        logger.exception(f"[REST API] Error en create_case")
        return _error_response("Error interno del servidor", status_code=500)


async def api_transfer_case(request: Request) -> JSONResponse:
    """
    POST /api/v1/cases/{case_id}/transfer

    Transfiere un expediente a otro sector (transfiere propiedad).

    Path params:
        - case_id: UUID del expediente

    Body JSON:
        - target_sector_id: UUID del sector destino (requerido)
        - reason: Motivo de la transferencia (requerido)
        - assigned_user_id: UUID del usuario asignado (opcional)
        - create_official_doc: Generar documento PV (opcional, default false)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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

    try:
        result = await cases.transfer_case(
            ctx=ctx,
            case_id=case_id,
            target_sector_id=target_sector_id,
            reason=reason,
            user_id=user_id,
            assigned_user_id=body.get("assigned_user_id"),
            create_official_doc=body.get("create_official_doc", False)
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        if "permiso" in str(e).lower() or "authorization" in str(e).lower():
            return _error_response("Sin permisos para transferir", status_code=403)
        logger.exception(f"[REST API] Error en transfer_case")
        return _error_response("Error interno del servidor", status_code=500)


async def api_link_document(request: Request) -> JSONResponse:
    """
    POST /api/v1/cases/{case_id}/documents/link

    Vincula un documento oficial a un expediente.

    Path params:
        - case_id: UUID del expediente

    Body JSON:
        - official_document_id: UUID del documento oficial (requerido)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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
        result = cases.link_document_to_case(
            ctx=ctx,
            case_id=case_id,
            official_document_id=official_document_id,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        error_msg = str(e).lower()
        if "permiso" in error_msg or "authorization" in error_msg:
            return _error_response("Sin permisos para vincular documento", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Expediente o documento no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en link_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_propose_document(request: Request) -> JSONResponse:
    """
    POST /api/v1/cases/{case_id}/documents/propose

    Propone un documento borrador para vincular a un expediente.

    Path params:
        - case_id: UUID del expediente

    Body JSON:
        - document_draft_id: UUID del documento borrador (requerido)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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
        result = cases.propose_document(
            ctx=ctx,
            case_id=case_id,
            document_draft_id=document_draft_id,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en propose_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_prepare_transfer(request: Request) -> JSONResponse:
    """
    GET /api/v1/cases/{case_id}/prepare-transfer

    Prepara informacion para transferencia de expediente.

    Path params:
        - case_id: UUID del expediente

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        result = cases.prepare_transfer(
            ctx=ctx,
            case_id=case_id,
            user_id=user_id
        )
        return _success_response(result)

    except Exception as e:
        error_msg = str(e).lower()
        if "permiso" in error_msg or "authorization" in error_msg:
            return _error_response("Sin permisos para transferir este expediente", status_code=403)
        logger.exception(f"[REST API] Error en prepare_transfer")
        return _error_response("Error interno del servidor", status_code=500)


async def api_accept_proposal(request: Request) -> JSONResponse:
    """
    POST /api/v1/cases/{case_id}/documents/accept-proposal

    Acepta un documento propuesto para vincularlo al expediente.

    Path params:
        - case_id: UUID del expediente

    Body JSON:
        - proposed_id: UUID de la propuesta (requerido)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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
        result = cases.accept_proposal(
            ctx=ctx,
            case_id=case_id,
            proposed_id=proposed_id,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except ValidationError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        error_msg = str(e).lower()
        if "permiso" in error_msg or "authorization" in error_msg:
            return _error_response("Sin permisos para aceptar propuesta", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Propuesta no encontrada", status_code=404)
        logger.exception(f"[REST API] Error en accept_proposal")
        return _error_response("Error interno del servidor", status_code=500)


async def api_reject_proposal(request: Request) -> JSONResponse:
    """
    POST /api/v1/cases/{case_id}/documents/reject-proposal

    Rechaza un documento propuesto (desactivar sin vincular).

    Path params:
        - case_id: UUID del expediente

    Body JSON:
        - proposed_id: UUID de la propuesta (requerido)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    case_id = request.path_params.get("case_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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
        result = cases.reject_proposal(
            ctx=ctx,
            case_id=case_id,
            proposed_id=proposed_id,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        error_msg = str(e).lower()
        if "permiso" in error_msg or "authorization" in error_msg:
            return _error_response("Sin permisos para rechazar propuesta", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Propuesta no encontrada", status_code=404)
        logger.exception(f"[REST API] Error en reject_proposal")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - SISTEMA (Catálogos adicionales)
# ============================================================================

async def api_get_sectors(request: Request) -> JSONResponse:
    """
    GET /api/v1/system/sectors

    Lista todos los sectores activos con sus departamentos.

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    try:
        result = system.get_sectors(ctx=ctx)
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en get_sectors")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_case_templates(request: Request) -> JSONResponse:
    """
    GET /api/v1/system/case-templates

    Lista plantillas de expedientes disponibles para el usuario.

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    try:
        result = system.get_case_templates(ctx=ctx, user_id=user_id)
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en get_case_templates")
        return _error_response("Error interno del servidor", status_code=500)


async def api_search_users(request: Request) -> JSONResponse:
    """
    GET /api/v1/system/users/search

    Busca usuarios para autocompletado.

    Query params:
        - q: Termino de busqueda (requerido, minimo 2 caracteres)
        - limit: Cantidad maxima de resultados (default 10)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    params = request.query_params
    q = params.get("q", "")
    limit = int(params.get("limit", 10))

    if not q or len(q) < 2:
        return _error_response("El parámetro 'q' es requerido y debe tener al menos 2 caracteres", status_code=400)

    try:
        result = system.search_users(ctx=ctx, search=q, limit=limit)
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en search_users")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - DASHBOARD
# ============================================================================

async def api_get_dashboard_stats(request: Request) -> JSONResponse:
    """
    GET /api/v1/dashboard/stats

    Obtiene estadisticas del dashboard para el usuario.

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    try:
        result = system.get_dashboard_stats(ctx=ctx, user_id=user_id)
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en get_dashboard_stats")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_dashboard_feed(request: Request) -> JSONResponse:
    """
    GET /api/v1/dashboard/feed

    Obtiene feed de actividad del dashboard para el usuario.

    Query params:
        - page: int (default 1)
        - page_size: int (default 20)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    params = request.query_params
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 20))

    try:
        result = system.get_dashboard_feed(
            ctx=ctx,
            user_id=user_id,
            page=page,
            page_size=page_size
        )
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en get_dashboard_feed")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - NOTAS
# ============================================================================

async def api_get_notes(request: Request) -> JSONResponse:
    """
    GET /api/v1/notes/received

    Obtiene notas recibidas del usuario.

    Query params:
        - page: int (default 1)
        - page_size: int (default 20)
        - unread_only: bool (default false)
        - search: str (opcional)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    params = request.query_params
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 20))
    unread_only = params.get("unread_only", "").lower() == "true"
    search = params.get("search")

    try:
        result = notes.get_notes(
            ctx=ctx,
            user_id=user_id,
            page=page,
            page_size=page_size,
            unread_only=unread_only,
            search=search
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_notes")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - EXPEDIENTES (Sector Users)
# ============================================================================

async def api_get_sector_users(request: Request) -> JSONResponse:
    """
    GET /api/v1/cases/sectors/{sector_id}/users

    Obtiene los usuarios de un sector.

    Path params:
        - sector_id: UUID del sector

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    sector_id = request.path_params.get("sector_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not sector_id:
        return _error_response("sector_id es requerido", status_code=400)

    try:
        result = cases.get_sector_users_list(ctx=ctx, sector_id=sector_id)
        return _success_response(result)

    except Exception as e:
        if "no encontrado" in str(e).lower() or "not found" in str(e).lower():
            return _error_response("Sector no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en get_sector_users")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - DOCUMENTOS (Busqueda por numero y detalles de firma)
# ============================================================================

async def api_search_document_by_number(request: Request) -> JSONResponse:
    """
    GET /api/v1/documents/search-official/{doc_number}

    Busca un documento oficial por su numero.

    Path params:
        - doc_number: Numero del documento oficial

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    doc_number = request.path_params.get("doc_number")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not doc_number:
        return _error_response("doc_number es requerido", status_code=400)

    try:
        result = documents.search_document_by_number(
            ctx=ctx,
            doc_number=doc_number,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        msg = str(e).lower()
        if "permisos" in msg:
            return _error_response(str(e), status_code=403)
        return _error_response(str(e), status_code=400)
    except Exception as e:
        if "no encontrado" in str(e).lower() or "not found" in str(e).lower():
            return _error_response("Documento no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en search_document_by_number")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_signature_details(request: Request) -> JSONResponse:
    """
    GET /api/v1/documents/{document_id}/signature-details

    Obtiene detalles de firma de un documento.

    Path params:
        - document_id: UUID del documento

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        result = await documents.get_signature_details(
            ctx=ctx,
            document_id=document_id,
            user_id=user_id
        )
        return _success_response(result)

    except Exception as e:
        if "no encontrado" in str(e).lower() or "not found" in str(e).lower():
            return _error_response("Documento no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en get_signature_details")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - DOCUMENTOS (Importacion)
# ============================================================================

async def api_import_document(request: Request) -> JSONResponse:
    """
    POST /api/v1/documents/import

    Importar documento PDF externo.

    Form data (multipart/form-data):
        - document_type_acronym: Acronimo del tipo de documento
        - reference: Referencia/descripcion del documento
        - pdf_file: Archivo PDF a importar

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    try:
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

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en import_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_replace_imported_pdf(request: Request) -> JSONResponse:
    """
    PUT /api/v1/documents/{document_id}/imported-pdf

    Reemplazar PDF de documento importado.

    Path params:
        - document_id: UUID del documento

    Form data (multipart/form-data):
        - pdf_file: Nuevo archivo PDF

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    document_id = request.path_params.get("document_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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
        logger.exception(f"[REST API] Error en replace_imported_pdf")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - SISTEMA (Permisos y Usuarios)
# ============================================================================

async def api_check_signer_permissions(request: Request) -> JSONResponse:
    """
    GET /api/v1/documents/check-signer-permissions

    Verifica permisos de firma de un usuario para un tipo de documento.

    Query params:
        - user_id: UUID del usuario a verificar
        - document_type_acronym: Acronimo del tipo de documento

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    auth_user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, auth_user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    params = request.query_params
    user_id_param = params.get("user_id")
    doc_type = params.get("document_type_acronym")

    if not user_id_param:
        return _error_response("user_id query param es requerido", status_code=400)
    if not doc_type:
        return _error_response("document_type_acronym query param es requerido", status_code=400)

    try:
        result = system.check_signer_permissions(
            ctx=ctx,
            user_id_to_check=user_id_param,
            document_type_acronym=doc_type
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        if "no encontrado" in str(e).lower() or "not found" in str(e).lower():
            return _error_response("Usuario o tipo de documento no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en check_signer_permissions")
        return _error_response("Error interno del servidor", status_code=500)


async def api_list_all_users(request: Request) -> JSONResponse:
    """
    GET /api/v1/system/users/list

    Lista todos los usuarios del tenant.

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    try:
        result = system.list_all_users(ctx=ctx)
        return _success_response(result)

    except Exception as e:
        logger.exception(f"[REST API] Error en list_all_users")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# NUEVOS ENDPOINTS - NOTAS (Enviadas, Archivadas, Detalle, Archivar)
# ============================================================================

async def api_get_sent_notes(request: Request) -> JSONResponse:
    """
    GET /api/v1/notes/sent

    Obtiene notas enviadas por el usuario.

    Query params:
        - page: int (default 1)
        - page_size: int (default 20)
        - search: str (opcional)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    params = request.query_params
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 20))
    search = params.get("search")

    try:
        result = notes.get_sent_notes(
            ctx=ctx,
            user_id=user_id,
            page=page,
            page_size=page_size,
            search=search
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_sent_notes")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_archived_notes(request: Request) -> JSONResponse:
    """
    GET /api/v1/notes/archived

    Obtiene notas archivadas del usuario.

    Query params:
        - page: int (default 1)
        - page_size: int (default 20)
        - search: str (opcional)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    params = request.query_params
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 20))
    search = params.get("search")

    try:
        result = notes.get_archived_notes(
            ctx=ctx,
            user_id=user_id,
            page=page,
            page_size=page_size,
            search=search
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception(f"[REST API] Error en get_archived_notes")
        return _error_response("Error interno del servidor", status_code=500)


async def api_archive_note(request: Request) -> JSONResponse:
    """
    PATCH /api/v1/notes/{note_id}/archive

    Archiva o desarchiva una nota.

    Path params:
        - note_id: UUID de la nota

    Body JSON:
        - archived: bool (true para archivar, false para desarchivar)
        - sector_id: UUID del sector

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    note_id = request.path_params.get("note_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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

    try:
        from services.notes import toggle_note_archive

        result = toggle_note_archive(
            document_id=note_id,
            sector_id=sector_id,
            archived=archived,
            schema_name=ctx.schema_name
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        error_msg = str(e).lower()
        if "permiso" in error_msg or "authorization" in error_msg:
            return _error_response("Sin permisos para archivar esta nota", status_code=403)
        if "no encontrado" in error_msg or "not found" in error_msg:
            return _error_response("Nota no encontrada", status_code=404)
        logger.exception(f"[REST API] Error en archive_note")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_note_detail(request: Request) -> JSONResponse:
    """
    GET /api/v1/notes/{note_id}

    Obtiene detalle de una nota.

    Path params:
        - note_id: UUID de la nota

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    note_id = request.path_params.get("note_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not note_id:
        return _error_response("note_id es requerido", status_code=400)

    try:
        result = notes.get_note_detail(
            ctx=ctx,
            note_id=note_id,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except Exception as e:
        if "no encontrado" in str(e).lower() or "not found" in str(e).lower():
            return _error_response("Nota no encontrada", status_code=404)
        logger.exception(f"[REST API] Error en get_note_detail")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# MEMOS ENDPOINTS
# ============================================================================


async def api_get_memos(request: Request) -> JSONResponse:
    """GET /api/v1/memos/received - Obtiene memos recibidos del usuario."""
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    params = request.query_params
    try:
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 20))
    except (ValueError, TypeError):
        return _error_response("page y page_size deben ser numeros enteros", status_code=400)
    search = params.get("search")

    try:
        result = memos.get_memos(
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
    """GET /api/v1/memos/sent - Obtiene memos enviados por el usuario."""
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    params = request.query_params
    try:
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 20))
    except (ValueError, TypeError):
        return _error_response("page y page_size deben ser numeros enteros", status_code=400)
    search = params.get("search")

    try:
        result = memos.get_sent_memos_tool(
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
    """GET /api/v1/memos/archived - Obtiene memos archivados del usuario."""
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not user_id:
        return _error_response("X-User-ID es requerido", status_code=401)

    params = request.query_params
    try:
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 20))
    except (ValueError, TypeError):
        return _error_response("page y page_size deben ser numeros enteros", status_code=400)
    search = params.get("search")

    try:
        result = memos.get_archived_memos_tool(
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


async def api_get_memo_detail(request: Request) -> JSONResponse:
    """GET /api/v1/memos/{memo_id} - Obtiene detalle de un memo."""
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)
    memo_id = request.path_params.get("memo_id")

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    if not memo_id:
        return _error_response("memo_id es requerido", status_code=400)

    try:
        result = memos.get_memo_detail(
            ctx=ctx,
            memo_id=memo_id,
            user_id=user_id
        )
        return _success_response(result)

    except ValueError as e:
        return _error_response(str(e), status_code=400)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except Exception as e:
        if "no encontrado" in str(e).lower() or "not found" in str(e).lower():
            return _error_response("Memo no encontrado", status_code=404)
        logger.exception(f"[REST API] Error en get_memo_detail")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# BACKUP SYNC ENDPOINTS
# ============================================================================

def _backup_error_response(e: BackupAuthError) -> JSONResponse:
    """Convierte BackupAuthError en JSONResponse con Retry-After si aplica."""
    headers = {}
    if e.retry_after:
        headers["Retry-After"] = str(e.retry_after)
    return JSONResponse({"error": e.message}, status_code=e.status_code, headers=headers)


async def api_sync_schema(request: Request) -> JSONResponse:
    """GET /api/v1/sync/schema — Catálogo de tablas sincronizables."""
    try:
        backup_ctx = validate_backup_api_key(request)
    except BackupAuthError as e:
        return _backup_error_response(e)

    result = sync.get_sync_catalog(schema_name=backup_ctx["schema_name"])
    return _success_response(result)


async def api_sync_data(request: Request) -> JSONResponse:
    """GET /api/v1/sync/data — Datos incrementales de una tabla (Backup)."""
    try:
        backup_ctx = validate_backup_api_key(request)
    except BackupAuthError as e:
        return _backup_error_response(e)

    # Validar params ANTES del rate limit (no gastar rate limit en requests inválidos)
    table = request.query_params.get("table")
    since = request.query_params.get("since")
    if not table or not since:
        return _error_response("Parámetros 'table' y 'since' son requeridos", 400)

    # Validar tabla en whitelist
    from api_gateway.tools.sync import SYNC_TABLES
    if table not in SYNC_TABLES:
        return _error_response(f"Tabla '{table}' no es sincronizable", 400)

    # Validar since como ISO 8601
    try:
        datetime.fromisoformat(since.replace('Z', '+00:00'))
    except ValueError:
        return _error_response("Formato de 'since' inválido. Use ISO 8601.", 400)

    # Validar page/page_size
    try:
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 100)), 100)
    except (ValueError, TypeError):
        return _error_response("page y page_size deben ser enteros", 400)

    # Rate limit atómico DESPUÉS de validar params (no gastar rate limit en requests inválidos)
    rate = backup_ctx.get("rate_limit_per_minute") or 1
    retry_after = check_and_log_sync_access(
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
        result = sync.get_sync_data(table, since, page, page_size, schema_name=backup_ctx["schema_name"])
    except ValueError as e:
        return _error_response(str(e), 400)

    return _success_response(result)


# ============================================================================
# RECORDS (RLM) ENDPOINTS
# ============================================================================

async def api_search_records(request: Request) -> JSONResponse:
    """
    GET /api/v1/records/search

    Query params:
        - page: int (default 1)
        - page_size: int (default 20, max 100)
        - family_code: str (ARQ, LUM, ORD)
        - search: str (buscar por número o datos)
        - state: str (filtro por estado)

    Response includes: record_number, display_name, state, resume (resumen IA)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    params = request.query_params

    try:
        result = records.search_records(
            ctx=ctx,
            family_code=params.get("family_code"),
            search=params.get("search"),
            state=params.get("state"),
            page=int(params.get("page", 1)),
            page_size=int(params.get("page_size", 20)),
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en search_records")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# SEMANTIC SEARCH ENDPOINT
# ============================================================================

async def api_semantic_search(request: Request) -> JSONResponse:
    """
    GET /api/v1/search/semantic

    Query params:
        - query: str (required, min 3 chars) - texto de busqueda
        - limit: int (default 6, max 50) - max resultados

    Response includes: documentos similares con vinculaciones a expedientes y legajos.

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    params = request.query_params
    query = params.get("query", "").strip()

    if not query or len(query) < 3:
        return _error_response("query param requerido (min 3 caracteres)", status_code=400)

    try:
        limit = min(int(params.get("limit", 6)), 50)
    except (ValueError, TypeError):
        limit = 6

    try:
        result = search.semantic_search_tool(
            ctx=ctx,
            query=query,
            limit=limit,
        )
        return _success_response(result)

    except Exception as e:
        logger.exception("[REST API] Error en semantic_search")
        return _error_response("Busqueda temporalmente no disponible", status_code=503)


async def api_get_record(request: Request) -> JSONResponse:
    """
    GET /api/v1/records/{record_id}

    Response includes: datos enriquecidos, estado, registro, permisos, resume (resumen IA)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")

    try:
        result = records.get_record_detail(
            ctx=ctx,
            record_id=record_id,
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en get_record")
        return _error_response("Error interno del servidor", status_code=500)


async def api_create_record(request: Request) -> JSONResponse:
    """
    POST /api/v1/records

    Body JSON:
        - registry_code: str (ARQ, LUM, ORD) - REQUIRED
        - data: dict (campos enriquecidos) - optional

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

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

    try:
        from services.rlm.records import create_record
        result = create_record(
            registry_code=registry_code,
            data=body.get("data") or {},
            display_name=display_name,
            user_id=ctx.user_id,
            schema_name=ctx.schema_name,
        )

        # Encolar generación de resumen IA (async, non-blocking)
        try:
            from services.shared.resume_trigger import enqueue_record_resume_fire_and_forget
            enqueue_record_resume_fire_and_forget(result['id'], ctx.schema_name)
        except Exception:
            pass

        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en create_record")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_registry_families(request: Request) -> JSONResponse:
    """
    GET /api/v1/registries

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    try:
        result = records.get_registry_families(ctx=ctx)
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en get_registry_families")
        return _error_response("Error interno del servidor", status_code=500)


# ============================================================================
# RLM - ENDPOINTS ADICIONALES (Fase 2.6)
# ============================================================================

async def api_update_record(request: Request) -> JSONResponse:
    """
    PATCH /api/v1/records/{record_id}

    Body JSON:
        - state: str - optional (at least one of state/display_name required)
        - display_name: str - optional
        - reason: str - optional

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    state = body.get("state")
    display_name = body.get("display_name")
    if not state and not display_name:
        return _error_response("Debe enviar al menos 'state' o 'display_name'", status_code=400)

    try:
        from services.rlm.records import update_record
        result = update_record(
            record_id=record_id,
            user_id=ctx.user_id,
            schema_name=ctx.schema_name,
            new_state=state,
            new_display_name=display_name,
            reason=body.get("reason"),
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en update_record")
        return _error_response("Error interno del servidor", status_code=500)


async def api_update_record_field(request: Request) -> JSONResponse:
    """
    PATCH /api/v1/records/{record_id}/fields/{field_name}

    Body JSON:
        - value: any - optional
        - expiration_date: str - optional
        - document_id: str - optional
        - notes: str - optional

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")
    field_name = request.path_params.get("field_name", "")

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    try:
        from services.rlm.fields import update_field
        result = update_field(
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
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en update_record_field")
        return _error_response("Error interno del servidor", status_code=500)


async def api_verify_record_field(request: Request) -> JSONResponse:
    """
    POST /api/v1/records/{record_id}/fields/{field_name}/verify

    Body JSON:
        - document_id: str - required (ID del documento oficial de respaldo)
        - notes: str - optional

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")
    field_name = request.path_params.get("field_name", "")

    try:
        body = await request.json()
    except Exception:
        body = {}

    document_id = body.get("document_id")
    if not document_id:
        return _error_response("document_id es obligatorio para verificar un campo", status_code=400)

    try:
        from services.rlm.fields import verify_field
        result = verify_field(
            record_id=record_id,
            field_name=field_name,
            user_id=ctx.user_id,
            document_id=document_id,
            notes=body.get("notes"),
            schema_name=ctx.schema_name,
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en verify_record_field")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_record_history(request: Request) -> JSONResponse:
    """
    GET /api/v1/records/{record_id}/history

    Query params:
        - page: int (default 1)
        - page_size: int (default 20)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")
    params = request.query_params

    try:
        from services.rlm.history import get_history
        result = get_history(
            record_id=record_id,
            user_id=ctx.user_id,
            schema_name=ctx.schema_name,
            page=int(params.get("page", 1)),
            page_size=int(params.get("page_size", 20)),
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en get_record_history")
        return _error_response("Error interno del servidor", status_code=500)


async def api_generate_record_report(request: Request) -> JSONResponse:
    """
    POST /api/v1/records/{record_id}/report

    Genera un informe IFRLM del legajo.
    Crea un documento tipo IFRLM con snapshot actual, lo vincula al legajo
    y registra en historial.

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")

    try:
        from services.rlm.report import generate_ifrlm
        result = await generate_ifrlm(
            record_id=record_id,
            user_id=ctx.user_id,
            schema_name=ctx.schema_name,
            is_initial=False,
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en generate_record_report")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_record_relations(request: Request) -> JSONResponse:
    """
    GET /api/v1/records/{record_id}/relations

    Query params:
        - page: int (default 1)
        - page_size: int (default 20)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")
    params = request.query_params

    try:
        from services.rlm.relations import get_relations
        result = get_relations(
            record_id,
            ctx.user_id,
            schema_name=ctx.schema_name,
            page=int(params.get("page", 1)),
            page_size=int(params.get("page_size", 20)),
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en get_record_relations")
        return _error_response("Error interno del servidor", status_code=500)


async def api_create_record_relation(request: Request) -> JSONResponse:
    """
    POST /api/v1/records/{record_id}/relations

    Body JSON:
        - target_record_id: str - REQUIRED
        - relation_type: str - REQUIRED (parent, child, related, replaces)
        - notes: str - optional

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    target_record_id = body.get("target_record_id")
    relation_type = body.get("relation_type")
    if not target_record_id or not relation_type:
        return _error_response("target_record_id y relation_type son requeridos", status_code=400)

    try:
        from services.rlm.relations import create_relation
        result = create_relation(
            record_id=record_id,
            target_record_id=target_record_id,
            relation_type=relation_type,
            user_id=ctx.user_id,
            notes=body.get("notes"),
            schema_name=ctx.schema_name,
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en create_record_relation")
        return _error_response("Error interno del servidor", status_code=500)


async def api_delete_record_relation(request: Request) -> JSONResponse:
    """
    DELETE /api/v1/records/{record_id}/relations/{relation_id}

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")
    relation_id = request.path_params.get("relation_id", "")

    try:
        from services.rlm.relations import delete_relation
        result = delete_relation(
            record_id=record_id,
            relation_id=relation_id,
            user_id=ctx.user_id,
            schema_name=ctx.schema_name,
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en delete_record_relation")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_record_cases(request: Request) -> JSONResponse:
    """
    GET /api/v1/records/{record_id}/cases

    Query params:
        - page: int (default 1)
        - page_size: int (default 20)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")
    params = request.query_params

    try:
        from services.rlm.links import get_linked_cases
        result = get_linked_cases(
            record_id,
            ctx.user_id,
            schema_name=ctx.schema_name,
            page=int(params.get("page", 1)),
            page_size=int(params.get("page_size", 20)),
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en get_record_cases")
        return _error_response("Error interno del servidor", status_code=500)


async def api_link_record_case(request: Request) -> JSONResponse:
    """
    POST /api/v1/records/{record_id}/cases

    Body JSON:
        - case_id: str - REQUIRED
        - notes: str - optional

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    case_id = body.get("case_id")
    if not case_id:
        return _error_response("case_id es requerido", status_code=400)

    try:
        from services.rlm.links import link_case
        result = link_case(
            record_id=record_id,
            case_id=case_id,
            user_id=ctx.user_id,
            notes=body.get("notes"),
            schema_name=ctx.schema_name,
        )
        return _success_response(result)

    except ConflictError as e:
        return _error_response(str(e), status_code=409)
    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en link_record_case")
        return _error_response("Error interno del servidor", status_code=500)


async def api_unlink_record_case(request: Request) -> JSONResponse:
    """
    DELETE /api/v1/records/{record_id}/cases/{link_id}

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")
    link_id = request.path_params.get("link_id", "")

    try:
        from services.rlm.links import unlink_case
        result = unlink_case(
            record_id=record_id,
            link_id=link_id,
            user_id=ctx.user_id,
            schema_name=ctx.schema_name,
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en unlink_record_case")
        return _error_response("Error interno del servidor", status_code=500)


async def api_get_record_documents(request: Request) -> JSONResponse:
    """
    GET /api/v1/records/{record_id}/documents

    Query params:
        - page: int (default 1)
        - page_size: int (default 20)

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")
    params = request.query_params

    try:
        from services.rlm.links import get_linked_documents
        result = get_linked_documents(
            record_id,
            ctx.user_id,
            schema_name=ctx.schema_name,
            page=int(params.get("page", 1)),
            page_size=int(params.get("page_size", 20)),
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en get_record_documents")
        return _error_response("Error interno del servidor", status_code=500)


async def api_link_record_document(request: Request) -> JSONResponse:
    """
    POST /api/v1/records/{record_id}/documents

    Body JSON:
        - document_id: str - REQUIRED
        - field_name: str - optional
        - notes: str - optional

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    document_id = body.get("document_id")
    if not document_id:
        return _error_response("document_id es requerido", status_code=400)

    try:
        from services.rlm.links import link_document
        result = link_document(
            record_id=record_id,
            document_id=document_id,
            user_id=ctx.user_id,
            field_name=body.get("field_name"),
            notes=body.get("notes"),
            schema_name=ctx.schema_name,
        )
        return _success_response(result)

    except ConflictError as e:
        return _error_response(str(e), status_code=409)
    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en link_record_document")
        return _error_response("Error interno del servidor", status_code=500)


async def api_unlink_record_document(request: Request) -> JSONResponse:
    """
    DELETE /api/v1/records/{record_id}/documents/{link_id}

    Headers:
        - X-API-Key: required
        - X-User-ID: required
    """
    api_key = _get_api_key(request)
    user_id = _get_user_id(request)

    try:
        ctx = validate_rest_api_key(api_key, user_id)
    except ValueError as e:
        return _error_response(str(e), status_code=401)

    record_id = request.path_params.get("record_id", "")
    link_id = request.path_params.get("link_id", "")

    try:
        from services.rlm.links import unlink_document
        result = unlink_document(
            record_id=record_id,
            link_id=link_id,
            user_id=ctx.user_id,
            schema_name=ctx.schema_name,
        )
        return _success_response(result)

    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except AuthorizationError as e:
        return _error_response(str(e), status_code=403)
    except (ValidationError, ValueError) as e:
        return _error_response(str(e), status_code=400)
    except Exception as e:
        logger.exception("[REST API] Error en unlink_record_document")
        return _error_response("Error interno del servidor", status_code=500)
