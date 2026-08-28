from shared.logging import get_logger
from typing import Dict, Any, Optional
from api_gateway.context import MCPContext
from services.case_service import CaseService
from database import fetch_all
from shared.exceptions import ValidationError, NotFoundError, AuthorizationError, BusinessLogicError, GDIBaseException
from services.cases.documents import link_official_document, accept_proposed_document, reject_proposed_document
from services.cases.creation import create_case_with_cover_service
from services.cases.transfer import get_available_sectors_for_transfer
from api_gateway.tools._sanitize import strip_storage_urls

logger = get_logger(__name__)


async def search_cases(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    date_filter: Optional[str] = None,
    sector_filter: Optional[str] = None,
) -> Dict[str, Any]:
    logger.info(f"[MCP] search_cases - schema={ctx.schema_name}, page={page}, search={search}")

    if page_size > 100:
        raise ValueError("page_size máximo es 100")

    if not user_id:
        raise ValueError("user_id es requerido para search_cases (necesario para permisos)")

    try:
        result = await CaseService.get_cases_by_user(
            user_id=user_id,
            page=page,
            page_size=page_size,
            status_filter=status,
            search_filter=search,
            date_filter=date_filter,
            sector_filter=sector_filter,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] search_cases - encontrados {result['total']} expedientes")
        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] search_cases - error: {e}")
        raise RuntimeError("Error buscando expedientes")


