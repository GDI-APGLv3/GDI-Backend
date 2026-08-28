import json
import re
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse

from shared.logging import get_logger
from shared.exceptions import ValidationError, DocumentStateError, AuthorizationError, NotFoundError
from api_gateway.rest_common import _success_response, _error_response
from api_gateway.auth_rest import validate_tad_api_key, TadAuthError
from api_gateway.rate_limiter import rate_limiter, get_client_ip, RateLimitExceeded
from database import fetch_all, fetch_one
from services.citizens import idempotency
from services.citizens.service import upsert_citizen, get_citizen, set_citizen_estado

logger = get_logger(__name__)

TAD_CITIZENS_GET_STRICT_LIMIT = 10

TAD_CITIZENS_STRICT_LIMIT_PER_IP = 30

TAD_CITIZENS_404_ALERT_THRESHOLD = 50
TAD_CITIZENS_404_ALERT_WINDOW_SECONDS = 3600

TAD_CITIZENS_POST_STRICT_LIMIT = 5

_GENERIC_CITIZEN_404 = "Ciudadano no encontrado"

_GENERIC_DOCUMENT_404 = "Documento no encontrado"


def _is_uuid_ref(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def _authenticate_tad(request: Request, *, strict_rate_limit: int = None):
    api_key = request.headers.get("X-API-Key")
    try:
        schema_name, _citizen = await validate_tad_api_key(
            api_key, strict_rate_limit=strict_rate_limit,
        )
    except TadAuthError as e:
        return None, _error_response(e.message, status_code=e.status_code)

    if strict_rate_limit is not None:
        rate_limiter.check(
            f"tad_ip:{get_client_ip(request)}:strict",
            TAD_CITIZENS_STRICT_LIMIT_PER_IP,
        )
    return schema_name, None


def _contar_404_ciudadano(request: Request) -> None:
    ip = get_client_ip(request)
    key = f"tad_404:{ip}"
    try:
        rate_limiter.check(
            key,
            TAD_CITIZENS_404_ALERT_THRESHOLD,
            window_seconds=TAD_CITIZENS_404_ALERT_WINDOW_SECONDS,
        )
    except RateLimitExceeded:
        logger.warning(
            f"[TAD][ALERTA] posible enumeracion de CUILs: mas de "
            f"{TAD_CITIZENS_404_ALERT_THRESHOLD} consultas sin resultado en la "
            f"ultima hora desde ip={ip}"
        )


async def api_tad_create_citizen(request: Request) -> JSONResponse:
    schema_name, err = await _authenticate_tad(
        request, strict_rate_limit=TAD_CITIZENS_POST_STRICT_LIMIT,
    )
    if err:
        return err

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    full_name = body.get("full_name")
    country_id = body.get("country_id")
    estado = body.get("estado", "pendiente")

    try:
        citizen = await upsert_citizen(full_name, country_id, estado, schema_name=schema_name)
        safe_citizen = {
            k: v for k, v in citizen.items()
            if k not in ("created_at", "updated_at", "validated_at", "validated_by")
        }
        return _success_response(safe_citizen)
    except ValidationError as e:
        return _error_response(e.message, status_code=400)
    except Exception:
        logger.exception("[TAD] Error en api_tad_create_citizen")
        return _error_response("Error interno del servidor", status_code=500)


async def api_tad_get_citizen(request: Request) -> JSONResponse:
    schema_name, err = await _authenticate_tad(
        request, strict_rate_limit=TAD_CITIZENS_GET_STRICT_LIMIT,
    )
    if err:
        return err

    ref = request.path_params.get("id_or_cuil", "")
    try:
        citizen = await get_citizen(ref, schema_name=schema_name)
        if citizen is None:
            _contar_404_ciudadano(request)
            return _error_response(_GENERIC_CITIZEN_404, status_code=404)
        return _success_response(citizen)
    except Exception:
        logger.exception("[TAD] Error en api_tad_get_citizen")
        return _error_response("Error interno del servidor", status_code=500)


async def api_tad_patch_citizen(request: Request) -> JSONResponse:
    schema_name, err = await _authenticate_tad(request)
    if err:
        return err

    ref = request.path_params.get("id", "")
    try:
        resolved = await get_citizen(ref, schema_name=schema_name)
    except Exception:
        logger.exception("[TAD] Error resolviendo ciudadano en api_tad_patch_citizen")
        return _error_response("Error interno del servidor", status_code=500)
    if resolved is None:
        return _error_response(_GENERIC_CITIZEN_404, status_code=404)
    citizen_id = resolved["id"]

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    extra_fields = set(body.keys()) - {"estado"}
    if extra_fields:
        return _error_response(
            f"Campos no editables: {sorted(extra_fields)}. Solo se puede cambiar 'estado'.",
            status_code=400,
        )

    estado = body.get("estado")
    if not estado:
        return _error_response("estado es requerido", status_code=400)

    try:
        citizen = await set_citizen_estado(citizen_id, estado, schema_name=schema_name)
        if citizen is None:
            return _error_response(_GENERIC_CITIZEN_404, status_code=404)
        return _success_response(citizen)
    except ValidationError as e:
        return _error_response(e.message, status_code=400)
    except Exception:
        logger.exception("[TAD] Error en api_tad_patch_citizen")
        return _error_response("Error interno del servidor", status_code=500)


async def api_tad_get_document_types(request: Request) -> JSONResponse:
    schema_name, err = await _authenticate_tad(request)
    if err:
        return err

    try:
        rows = await fetch_all(
            """
            SELECT dt.id, dt.name, dt.acronym, dt.description,
                   EXISTS(
                       SELECT 1 FROM document_type_fields dtf
                       WHERE dtf.document_type_id = dt.id
                   ) AS has_fields
            FROM document_types dt
            WHERE dt.external_signable = true AND dt.is_active = true
              AND dt.type IN ('HTML', 'Importado')
            ORDER BY dt.name
            """,
            schema_name=schema_name,
        )
        return _success_response({"document_types": [dict(r) for r in rows]})
    except Exception:
        logger.exception("[TAD] Error en api_tad_get_document_types")
        return _error_response("Error interno del servidor", status_code=500)


async def api_tad_get_document_type_fields(request: Request) -> JSONResponse:
    schema_name, err = await _authenticate_tad(request)
    if err:
        return err

    raw_id = request.path_params.get("id", "")
    try:
        document_type_id = int(raw_id)
    except (ValueError, TypeError):
        return _error_response("Tipo de documento no encontrado", status_code=404)

    try:
        doc_type = await fetch_one(
            """
            SELECT id FROM document_types
            WHERE id = $1 AND external_signable = true AND is_active = true
            """,
            document_type_id,
            schema_name=schema_name,
        )
        if not doc_type:
            return _error_response("Tipo de documento no encontrado", status_code=404)

        fields_row = await fetch_one(
            "SELECT field_definitions FROM document_type_fields WHERE document_type_id = $1",
            document_type_id,
            schema_name=schema_name,
        )
        if fields_row is None:
            return _error_response("Tipo de documento no encontrado", status_code=404)

        return _success_response({
            "document_type_id": document_type_id,
            "field_definitions": fields_row["field_definitions"],
        })
    except Exception:
        logger.exception("[TAD] Error en api_tad_get_document_type_fields")
        return _error_response("Error interno del servidor", status_code=500)


async def api_tad_webhook_test(request: Request) -> JSONResponse:
    schema_name, err = await _authenticate_tad(request)
    if err:
        return err

    try:
        from services.webhooks.tad_notify import send_test_webhook
        result = await send_test_webhook(schema_name=schema_name)
        return _success_response(result)
    except ValueError as e:
        return _error_response(str(e), status_code=422)
    except Exception:
        logger.exception("[TAD] Error en api_tad_webhook_test")
        return _error_response("Error interno del servidor", status_code=500)


async def api_tad_create_document(request: Request) -> JSONResponse:
    schema_name, citizen, err = await _authenticate_tad_citizen(request, require_validado=True)
    if err:
        return err

    try:
        body_bytes = await request.body()
        body = json.loads(body_bytes)
        if not isinstance(body, dict):
            raise ValueError("el cuerpo debe ser un objeto JSON")
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    document_type_acronym = body.get("document_type_acronym")
    reference = body.get("reference")
    if not document_type_acronym:
        return _error_response("document_type_acronym es requerido", status_code=400)
    if not reference:
        return _error_response("reference es requerido", status_code=400)

    try:
        idem_key = idempotency.validate_key(request.headers.get("Idempotency-Key"))
    except ValueError as e:
        return _error_response(str(e), status_code=400)

    idem_api_key_id = None
    if idem_key:
        idem_api_key_id = await idempotency.resolve_api_key_id(request.headers.get("X-API-Key"))
        if not idem_api_key_id:
            logger.warning(
                "[TAD] Idempotency-Key sin api_key_id resoluble (schema=%s) — se procesa sin idempotencia",
                schema_name,
            )
        else:
            decision = await idempotency.begin(
                api_key_id=idem_api_key_id,
                key=idem_key,
                schema_name=schema_name,
                citizen_id=citizen["id"],
                request_fingerprint=idempotency.fingerprint(body_bytes),
            )
            if decision.outcome is idempotency.IdempotencyOutcome.REPLAY:
                return _success_response(
                    decision.response, status_code=202,
                    headers={"Idempotent-Replay": "true"},
                )
            if decision.outcome is idempotency.IdempotencyOutcome.CONFLICT:
                return _error_response(decision.message, status_code=409)

    try:
        from services.documents.signing.citizen_signing import create_and_sign_citizen_document
        result = await create_and_sign_citizen_document(
            document_type_acronym, citizen["id"], reference, schema_name=schema_name,
            content_html=body.get("content_html"),
            pdf_base64=body.get("pdf_base64"),
            embedded_files=body.get("embedded_files"),
            form_data=body.get("form_data"),
        )
        if idem_api_key_id:
            await idempotency.complete(
                api_key_id=idem_api_key_id, key=idem_key,
                document_id=result["document_id"], response=result,
            )
        return _success_response(result, status_code=202)
    except ValidationError as e:
        await _release_idem(idem_api_key_id, idem_key)
        message = e.message
        status_code = 503 if "numerator_citizen" in message or "migracion" in message.lower() else 400
        return _error_response(message, status_code=status_code)
    except (DocumentStateError, AuthorizationError) as e:
        await _release_idem(idem_api_key_id, idem_key)
        return _error_response(str(e), status_code=409 if isinstance(e, DocumentStateError) else 403)
    except NotFoundError as e:
        await _release_idem(idem_api_key_id, idem_key)
        return _error_response(str(e), status_code=404)
    except Exception:
        await _release_idem(idem_api_key_id, idem_key)
        logger.exception("[TAD] Error en api_tad_create_document")
        return _error_response("Error interno del servidor", status_code=500)


async def _release_idem(api_key_id: str | None, key: str | None) -> None:
    if api_key_id and key:
        await idempotency.release(api_key_id=api_key_id, key=key)


async def api_tad_get_document(request: Request) -> JSONResponse:
    schema_name, citizen, err = await _authenticate_tad_citizen(request)
    if err:
        return err

    document_id = request.path_params.get("id", "")
    if not _is_uuid_ref(document_id):
        return _error_response(_GENERIC_DOCUMENT_404, status_code=404)

    try:
        from services.citizens.document_status import get_citizen_document_status
        estado = await get_citizen_document_status(
            document_id, citizen["id"], schema_name=schema_name,
        )
        if estado is None:
            return _error_response(_GENERIC_DOCUMENT_404, status_code=404)
        return _success_response(estado)
    except Exception:
        logger.exception("[TAD] Error en api_tad_get_document")
        return _error_response("Error interno del servidor", status_code=500)


async def _authenticate_tad_citizen(request: Request, *, require_validado: bool = False):
    api_key = request.headers.get("X-API-Key")
    citizen_ref = request.headers.get("X-Citizen-ID")
    if not citizen_ref:
        return None, None, _error_response("X-Citizen-ID header requerido", status_code=400)

    if not (_is_uuid_ref(citizen_ref) or re.fullmatch(r"\d{11}", citizen_ref)):
        return None, None, _error_response("X-Citizen-ID con formato invalido", status_code=401)

    try:
        schema_name, citizen = await validate_tad_api_key(api_key, citizen_ref)
    except TadAuthError as e:
        return None, None, _error_response(e.message, status_code=e.status_code)

    if citizen is None:
        return None, None, _error_response(_GENERIC_CITIZEN_404, status_code=404)
    if require_validado and citizen.get("estado") != "validado":
        return None, None, _error_response(
            f"El ciudadano debe estar 'validado' para esta operacion (estado actual: {citizen.get('estado')})",
            status_code=403,
        )
    return schema_name, citizen, None


async def api_tad_get_case_templates(request: Request) -> JSONResponse:
    schema_name, err = await _authenticate_tad(request)
    if err:
        return err

    try:
        rows = await fetch_all(
            """
            SELECT id, type_name, acronym, description
            FROM case_templates
            WHERE creation_channel IN ('api', 'both') AND is_active = true
            ORDER BY type_name
            """,
            schema_name=schema_name,
        )
        return _success_response({"case_templates": [dict(r) for r in rows]})
    except Exception:
        logger.exception("[TAD] Error en api_tad_get_case_templates")
        return _error_response("Error interno del servidor", status_code=500)


async def api_tad_create_case(request: Request) -> JSONResponse:
    schema_name, citizen, err = await _authenticate_tad_citizen(request, require_validado=True)
    if err:
        return err

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    case_template_id = body.get("case_template_id")
    reference = body.get("reference")
    if not case_template_id:
        return _error_response("case_template_id es requerido", status_code=400)
    if not reference:
        return _error_response("reference es requerido", status_code=400)

    try:
        from services.cases.creation import create_case_with_cover_service
        result = await create_case_with_cover_service(
            case_template_id, reference, citizen_id=citizen["id"], schema_name=schema_name,
        )
        return _success_response({
            "case_id": result["case"]["case_id"],
            "case_number": result["case"]["case_number"],
            "official_number": result["cover"]["official_number"],
        })
    except ValidationError as e:
        return _error_response(e.message, status_code=400)
    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except Exception:
        logger.exception("[TAD] Error en api_tad_create_case")
        return _error_response("Error interno del servidor", status_code=500)


async def api_tad_get_cases(request: Request) -> JSONResponse:
    schema_name, citizen, err = await _authenticate_tad_citizen(request)
    if err:
        return err

    try:
        from services.cases.citizen_shares import list_cases_shared_with_citizen
        cases = await list_cases_shared_with_citizen(citizen["id"], schema_name=schema_name)
        return _success_response({"cases": cases})
    except Exception:
        logger.exception("[TAD] Error en api_tad_get_cases")
        return _error_response("Error interno del servidor", status_code=500)


async def api_tad_get_case_detail(request: Request) -> JSONResponse:
    schema_name, citizen, err = await _authenticate_tad_citizen(request)
    if err:
        return err

    case_id = request.path_params.get("id", "")
    try:
        uuid.UUID(case_id)
    except (ValueError, AttributeError, TypeError):
        return _error_response("Expediente no encontrado", status_code=404)

    try:
        from services.cases.citizen_shares import can_citizen_access_case
        if not await can_citizen_access_case(case_id, citizen["id"], schema_name=schema_name):
            return _error_response("Expediente no encontrado", status_code=404)

        case_row = await fetch_one(
            """
            SELECT c.id, c.case_number, c.reference, c.status, ct.type_name, ct.acronym
            FROM cases c
            JOIN case_templates ct ON c.case_template_id = ct.id
            WHERE c.id = $1
            """,
            case_id,
            schema_name=schema_name,
        )
        if not case_row:
            return _error_response("Expediente no encontrado", status_code=404)

        from services.cases.documents import get_case_documents
        docs = await get_case_documents(case_id, schema_name=schema_name)

        visible_documents = [
            doc for doc in docs["official"]
            if not doc.get("is_reserved", False) and doc.get("is_active", True)
        ]

        return _success_response({
            "case_id": str(case_row["id"]),
            "case_number": case_row["case_number"],
            "reference": case_row["reference"],
            "status": case_row["status"],
            "template_name": case_row["type_name"],
            "template_acronym": case_row["acronym"],
            "documents": visible_documents,
        })
    except Exception:
        logger.exception("[TAD] Error en api_tad_get_case_detail")
        return _error_response("Error interno del servidor", status_code=500)


async def api_tad_propose_document(request: Request) -> JSONResponse:
    schema_name, citizen, err = await _authenticate_tad_citizen(request)
    if err:
        return err

    case_id = request.path_params.get("id", "")
    try:
        uuid.UUID(case_id)
    except (ValueError, AttributeError, TypeError):
        return _error_response("Expediente no encontrado", status_code=404)

    try:
        body = await request.json()
    except Exception:
        return _error_response("Body JSON invalido", status_code=400)

    document_id = body.get("document_id")
    if not document_id:
        return _error_response("document_id es requerido", status_code=400)
    try:
        uuid.UUID(str(document_id))
    except (ValueError, AttributeError, TypeError):
        return _error_response("Documento no encontrado", status_code=404)

    try:
        from services.cases.citizen_shares import can_citizen_access_case
        if not await can_citizen_access_case(case_id, citizen["id"], schema_name=schema_name):
            return _error_response("Expediente no encontrado", status_code=404)

        doc_row = await fetch_one(
            "SELECT id, status, created_by_citizen FROM document_draft WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )
        if not doc_row:
            return _error_response("Documento no encontrado", status_code=404)
        if str(doc_row["created_by_citizen"]) != citizen["id"]:
            return _error_response("Documento no encontrado", status_code=404)
        if doc_row["status"] != "signed":
            return _error_response(
                f"El documento debe estar firmado para proponerlo (estado actual: {doc_row['status']})",
                status_code=409,
            )

        existing = await fetch_one(
            """
            SELECT id FROM case_proposed_documents
            WHERE case_id = $1 AND document_draft_id = $2 AND is_active = true
            """,
            case_id, document_id,
            schema_name=schema_name,
        )
        if existing:
            return _error_response(
                "El documento ya fue propuesto a este expediente y la propuesta sigue pendiente",
                status_code=409,
            )

        from services.cases.documents import propose_document_to_case
        result = await propose_document_to_case(
            case_id, document_id, schema_name=schema_name,
            auth_source="tad", proposing_citizen_id=citizen["id"],
        )
        return _success_response(result)
    except ValidationError as e:
        return _error_response(e.message, status_code=400)
    except NotFoundError as e:
        return _error_response(str(e), status_code=404)
    except Exception:
        logger.exception("[TAD] Error en api_tad_propose_document")
        return _error_response("Error interno del servidor", status_code=500)
