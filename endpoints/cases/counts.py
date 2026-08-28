
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from models.schemas import AuthenticatedUser
from database import fetch_all
from services.cases.reserved_predicate import build_reserved_or_exists
from shared.exceptions import exception_to_http_exception, ValidationError
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from shared.logging import get_logger
from config.constants import USER_UNAUTHENTICATED_ERROR

logger = get_logger(__name__)
router = APIRouter(tags=["expedientes"])


class CaseCountsResponse(BaseModel):
    asignado: int = Field(..., example=5, description="Expedientes asignados directamente al usuario")
    admin: int = Field(..., example=12, description="Expedientes administrados por el sector del usuario")
    actuante: int = Field(..., example=3, description="Expedientes donde el sector del usuario es actuante activo")
    favoritos: int = Field(..., example=8, description="Expedientes marcados como favoritos por el usuario")


@router.get("/counts", response_model=CaseCountsResponse)
async def get_case_counts(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> CaseCountsResponse:
    """
    Retorna el conteo de expedientes por cada vista del usuario.

    Se ejecutan 4 COUNT queries simples. Sin paginación.
    Usado para mostrar los badges numéricos en los tabs de la UI.
    """
    try:
        tenant_user_id = getattr(request.state, 'tenant_user_id', None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        db_user_id = await get_authenticated_user(tenant_user_id, schema_name=schema_name)

        logger.info(f"Getting case counts - User: {db_user_id[:8]}")

        reserved_gate = f"""
            AND (
                NOT ct.is_reserved
                OR ({build_reserved_or_exists(case_ref="c.id", user_ph="$1")})
            )
        """
        templates_join = "JOIN case_templates ct ON ct.id = c.case_template_id"

        asignado_query = f"""
            SELECT COUNT(DISTINCT c.id) AS total
            FROM cases c
            {templates_join}
            INNER JOIN (
                SELECT case_id FROM case_responsibles
                WHERE user_id = $1 AND is_active = true
                UNION
                SELECT case_id FROM case_assignment_tasks
                WHERE assigned_user_id = $1 AND status = 'open'
            ) cr ON cr.case_id = c.id
            WHERE c.status = 'active'
            {reserved_gate}
        """

        admin_query = f"""
            SELECT COUNT(DISTINCT c.id) AS total
            FROM cases c
            {templates_join}
            WHERE c.status = 'active'
              AND (
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                      AND cm.type = 'transfer' AND cm.is_active = false
                      AND cm.admin_sector_id IN (
                          SELECT sector_id FROM users WHERE id = $1
                          UNION SELECT sector_id FROM user_sector_permissions
                          WHERE user_id = $2 AND can_view = true
                      )
                      AND cm.closed_at = (
                          SELECT MAX(cm2.closed_at) FROM case_movements cm2
                          WHERE cm2.case_id = c.id AND cm2.type = 'transfer' AND cm2.is_active = false
                      )
                )
                OR (
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id AND cm.type = 'creation'
                          AND cm.admin_sector_id IN (
                              SELECT sector_id FROM users WHERE id = $3
                              UNION SELECT sector_id FROM user_sector_permissions
                              WHERE user_id = $4 AND can_view = true
                          )
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id AND cm.type = 'transfer'
                    )
                )
              )
            {reserved_gate}
        """

        actuante_query = f"""
            SELECT COUNT(DISTINCT c.id) AS total
            FROM cases c
            {templates_join}
            WHERE c.status = 'active'
              AND EXISTS (
                SELECT 1
                FROM case_movements cm
                WHERE cm.case_id = c.id
                  AND cm.type = 'assignment'
                  AND cm.is_active = true
                  AND cm.assigned_sector_id IN (
                      SELECT sector_id FROM users WHERE id = $1
                      UNION
                      SELECT sector_id FROM user_sector_permissions
                      WHERE user_id = $2 AND can_view = true
                  )
              )
            {reserved_gate}
        """

        favoritos_query = f"""
            SELECT COUNT(DISTINCT c.id) AS total
            FROM cases c
            {templates_join}
            INNER JOIN case_favorites cf
                ON cf.case_id = c.id
                AND cf.user_id = $1
            WHERE c.status = 'active'
            {reserved_gate}
        """

        r_asignado = await fetch_all(asignado_query, db_user_id, schema_name=schema_name)
        r_admin = await fetch_all(admin_query, db_user_id, db_user_id, db_user_id, db_user_id, schema_name=schema_name)
        r_actuante = await fetch_all(actuante_query, db_user_id, db_user_id, schema_name=schema_name)
        r_favoritos = await fetch_all(favoritos_query, db_user_id, schema_name=schema_name)

        counts = CaseCountsResponse(
            asignado=int(r_asignado[0]['total']) if r_asignado else 0,
            admin=int(r_admin[0]['total']) if r_admin else 0,
            actuante=int(r_actuante[0]['total']) if r_actuante else 0,
            favoritos=int(r_favoritos[0]['total']) if r_favoritos else 0,
        )

        logger.info(
            f"Case counts - User: {db_user_id[:8]}, "
            f"asignado={counts.asignado}, admin={counts.admin}, "
            f"actuante={counts.actuante}, favoritos={counts.favoritos}"
        )

        return counts

    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"Error getting case counts: {str(e)}")
        raise exception_to_http_exception(e)