async def get_case(
    ctx: MCPContext,
    case_id: str,
    user_id: str,
    include_documents: bool = False,
    include_movements: bool = False,
) -> Optional[Dict[str, Any]]:
    logger.info(
        f"[MCP] get_case - case_id={case_id}, user_id={user_id}, schema={ctx.schema_name}, "
        f"include_documents={include_documents}, include_movements={include_movements}"
    )

    try:
        result = await CaseService.get_case_detail(
            case_id=case_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        if not result:
            logger.warning(f"[MCP] get_case - expediente no encontrado o sin permisos")
            return None

        if include_documents:
            result["documents"] = await CaseService.get_case_documents(case_id, schema_name=ctx.schema_name)
            logger.info(f"[MCP] get_case - documentos incluidos: {result['documents']['total_official']} oficiales, {result['documents']['total_proposed']} propuestos")

        if include_movements:
            movements = await CaseService.get_case_movements(case_id, schema_name=ctx.schema_name)
            result["movements"] = {
                "items": movements,
                "total": len(movements),
            }
            logger.info(f"[MCP] get_case - movimientos incluidos: {len(movements)}")

        logger.info(f"[MCP] get_case - expediente encontrado: {result.get('case_number')}")
        return strip_storage_urls(result)

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_case - error: {e}")
        raise RuntimeError("Error obteniendo expediente")


async def get_case_history(
    ctx: MCPContext,
    case_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_case_history - case_id={case_id}, user_id={user_id}, schema={ctx.schema_name}")

    try:
        if not await CaseService.can_user_view_case(case_id, user_id, schema_name=ctx.schema_name):
            logger.warning(f"[MCP] get_case_history - usuario {user_id} sin permisos para expediente {case_id}")
            raise ValueError("Usuario no tiene permisos para ver el historial de este expediente")

        result = await CaseService.get_case_history(
            case_id=case_id,
            schema_name=ctx.schema_name
        )

        movements_count = len(result.get("movements", []))
        logger.info(f"[MCP] get_case_history - {movements_count} movimientos encontrados")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_case_history - error: {e}")
        raise RuntimeError("Error obteniendo historial")


async def get_case_documents(
    ctx: MCPContext,
    case_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_case_documents - case_id={case_id}, user_id={user_id}, schema={ctx.schema_name}")

    try:
        can_view = await CaseService.can_user_view_case(case_id, user_id, schema_name=ctx.schema_name)
        if not can_view:
            logger.warning(f"[MCP] get_case_documents - usuario {user_id} sin permisos para expediente {case_id}")
            raise ValueError("Usuario no tiene permisos para ver este expediente")

        result = await CaseService.get_case_documents(case_id, schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_case_documents - encontrados {result['total_official']} oficiales, {result['total_proposed']} propuestos")
        return strip_storage_urls(result)

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_case_documents - error: {e}")
        raise RuntimeError("Error obteniendo documentos del expediente")


async def get_case_permissions(
    ctx: MCPContext,
    case_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_case_permissions - case_id={case_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not user_id:
        raise ValueError("user_id es requerido para get_case_permissions")

    if not await CaseService.can_user_view_case(case_id, user_id, schema_name=ctx.schema_name):
        logger.warning(f"[MCP] get_case_permissions - usuario {user_id} sin permisos para expediente {case_id}")
        raise ValueError("Usuario no tiene permisos para ver este expediente")

    try:
        result = await CaseService.get_user_case_permissions(
            case_id=case_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] get_case_permissions - ownership_level={result.get('ownership_level')}")
        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_case_permissions - error: {e}")
        raise RuntimeError("Error obteniendo permisos")


async def get_case_by_number(
    ctx: MCPContext,
    case_number: str,
    user_id: str
) -> Optional[Dict[str, Any]]:
    logger.info(f"[MCP] get_case_by_number - case_number={case_number}, user_id={user_id}, schema={ctx.schema_name}")

    if not case_number:
        raise ValueError("case_number es requerido")

    try:
        case_service = CaseService()
        result = await case_service.get_case_by_exact_number_unrestricted(
            case_number=case_number,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        if not result:
            logger.warning(f"[MCP] get_case_by_number - expediente no encontrado o sin permisos: {case_number}")
            return None

        logger.info(f"[MCP] get_case_by_number - expediente encontrado: {case_number}")
        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_case_by_number - error: {e}")
        raise RuntimeError("Error buscando expediente por número")


async def prepare_assignment(
    ctx: MCPContext,
    case_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] prepare_assignment - case_id={case_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not case_id:
        raise ValueError("case_id es requerido")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        user_sectors_query = """
            -- Sector principal
            SELECT s.id as sector_id
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            WHERE u.id = $1 AND s.is_active = true

            UNION

            -- Sectores adicionales con can_edit=true
            SELECT s2.id as sector_id
            FROM users u
            JOIN user_sector_permissions usp ON u.id = usp.user_id
            JOIN sectors s2 ON usp.sector_id = s2.id
            WHERE u.id = $2 AND s2.is_active = true AND usp.can_edit = true
        """
        user_sectors_result = await fetch_all(
            user_sectors_query,
            user_id, user_id,
            schema_name=ctx.schema_name
        )
        user_sector_ids = [str(row['sector_id']) for row in user_sectors_result if row['sector_id']]

        admin_sector_query = """
            SELECT
                s.id as sector_id,
                d.acronym || '#' || s.acronym as sector_acronym,
                d.name as department_name
            FROM case_movements cm
            JOIN sectors s ON cm.admin_sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE cm.case_id = $1
              AND cm.is_active = false
              AND cm.type IN ('creation', 'transfer')
            ORDER BY cm.closed_at DESC
            LIMIT 1
        """
        admin_sector_result = await fetch_all(
            admin_sector_query,
            case_id,
            schema_name=ctx.schema_name
        )

        admin_sector_id = None
        admin_sector_data = None
        if admin_sector_result:
            admin_sector_id = str(admin_sector_result[0]['sector_id'])
            admin_sector_data = admin_sector_result[0]

        assigned_sectors_query = """
            SELECT DISTINCT
                s.id as sector_id,
                d.acronym || '#' || s.acronym as sector_acronym,
                d.name as department_name
            FROM case_movements cm
            JOIN sectors s ON cm.assigned_sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE cm.case_id = $1
              AND cm.is_active = true
              AND cm.assigned_sector_id IS NOT NULL
        """
        assigned_sectors_result = await fetch_all(
            assigned_sectors_query,
            case_id,
            schema_name=ctx.schema_name
        )

        assigned_sector_ids = []
        assigned_sectors_data = {}
        for row in assigned_sectors_result:
            sector_id = str(row['sector_id'])
            assigned_sector_ids.append(sector_id)
            assigned_sectors_data[sector_id] = row

        user_sectors_in_case = []

        if admin_sector_id and admin_sector_id in user_sector_ids:
            user_sectors_in_case.append({
                "sector_id": admin_sector_id,
                "acronym": admin_sector_data['sector_acronym'],
                "department": admin_sector_data['department_name'],
                "role": "ADMIN"
            })

        for sector_id in assigned_sector_ids:
            if sector_id in user_sector_ids and sector_id != admin_sector_id:
                user_sectors_in_case.append({
                    "sector_id": sector_id,
                    "acronym": assigned_sectors_data[sector_id]['sector_acronym'],
                    "department": assigned_sectors_data[sector_id]['department_name'],
                    "role": "ASIGNADO"
                })

        if not user_sectors_in_case:
            logger.warning(f"[MCP] prepare_assignment - usuario {user_id} sin permisos en expediente {case_id}")
            return {
                "success": False,
                "status": "NOT_ALLOWED",
                "message": "Usuario no tiene permisos sobre este expediente"
            }

        available_sectors_query = """
            SELECT DISTINCT
                s.id as sector_id,
                d.acronym || '#' || s.acronym as sector_acronym,
                d.name as department_name
            FROM sectors s
            JOIN departments d ON s.department_id = d.id
            WHERE s.is_active = true
            ORDER BY sector_acronym
        """
        available_sectors_result = await fetch_all(
            available_sectors_query,
            schema_name=ctx.schema_name
        )

        available_sectors = [
            {
                "sector_id": str(row['sector_id']),
                "acronym": row['sector_acronym'],
                "department": row['department_name']
            }
            for row in available_sectors_result
        ]

        if not available_sectors:
            return {
                "success": False,
                "status": "NOT_ALLOWED",
                "message": "No hay sectores disponibles en la municipalidad"
            }

        user_sectors_in_case.sort(key=lambda x: (x['role'] != 'ADMIN', x['acronym']))

        logger.info(f"[MCP] prepare_assignment - usuario tiene {len(user_sectors_in_case)} sector(es) en expediente")

        return {
            "success": True,
            "status": "OK",
            "user_sectors_in_case": user_sectors_in_case,
            "available_sectors": available_sectors,
            "total": len(user_sectors_in_case),
            "total_available_sectors": len(available_sectors)
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] prepare_assignment - error: {e}")
        raise RuntimeError("Error preparando asignación")


async def assign_case(
    ctx: MCPContext,
    case_id: str,
    target_sector_id: str,
    reason: str,
    user_id: str,
    assigned_user_id: Optional[str] = None,
    create_official_doc: bool = False
) -> Dict[str, Any]:
    logger.info(f"[MCP] assign_case - case_id={case_id}, target={target_sector_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not target_sector_id:
        raise ValueError("target_sector_id es requerido")
    if not reason:
        raise ValueError("reason es requerido")
    if len(reason) < 5 or len(reason) > 500:
        raise ValueError("reason debe tener entre 5 y 500 caracteres")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        from services.cases.tasks import ensure_assignment_and_create_task

        official_document_info = None
        if create_official_doc:
            from services.cases.transfer_document_creator import create_transfer_document

            case_info = await fetch_all(
                "SELECT case_number FROM cases WHERE id = $1",
                case_id, schema_name=ctx.schema_name
            )
            user_info = await fetch_all(
                "SELECT sector_id FROM users WHERE id = $1",
                user_id, schema_name=ctx.schema_name
            )

            if case_info and user_info:
                doc_result = await create_transfer_document(
                    case_id=case_id,
                    case_number=case_info[0]['case_number'],
                    movement_type="Asignación",
                    movement_reason=reason.strip(),
                    requesting_sector_id=str(user_info[0]['sector_id']),
                    receiving_sector_id=target_sector_id,
                    user_id=user_id,
                    schema_name=ctx.schema_name
                )
                await link_official_document(
                    case_id=case_id,
                    official_document_id=doc_result['document_id'],
                    linking_user_id=user_id,
                    user_sector_id=str(user_info[0]['sector_id']),
                    schema_name=ctx.schema_name
                )
                official_document_info = doc_result
                logger.info(f"[MCP] assign_case - PV creado: {doc_result['official_number']}")

        result = await ensure_assignment_and_create_task(
            case_id=case_id,
            target_sector_id=target_sector_id,
            reason=reason,
            user_id=user_id,
            assigned_user_id=assigned_user_id,
            create_official_doc=False,
            schema_name=ctx.schema_name,
        )

        logger.info(
            f"[MCP] assign_case - OK: assignment={result['assignment_id']}, "
            f"task={result['task_id']}, is_new={result['is_new_assignment']}"
        )

        response = {
            "success": True,
            "movement_id": result["assignment_id"],
            "assignment_id": result["assignment_id"],
            "task_id": result["task_id"],
            "action_type": "asignado",
            "sector_acronym": result["sector_acronym"],
            "department_name": result["department_name"],
            "is_new_assignment": result["is_new_assignment"],
        }

        if official_document_info:
            response["official_document"] = {
                "document_id": official_document_info['document_id'],
                "official_number": official_document_info['official_number'],
            }

        return response

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] assign_case - error: {e}")
        raise RuntimeError("Error asignando expediente")


async def close_assignment(
    ctx: MCPContext,
    case_id: str,
    movement_id: str,
    reason: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] close_assignment - case_id={case_id}, movement_id={movement_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not movement_id:
        raise ValueError("movement_id es requerido")
    if not reason:
        raise ValueError("reason es requerido")
    if len(reason) < 5 or len(reason) > 500:
        raise ValueError("reason debe tener entre 5 y 500 caracteres")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        document_result = None
        mov_query = "SELECT supporting_document_id, assigned_sector_id FROM case_movements WHERE id = $1 AND case_id = $2"
        mov_check = await fetch_all(mov_query, movement_id, case_id, schema_name=ctx.schema_name)
        has_supporting_doc = mov_check and mov_check[0]['supporting_document_id'] is not None

        if has_supporting_doc:
            try:
                from services.cases.transfer_document_creator import create_transfer_document

                case_info = await fetch_all(
                    "SELECT case_number FROM cases WHERE id = $1",
                    case_id, schema_name=ctx.schema_name
                )
                user_info = await fetch_all(
                    "SELECT sector_id FROM users WHERE id = $1",
                    user_id, schema_name=ctx.schema_name
                )

                if case_info and user_info:
                    assigned_sector = str(mov_check[0]['assigned_sector_id']) if mov_check[0]['assigned_sector_id'] else str(user_info[0]['sector_id'])

                    document_result = await create_transfer_document(
                        case_id=case_id,
                        case_number=case_info[0]['case_number'],
                        movement_type="Cierre de Asignación",
                        movement_reason=reason.strip(),
                        requesting_sector_id=assigned_sector,
                        receiving_sector_id=assigned_sector,
                        user_id=user_id,
                        schema_name=ctx.schema_name
                    )

                    await link_official_document(
                        case_id=case_id,
                        official_document_id=document_result['document_id'],
                        linking_user_id=user_id,
                        user_sector_id=str(user_info[0]['sector_id']),
                        schema_name=ctx.schema_name
                    )

                    logger.info(f"[MCP] close_assignment - PV de cierre creado: {document_result['official_number']}")
            except Exception as doc_error:
                logger.warning(f"[MCP] close_assignment - error creando PV de cierre: {doc_error}")

        result = await CaseService.close_assignment(
            case_id=case_id,
            movement_id=movement_id,
            reason=reason,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] close_assignment - asignación cerrada: movement_id={movement_id}")

        response = {
            "success": True,
            "movement_id": str(result.get("movement_id")),
            "assignment_id": str(result.get("movement_id")),
            "case_id": str(result.get("case_id")),
            "movement_type": result.get("movement_type"),
            "closing_reason": result.get("closing_reason")
        }

        if document_result:
            response["official_document"] = {
                "document_id": document_result['document_id'],
                "official_number": document_result['official_number'],
            }

        return response

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.exception(f"[MCP] close_assignment - error: {e}")
        raise RuntimeError("Error cerrando asignación")


async def _get_user_sector_id(user_id: str, *, schema_name: str) -> Dict[str, Any]:
    query = "SELECT sector_id FROM users WHERE id = $1"
    result = await fetch_all(query, user_id, schema_name=schema_name)
    if not result:
        raise NotFoundError(f"Usuario no encontrado: {user_id}")
    return result[0]


async def create_case(
    ctx: MCPContext,
    case_template_id: str,
    reference: str,
    user_id: str,
    owner_sector_id: Optional[str] = None
) -> Dict[str, Any]:
    logger.info(f"[MCP] create_case - template={case_template_id}, user={user_id}, schema={ctx.schema_name}")

    if not case_template_id:
        raise ValueError("case_template_id es requerido")
    if not reference:
        raise ValueError("reference es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = await create_case_with_cover_service(
            case_template_id=case_template_id,
            reference=reference,
            user_id=user_id,
            owner_sector_id=owner_sector_id,
            schema_name=ctx.schema_name
        )

        case_data = result["case"]
        logger.info(f"[MCP] create_case - expediente creado: {case_data.get('case_id')}")

        return {
            "success": True,
            "case_id": str(case_data["case_id"]),
            "case_number": case_data.get("case_number"),
            "message": "Expediente creado exitosamente"
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] create_case - error: {e}")
        raise RuntimeError("Error creando expediente")


async def transfer_case(
    ctx: MCPContext,
    case_id: str,
    target_sector_id: str,
    reason: str,
    user_id: str,
    assigned_user_id: Optional[str] = None,
    create_official_doc: bool = False
) -> Dict[str, Any]:
    logger.info(f"[MCP] transfer_case - case_id={case_id}, target={target_sector_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not target_sector_id:
        raise ValueError("target_sector_id es requerido")
    if not reason:
        raise ValueError("reason es requerido")
    if len(reason) < 5 or len(reason) > 500:
        raise ValueError("reason debe tener entre 5 y 500 caracteres")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        prep = await prepare_assignment(ctx, case_id, user_id)
        if not prep.get("success"):
            raise AuthorizationError(prep.get("message", "Sin permisos para transferir"))

        supporting_document_id = None
        official_document_info = None
        if create_official_doc:
            from services.cases.transfer_document_creator import create_transfer_document

            case_info = await fetch_all(
                "SELECT case_number FROM cases WHERE id = $1",
                case_id, schema_name=ctx.schema_name
            )
            user_info = await fetch_all(
                "SELECT sector_id FROM users WHERE id = $1",
                user_id, schema_name=ctx.schema_name
            )

            if case_info and user_info:
                doc_result = await create_transfer_document(
                    case_id=case_id,
                    case_number=case_info[0]['case_number'],
                    movement_type="Transferencia",
                    movement_reason=reason.strip(),
                    requesting_sector_id=str(user_info[0]['sector_id']),
                    receiving_sector_id=target_sector_id,
                    user_id=user_id,
                    schema_name=ctx.schema_name
                )

                await link_official_document(
                    case_id=case_id,
                    official_document_id=doc_result['document_id'],
                    linking_user_id=user_id,
                    user_sector_id=str(user_info[0]['sector_id']),
                    schema_name=ctx.schema_name
                )

                supporting_document_id = doc_result['document_id']
                official_document_info = doc_result
                logger.info(f"[MCP] transfer_case - PV creado: {doc_result['official_number']}")

        result = await CaseService.transfer_case(
            case_id=case_id,
            target_sector_id=target_sector_id,
            reason=reason,
            user_id=user_id,
            transfer_ownership=True,
            assigned_user_id=assigned_user_id,
            supporting_document_id=supporting_document_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] transfer_case - transferencia exitosa: movement_id={result.get('movement_id')}")

        response = {
            "success": True,
            "movement_id": result.get("movement_id"),
            "case_number": result.get("case_number"),
            "action_type": "transferido",
            "target_sector": result.get("target_sector"),
            "target_department": result.get("target_department"),
            "transferred_by": result.get("transferred_by"),
            "assigned_user": result.get("assigned_user"),
        }

        if official_document_info:
            response["official_document"] = {
                "document_id": official_document_info['document_id'],
                "official_number": official_document_info['official_number'],
            }

        return response

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] transfer_case - error: {e}")
        raise RuntimeError("Error transfiriendo expediente")


async def link_document_to_case(
    ctx: MCPContext,
    case_id: str,
    official_document_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] link_document_to_case - case_id={case_id}, doc={official_document_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not official_document_id:
        raise ValueError("official_document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        user_data = await _get_user_sector_id(user_id, schema_name=ctx.schema_name)

        result = await link_official_document(
            case_id=case_id,
            official_document_id=official_document_id,
            linking_user_id=user_id,
            user_sector_id=str(user_data["sector_id"]),
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] link_document_to_case - documento vinculado exitosamente")

        return {
            "success": True,
            **result
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] link_document_to_case - error: {e}")
        raise RuntimeError("Error vinculando documento al expediente")


async def propose_document(
    ctx: MCPContext,
    case_id: str,
    document_draft_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] propose_document - case_id={case_id}, doc={document_draft_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not document_draft_id:
        raise ValueError("document_draft_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        from services.cases.documents import propose_document_to_case

        result = await propose_document_to_case(
            case_id=case_id,
            document_draft_id=document_draft_id,
            proposing_user_id=user_id,
            schema_name=ctx.schema_name,
        )

        logger.info(f"[MCP] propose_document - documento propuesto exitosamente")

        return {
            "success": True,
            "case_id": result["case_id"],
            "document_draft_id": result["document_draft_id"],
            "message": result["message"],
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] propose_document - error: {e}")
        raise RuntimeError("Error proponiendo documento")


async def prepare_transfer(
    ctx: MCPContext,
    case_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] prepare_transfer - case_id={case_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        sectors = await get_available_sectors_for_transfer(
            case_id=case_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] prepare_transfer - {len(sectors)} sectores disponibles")

        return {
            "success": True,
            "available_sectors": sectors,
            "total": len(sectors)
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] prepare_transfer - error: {e}")
        raise RuntimeError("Error preparando transferencia")


async def accept_proposal(
    ctx: MCPContext,
    case_id: str,
    proposed_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] accept_proposal - case_id={case_id}, proposed_id={proposed_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not proposed_id:
        raise ValueError("proposed_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        user_data = await _get_user_sector_id(user_id, schema_name=ctx.schema_name)

        result = await accept_proposed_document(
            case_id=case_id,
            proposed_id=proposed_id,
            user_id=user_id,
            user_sector_id=str(user_data["sector_id"]),
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] accept_proposal - propuesta aceptada exitosamente")

        return {
            "success": True,
            **result
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] accept_proposal - error: {e}")
        raise RuntimeError("Error aceptando propuesta")


async def reject_proposal(
    ctx: MCPContext,
    case_id: str,
    proposed_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] reject_proposal - case_id={case_id}, proposed_id={proposed_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not proposed_id:
        raise ValueError("proposed_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        user_data = await _get_user_sector_id(user_id, schema_name=ctx.schema_name)

        result = await reject_proposed_document(
            case_id=case_id,
            proposed_id=proposed_id,
            user_id=user_id,
            user_sector_id=str(user_data["sector_id"]),
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] reject_proposal - propuesta rechazada exitosamente")

        return {
            "success": True,
            **result
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] reject_proposal - error: {e}")
        raise RuntimeError("Error rechazando propuesta")


async def get_sector_users_list(
    ctx: MCPContext,
    sector_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_sector_users_list - sector_id={sector_id}, schema={ctx.schema_name}")

    if not sector_id:
        raise ValueError("sector_id es requerido")

    try:
        from services.cases.transfer import get_sector_users

        users = await get_sector_users(sector_id, schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_sector_users_list - {len(users)} usuarios encontrados en sector {sector_id}")

        return {
            "users": users,
            "total": len(users)
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_sector_users_list - error: {e}")
        raise RuntimeError("Error obteniendo usuarios del sector")


async def get_case_responsibles_list(
    ctx: MCPContext,
    case_id: str,
    user_id: str,
) -> Dict[str, Any]:
    from services.cases.responsibles import get_case_responsibles
    from services.cases.permissions import can_user_view_case

    if not case_id:
        raise ValueError("case_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    if not await can_user_view_case(case_id, user_id, schema_name=ctx.schema_name):
        raise AuthorizationError("Sin permisos para ver este expediente")

    data = await get_case_responsibles(case_id, schema_name=ctx.schema_name)
    return {"success": True, "data": data, "message": "Responsables obtenidos correctamente"}


async def add_case_responsible(
    ctx: MCPContext,
    case_id: str,
    user_id: str,
    responsible_user_id: str,
    responsible_type: str,
    sector_id: str,
    reason: str = "Asignación de responsable",
) -> Dict[str, Any]:
    from services.cases.responsibles import add_responsible
    from services.cases.permissions import can_user_view_case, can_user_edit_case

    if not case_id:
        raise ValueError("case_id es requerido")
    if not responsible_user_id:
        raise ValueError("user_id es requerido")
    if responsible_type not in ("ADMIN", "ADDITIONAL"):
        raise ValueError("type debe ser ADMIN o ADDITIONAL")
    if not sector_id:
        raise ValueError("sector_id es requerido")

    if not await can_user_view_case(case_id, user_id, schema_name=ctx.schema_name):
        raise AuthorizationError("Sin permisos para ver este expediente")
    if not await can_user_edit_case(case_id, user_id, schema_name=ctx.schema_name):
        raise AuthorizationError("Sin permisos para modificar responsables de este expediente")

    from services.case_queries import get_admin_sector_for_case_query, get_assigned_sectors_for_case_query

    admin_rows = await fetch_all(get_admin_sector_for_case_query(), case_id, schema_name=ctx.schema_name)
    assigned_rows = await fetch_all(get_assigned_sectors_for_case_query(), case_id, schema_name=ctx.schema_name)

    valid_sector_ids = set()
    if admin_rows:
        valid_sector_ids.add(str(admin_rows[0]["sector_id"]))
    for row in assigned_rows:
        valid_sector_ids.add(str(row["sector_id"]))

    if sector_id not in valid_sector_ids:
        raise ValueError(
            f"El sector_id proporcionado no participa en el expediente. "
            f"Sectores válidos: {sorted(valid_sector_ids) or '(ninguno obtenido)'}"
        )

    result = await add_responsible(
        case_id=case_id,
        user_id=responsible_user_id,
        responsible_type=responsible_type,
        sector_id=sector_id,
        added_by=user_id,
        movement_reason=reason,
        schema_name=ctx.schema_name,
    )
    return {"success": True, "data": result, "message": "Responsable agregado exitosamente"}


async def remove_case_responsible(
    ctx: MCPContext,
    case_id: str,
    responsible_id: str,
    user_id: str,
    reason: str = "Remoción de responsable",
) -> Dict[str, Any]:
    from services.cases.responsibles import remove_responsible
    from services.cases.permissions import can_user_view_case, can_user_edit_case

    if not case_id:
        raise ValueError("case_id es requerido")
    if not responsible_id:
        raise ValueError("responsible_id es requerido")

    if not await can_user_view_case(case_id, user_id, schema_name=ctx.schema_name):
        raise AuthorizationError("Sin permisos para ver este expediente")
    if not await can_user_edit_case(case_id, user_id, schema_name=ctx.schema_name):
        raise AuthorizationError("Sin permisos para modificar responsables de este expediente")

    await remove_responsible(
        responsible_id=responsible_id,
        removed_by=user_id,
        movement_reason=reason,
        schema_name=ctx.schema_name,
    )
    return {"success": True, "message": "Responsable removido exitosamente"}


async def get_assignments_with_tasks_wrapper(
    ctx: MCPContext,
    case_id: str,
    user_id: str,
) -> Dict[str, Any]:
    from services.cases.tasks import get_assignments_with_tasks

    if not case_id:
        raise ValueError("case_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    assignments = await get_assignments_with_tasks(
        case_id=case_id,
        user_id=user_id,
        schema_name=ctx.schema_name,
    )
    return {
        "success": True,
        "data": {"assignments": assignments, "total": len(assignments)},
        "message": f"{len(assignments)} asignaciones activas.",
    }


async def get_assignable_users_wrapper(
    ctx: MCPContext,
    case_id: str,
    user_id: str,
    q: str = "",
    sector_id: Optional[str] = None,
) -> Dict[str, Any]:
    from services.cases.tasks import get_assignable_users

    if not case_id:
        raise ValueError("case_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    users = await get_assignable_users(
        case_id=case_id,
        q=q,
        user_id=user_id,
        schema_name=ctx.schema_name,
        sector_id=sector_id,
    )
    return {
        "success": True,
        "data": {"users": users, "total": len(users)},
        "message": f"{len(users)} usuarios encontrados.",
    }


async def get_available_responsibles_wrapper(
    ctx: MCPContext,
    case_id: str,
    user_id: str,
    responsible_type: str,
    sector_id: Optional[str] = None,
) -> Dict[str, Any]:
    from services.cases.responsibles import get_available_responsibles
    from services.cases.permissions import can_user_view_case

    if not case_id:
        raise ValueError("case_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")
    responsible_type = responsible_type.upper()
    if responsible_type not in ("ADMIN", "ADDITIONAL"):
        raise ValueError("type debe ser ADMIN o ADDITIONAL")

    if not await can_user_view_case(case_id, user_id, schema_name=ctx.schema_name):
        raise AuthorizationError("Sin permisos para ver este expediente")

    users = await get_available_responsibles(
        case_id,
        responsible_type,
        sector_id=sector_id,
        schema_name=ctx.schema_name,
    )
    return {
        "success": True,
        "data": users,
        "total": len(users),
        "message": f"{len(users)} usuario(s) disponible(s)",
    }


async def update_task_wrapper(
    ctx: MCPContext,
    case_id: str,
    task_id: str,
    user_id: str,
    assigned_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    from services.cases.tasks import update_task

    if not case_id:
        raise ValueError("case_id es requerido")
    if not task_id:
        raise ValueError("task_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    result = await update_task(
        case_id=case_id,
        task_id=task_id,
        user_id=user_id,
        assigned_user_id=assigned_user_id,
        schema_name=ctx.schema_name,
    )
    return {
        "success": True,
        "data": result,
        "message": "Responsable de tarea actualizado.",
    }


async def close_task_wrapper(
    ctx: MCPContext,
    case_id: str,
    task_id: str,
    user_id: str,
    closing_reason: Optional[str] = None,
    create_official_doc: bool = False,
) -> Dict[str, Any]:
    from services.cases.tasks import close_task

    if not case_id:
        raise ValueError("case_id es requerido")
    if not task_id:
        raise ValueError("task_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    result = await close_task(
        case_id=case_id,
        task_id=task_id,
        user_id=user_id,
        closing_reason=closing_reason,
        create_official_doc=create_official_doc,
        schema_name=ctx.schema_name,
    )

    msg = "Tarea cerrada."
    if result.get("assignment_closed"):
        msg = "Tarea cerrada. El sector cerró su intervención en el expediente."
        if result.get("official_document"):
            msg += f" Documento oficial: {result['official_document']['official_number']}."

    return {"success": True, "data": result, "message": msg}
