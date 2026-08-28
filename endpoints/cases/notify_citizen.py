from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.exceptions import NotFoundError, AuthorizationError, ValidationError, exception_to_http_exception
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from services.case_service import CaseService
from config.constants import CASE_NOT_FOUND_ERROR
from services.cases.citizen_shares import can_citizen_access_case, can_user_manage_citizen_shares
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["expedientes"])


class NotifyCitizenRequest(BaseModel):
    citizen_id: str = Field(..., description="UUID del ciudadano a notificar")
    document_ids: list[str] = Field(..., min_length=1, description="IDs de official_documents a notificar")


class NotifyCitizenResponse(BaseModel):
    success: bool = True
    data: dict
    message: str


@router.post("/{case_id}/notify-citizen", response_model=NotifyCitizenResponse)
async def notify_citizen(
    request: Request,
    body: NotifyCitizenRequest,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    try:
        db_user_id = await get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for notify_citizen: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        if not await can_user_manage_citizen_shares(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Edit denied for notify_citizen: user={db_user_id[:8]}, case={case_id[:8]}")
            raise AuthorizationError("Sin permisos para notificar en este expediente")

        if not await can_citizen_access_case(case_id, body.citizen_id, schema_name=schema_name):
            raise ValidationError("El expediente no esta compartido (activamente) con este ciudadano")

        from database import fetch_one, fetch_all
        citizen_row = await fetch_one(
            "SELECT id, full_name, country_id FROM citizens WHERE id = $1",
            body.citizen_id,
            schema_name=schema_name,
        )
        if not citizen_row:
            raise NotFoundError("Ciudadano no encontrado")

        case_row = await fetch_one(
            "SELECT id, case_number, reference FROM cases WHERE id = $1",
            case_id,
            schema_name=schema_name,
        )
        if not case_row:
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        linked_docs = await fetch_all(
            """
            SELECT od.id, od.official_number
            FROM case_official_documents cod
            JOIN official_documents od ON cod.official_document_id = od.id
            WHERE cod.case_id = $1 AND cod.is_active = true
              AND od.id = ANY($2::uuid[]) AND od.signed_at IS NOT NULL
            """,
            case_id, body.document_ids,
            schema_name=schema_name,
        )
        linked_ids = {str(r["id"]) for r in linked_docs}
        missing = [d for d in body.document_ids if d not in linked_ids]
        if missing:
            raise ValidationError(
                f"Documento(s) no vinculado(s) (o no firmado(s)) a este expediente: {missing}"
            )
        official_numbers_by_id = {str(r["id"]): r["official_number"] for r in linked_docs}
        official_numbers = [official_numbers_by_id[d] for d in body.document_ids]

        from services.webhooks.tad_notify import (
            get_tad_webhook_config, build_documents_notified_payload, enqueue_tad_webhook,
        )

        config = await get_tad_webhook_config(schema_name=schema_name)
        if not config:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "El municipio no tiene un webhook TAD configurado (webhook_url/secret)",
                    "type": "ValidationError",
                },
            )

        payload = await build_documents_notified_payload(
            citizen={"id": str(citizen_row["id"]), "full_name": citizen_row["full_name"], "country_id": citizen_row["country_id"]},
            case={"id": str(case_row["id"]), "number": case_row["case_number"], "reference": case_row["reference"]},
            document_ids=body.document_ids,
            schema_name=schema_name,
        )

        job_id = await enqueue_tad_webhook(
            schema_name=schema_name,
            api_key_id=config["api_key_id"],
            event_type="documents.notified",
            payload=payload,
        )

        try:
            from services.cases.citizen_shares_queries import (
                get_case_admin_sector_query, get_user_sector_and_name_query,
            )
            from services.cases.history import create_movement
            from config.constants import MOVEMENT_TYPE_CITIZEN_NOTIFY

            actor_rows = await fetch_all(get_user_sector_and_name_query(), db_user_id, schema_name=schema_name)
            actor_sector_id = str(actor_rows[0]["sector_id"]) if actor_rows and actor_rows[0]["sector_id"] else None

            admin_rows = await fetch_all(get_case_admin_sector_query(), case_id, schema_name=schema_name)
            admin_sector_id = (
                str(admin_rows[0]["admin_sector_id"])
                if admin_rows and admin_rows[0]["admin_sector_id"] else actor_sector_id
            )

            await create_movement(
                case_id,
                MOVEMENT_TYPE_CITIZEN_NOTIFY,
                user_id=db_user_id,
                creator_sector_id=actor_sector_id,
                admin_sector_id=admin_sector_id or actor_sector_id,
                reason=f"Se enviaron a notificar los documentos {', '.join(official_numbers)}",
                schema_name=schema_name,
            )
        except Exception as exc:
            logger.warning(f"No se pudo registrar movement citizen_notify para caso {case_id}: {exc}")

        return {
            "success": True,
            "data": {"job_id": job_id, "status": "pending"},
            "message": "Documentos enviados al sistema de notificaciones",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error notificando ciudadano en caso {case_id}: {exc}")
        raise exception_to_http_exception(exc)
