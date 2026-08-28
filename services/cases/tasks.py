
import uuid
from typing import Dict, Any, List, Optional

from database import fetch_all, fetch_one, execute, transaction
from shared.exceptions import (
    NotFoundError,
    ValidationError,
    AuthorizationError,
    BusinessLogicError,
    IsLastTaskError,
)
from shared.logging import get_logger
from config.constants import (
    MOVEMENT_TYPE_ASSIGNMENT,
    MOVEMENT_TYPE_ASSIGNMENT_CLOSE,
)

logger = get_logger(__name__)


async def _get_admin_sector_id(case_id: str, *, schema_name: str) -> Optional[str]:
    row = await fetch_one(
        """
        SELECT admin_sector_id
        FROM case_movements
        WHERE case_id = $1
          AND is_active = false
          AND type IN ('creation', 'transfer')
        ORDER BY closed_at DESC
        LIMIT 1
        """,
        case_id,
        schema_name=schema_name,
    )
    return str(row["admin_sector_id"]) if row and row["admin_sector_id"] else None


async def _check_task_permission(
    task_row: dict,
    case_id: str,
    user_id: str,
    *,
    schema_name: str,
) -> None:
    from services.cases.permissions import get_user_editable_sector_ids

    user_sectors = await get_user_editable_sector_ids(user_id, schema_name=schema_name)
    assigned_sector_id = str(task_row["assigned_sector_id"])

    if assigned_sector_id in user_sectors:
        return

    admin_sector_id = await _get_admin_sector_id(case_id, schema_name=schema_name)
    if admin_sector_id and admin_sector_id in user_sectors:
        return

    raise AuthorizationError(
        "Sin permisos para modificar esta tarea. "
        "Debe pertenecer al sector asignado o ser administrador del expediente."
    )


async def _record_assignment_close(
    conn,
    case_id: str,
    assignment_id: str,
    assigned_sector_id: Optional[str],
    user_id: str,
    reason: str,
    *,
    schema_name: str,
) -> None:
    admin_sector_id = await _get_admin_sector_id(case_id, schema_name=schema_name)
    if not admin_sector_id:
        admin_sector_id = assigned_sector_id

    user_row = await fetch_one(
        "SELECT sector_id FROM users WHERE id = $1",
        user_id,
        schema_name=schema_name,
    )
    closer_sector_id = (
        str(user_row["sector_id"]) if user_row and user_row["sector_id"]
        else (assigned_sector_id or admin_sector_id)
    )

    close_history_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO case_movements (
            id, case_id, type, user_id,
            creator_sector_id, admin_sector_id,
            assigned_sector_id, reason,
            is_active, closed_at, closing_reason
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8,
            false, NOW(), $9
        )
        """,
        close_history_id, case_id, MOVEMENT_TYPE_ASSIGNMENT_CLOSE, user_id,
        closer_sector_id, admin_sector_id,
        assigned_sector_id, reason.strip(),
        reason.strip(),
    )


async def ensure_assignment_and_create_task(
    case_id: str,
    target_sector_id: str,
    reason: str,
    user_id: str,
    *,
    assigned_user_id: Optional[str] = None,
    create_official_doc: bool = False,
    supporting_document_id: Optional[str] = None,
    schema_name: str,
) -> Dict[str, Any]:
    from services.cases.permissions import can_user_edit_case
    from services.case_queries import check_duplicate_assignment_query

    if not await can_user_edit_case(case_id, user_id, schema_name=schema_name):
        raise AuthorizationError(
            "Sin permisos para asignar tareas en este expediente."
        )

    sector_row = await fetch_one(
        """
        SELECT s.id, s.acronym, s.primary_color AS color, d.name AS department_name, d.acronym AS department_acronym
        FROM sectors s
        JOIN departments d ON s.department_id = d.id
        WHERE s.id = $1 AND s.is_active = true
        """,
        target_sector_id,
        schema_name=schema_name,
    )
    if not sector_row:
        raise ValidationError("El sector destino no existe o está inactivo.")

    if assigned_user_id:
        user_in_sector = await fetch_one(
            """
            SELECT u.id FROM users u
            WHERE u.id = $1
              AND u.estado = 1
              AND (
                u.sector_id = $2
                OR EXISTS (
                    SELECT 1 FROM user_sector_permissions usp
                    WHERE usp.user_id = u.id AND usp.sector_id = $2
                )
              )
            """,
            assigned_user_id, target_sector_id,
            schema_name=schema_name,
        )
        if not user_in_sector:
            raise ValidationError(
                "El usuario asignado no pertenece al sector destino."
            )

    dup = await fetch_all(
        check_duplicate_assignment_query(),
        case_id, target_sector_id,
        schema_name=schema_name,
    )

    user_row = await fetch_one(
        "SELECT sector_id FROM users WHERE id = $1",
        user_id,
        schema_name=schema_name,
    )
    if not user_row:
        raise NotFoundError("Usuario no encontrado.")
    creator_sector_id = str(user_row["sector_id"])

    admin_sector_id = await _get_admin_sector_id(case_id, schema_name=schema_name)
    if not admin_sector_id:
        admin_sector_id = creator_sector_id

    is_new_assignment = not bool(dup)
    task_id = str(uuid.uuid4())

    async with transaction(schema_name=schema_name, user_id=user_id, auth_source="jwt") as conn:
        if is_new_assignment:
            assignment_id = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO case_movements (
                    id, case_id, type, user_id,
                    creator_sector_id, admin_sector_id,
                    assigned_sector_id, assigned_user_id,
                    reason, is_active, supporting_document_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                assignment_id, case_id, MOVEMENT_TYPE_ASSIGNMENT, user_id,
                creator_sector_id, admin_sector_id,
                target_sector_id, assigned_user_id,
                reason.strip(), True, supporting_document_id,
            )
        else:
            assignment_id = str(dup[0]["id"])
            if supporting_document_id:
                await conn.execute(
                    """
                    UPDATE case_movements
                    SET supporting_document_id = $1
                    WHERE id = $2 AND supporting_document_id IS NULL
                    """,
                    supporting_document_id, assignment_id,
                )

        await conn.execute(
            """
            INSERT INTO case_assignment_tasks (
                id, case_id, assignment_id, assigned_sector_id,
                assigned_user_id, reason, status,
                created_by, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, 'open', $7, NOW())
            """,
            task_id, case_id, assignment_id, target_sector_id,
            assigned_user_id, reason.strip(), user_id,
        )

    logger.info(
        f"ensure_assignment_and_create_task: case={case_id}, "
        f"assignment={assignment_id}, task={task_id}, new={is_new_assignment}"
    )

    return {
        "assignment_id": assignment_id,
        "task_id": task_id,
        "sector_acronym": str(sector_row["department_acronym"]) + "#" + str(sector_row["acronym"]),
        "department_name": str(sector_row["department_name"]),
        "is_new_assignment": is_new_assignment,
    }


async def close_task(
    case_id: str,
    task_id: str,
    user_id: str,
    *,
    closing_reason: Optional[str] = None,
    create_official_doc: bool = False,
    schema_name: str,
) -> Dict[str, Any]:
    task_row = await fetch_one(
        """
        SELECT id, case_id, assignment_id, assigned_sector_id, assigned_user_id,
               reason, status, created_by, created_at
        FROM case_assignment_tasks
        WHERE id = $1 AND case_id = $2
        """,
        task_id, case_id,
        schema_name=schema_name,
    )
    if not task_row:
        raise NotFoundError("Tarea no encontrada en este expediente.")

    if task_row["status"] != "open":
        raise BusinessLogicError("La tarea ya fue cerrada.")

    await _check_task_permission(task_row, case_id, user_id, schema_name=schema_name)

    assignment_id = str(task_row["assignment_id"])

    open_count_row = await fetch_one(
        """
        SELECT COUNT(*) AS cnt
        FROM case_assignment_tasks
        WHERE assignment_id = $1
          AND status = 'open'
          AND id != $2
        """,
        assignment_id, task_id,
        schema_name=schema_name,
    )
    other_open = int(open_count_row["cnt"]) if open_count_row else 0
    is_last = other_open == 0

    reason_valid = closing_reason and len(closing_reason.strip()) >= 5
    if is_last and not reason_valid:
        raise IsLastTaskError(
            "Es la última tarea abierta del sector. "
            "Incluí closing_reason (min 5 caracteres) para confirmar el cierre del sector."
        )

    assignment_closed = False
    async with transaction(schema_name=schema_name, user_id=user_id, auth_source="jwt") as conn:
        inner_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM case_assignment_tasks
            WHERE assignment_id = $1 AND status = 'open' AND id != $2
            """,
            assignment_id, task_id,
        )
        inner_other_open = int(inner_count)

        updated = await conn.execute(
            """
            UPDATE case_assignment_tasks
            SET status = 'closed',
                closed_at = NOW(),
                closed_by = $1,
                closing_reason = $2
            WHERE id = $3 AND status = 'open'
            """,
            user_id,
            (closing_reason.strip() if closing_reason else None),
            task_id,
        )
        if updated == "UPDATE 0":
            raise BusinessLogicError("La tarea ya fue cerrada por otro usuario.")

        if inner_other_open == 0:
            cascade_reason = closing_reason.strip() if closing_reason else "Última tarea cerrada."
            await conn.execute(
                """
                UPDATE case_movements
                SET is_active = false,
                    closed_at = NOW(),
                    closing_reason = $1
                WHERE id = $2 AND is_active = true
                """,
                cascade_reason,
                assignment_id,
            )
            assigned_sector_id_str = str(task_row["assigned_sector_id"]) if task_row["assigned_sector_id"] else None
            await _record_assignment_close(
                conn,
                case_id=case_id,
                assignment_id=assignment_id,
                assigned_sector_id=assigned_sector_id_str,
                user_id=user_id,
                reason=cascade_reason,
                schema_name=schema_name,
            )
            assignment_closed = True

    logger.info(
        f"close_task: task={task_id}, case={case_id}, "
        f"assignment_closed={assignment_closed}"
    )

    official_document = None
    should_create_doc = create_official_doc
    if assignment_closed and not should_create_doc:
        opening_mov = await fetch_one(
            "SELECT supporting_document_id FROM case_movements WHERE id = $1",
            assignment_id,
            schema_name=schema_name,
        )
        should_create_doc = bool(opening_mov and opening_mov["supporting_document_id"])
    if assignment_closed and should_create_doc:
        try:
            from services.cases.transfer_document_creator import create_transfer_document
            from services.cases.documents import link_official_document as _link_official_document

            case_info = await fetch_one(
                "SELECT case_number FROM cases WHERE id = $1",
                case_id,
                schema_name=schema_name,
            )
            user_info = await fetch_one(
                "SELECT sector_id FROM users WHERE id = $1",
                user_id,
                schema_name=schema_name,
            )
            if case_info and user_info:
                assigned_sector_id_for_pv = str(task_row["assigned_sector_id"]) if task_row["assigned_sector_id"] else str(user_info["sector_id"])
                doc_result = await create_transfer_document(
                    case_id=case_id,
                    case_number=case_info["case_number"],
                    movement_type="Cierre de Asignación",
                    movement_reason=(closing_reason.strip() if closing_reason else "Cierre de sector"),
                    requesting_sector_id=assigned_sector_id_for_pv,
                    receiving_sector_id=assigned_sector_id_for_pv,
                    user_id=user_id,
                    schema_name=schema_name,
                )
                await _link_official_document(
                    case_id=case_id,
                    official_document_id=doc_result["document_id"],
                    linking_user_id=user_id,
                    user_sector_id=str(user_info["sector_id"]),
                    schema_name=schema_name,
                )
                official_document = {
                    "document_id": doc_result["document_id"],
                    "official_number": doc_result["official_number"],
                    "message": doc_result.get("message", "Documento creado"),
                }
                logger.info(f"close_task: PV cierre generado {doc_result['official_number']}")
        except Exception as pv_err:
            logger.warning(f"close_task: error generando PV de cierre (no bloquea): {pv_err}")

    return {
        "task_id": task_id,
        "closed": True,
        "assignment_closed": assignment_closed,
        "official_document": official_document,
    }


async def update_task(
    case_id: str,
    task_id: str,
    user_id: str,
    *,
    assigned_user_id: Optional[str],
    schema_name: str,
) -> Dict[str, Any]:
    task_row = await fetch_one(
        """
        SELECT id, case_id, assignment_id, assigned_sector_id, status
        FROM case_assignment_tasks
        WHERE id = $1 AND case_id = $2
        """,
        task_id, case_id,
        schema_name=schema_name,
    )
    if not task_row:
        raise NotFoundError("Tarea no encontrada en este expediente.")

    if task_row["status"] != "open":
        raise BusinessLogicError("Solo se puede reasignar una tarea abierta.")

    await _check_task_permission(task_row, case_id, user_id, schema_name=schema_name)

    if assigned_user_id:
        sector_id = str(task_row["assigned_sector_id"])
        user_in_sector = await fetch_one(
            """
            SELECT u.id FROM users u
            WHERE u.id = $1
              AND u.estado = 1
              AND (
                u.sector_id = $2
                OR EXISTS (
                    SELECT 1 FROM user_sector_permissions usp
                    WHERE usp.user_id = u.id AND usp.sector_id = $2
                )
              )
            """,
            assigned_user_id, sector_id,
            schema_name=schema_name,
        )
        if not user_in_sector:
            raise ValidationError(
                "El usuario asignado no pertenece al sector de la tarea."
            )

    status = await execute(
        """
        UPDATE case_assignment_tasks
        SET assigned_user_id = $1
        WHERE id = $2 AND status = 'open'
        """,
        assigned_user_id, task_id,
        schema_name=schema_name,
    )
    if status == "UPDATE 0":
        raise BusinessLogicError("La tarea no pudo ser actualizada (ya fue cerrada o no existe).")

    logger.info(f"update_task: task={task_id}, new_user={assigned_user_id}")

    return {
        "task_id": task_id,
        "assigned_user_id": assigned_user_id,
    }


async def get_assignments_with_tasks(
    case_id: str,
    user_id: str,
    *,
    schema_name: str,
) -> List[Dict[str, Any]]:
    from services.cases.permissions import can_user_view_case

    if not await can_user_view_case(case_id, user_id, schema_name=schema_name):
        raise AuthorizationError("Sin acceso para ver las asignaciones de este expediente.")

    assignments_rows = await fetch_all(
        """
        SELECT
            cm.id,
            cm.created_at,
            s.id AS sector_id,
            s.acronym AS sector_acronym,
            s.primary_color AS sector_color,
            d.name AS department_name,
            d.acronym AS department_acronym
        FROM case_movements cm
        JOIN sectors s ON cm.assigned_sector_id = s.id
        JOIN departments d ON s.department_id = d.id
        WHERE cm.case_id = $1
          AND cm.type = 'assignment'
          AND cm.is_active = true
        ORDER BY cm.created_at ASC
        """,
        case_id,
        schema_name=schema_name,
    )

    if not assignments_rows:
        return []

    assignment_ids = [str(row["id"]) for row in assignments_rows]

    tasks_rows = await fetch_all(
        """
        SELECT
            cat.id,
            cat.assignment_id,
            cat.assigned_user_id,
            cat.reason,
            cat.created_at,
            cat.status,
            u.full_name AS user_full_name,
            u.profile_picture_url AS user_profile_picture_url
        FROM case_assignment_tasks cat
        LEFT JOIN users u ON cat.assigned_user_id = u.id
        WHERE cat.assignment_id = ANY($1::uuid[])
          AND cat.status = 'open'
        ORDER BY cat.created_at ASC
        """,
        assignment_ids,
        schema_name=schema_name,
    )

    tasks_by_assignment: Dict[str, List[Dict]] = {aid: [] for aid in assignment_ids}
    for t in tasks_rows:
        aid = str(t["assignment_id"])
        assigned_user = None
        if t["assigned_user_id"]:
            assigned_user = {
                "id": str(t["assigned_user_id"]),
                "full_name": t["user_full_name"],
                "profile_picture_url": t["user_profile_picture_url"],
            }
        tasks_by_assignment[aid].append({
            "id": str(t["id"]),
            "assigned_user": assigned_user,
            "reason": t["reason"],
            "created_at": t["created_at"].isoformat() if t["created_at"] else None,
            "status": t["status"],
        })

    from services.cases.permissions import get_user_editable_sector_ids
    user_sectors = set(await get_user_editable_sector_ids(user_id, schema_name=schema_name))
    admin_sector_id = await _get_admin_sector_id(case_id, schema_name=schema_name)
    is_case_admin = bool(admin_sector_id and admin_sector_id in user_sectors)

    result = []
    for row in assignments_rows:
        aid = str(row["id"])
        sector_id = str(row["sector_id"])
        result.append({
            "id": aid,
            "sector": {
                "id": sector_id,
                "acronym": row["department_acronym"] + "#" + row["sector_acronym"],
                "color": row["sector_color"],
                "department_name": row["department_name"],
                "department_acronym": row["department_acronym"],
            },
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "can_close": is_case_admin or (sector_id in user_sectors),
            "tasks": tasks_by_assignment.get(aid, []),
        })

    return result


async def get_closed_timeline(
    case_id: str,
    user_id: str,
    *,
    schema_name: str,
) -> Dict[str, Any]:
    from services.cases.permissions import can_user_view_case

    if not await can_user_view_case(case_id, user_id, schema_name=schema_name):
        raise AuthorizationError("Sin acceso para ver el historial de este expediente.")

    closed_rows = await fetch_all(
        """
        SELECT
            cm.id,
            cm.created_at,
            cm.closed_at,
            cm.closing_reason,
            s.id AS sector_id,
            s.acronym AS sector_acronym,
            s.primary_color AS sector_color,
            d.name AS department_name,
            d.acronym AS department_acronym
        FROM case_movements cm
        JOIN sectors s ON cm.assigned_sector_id = s.id
        JOIN departments d ON s.department_id = d.id
        WHERE cm.case_id = $1
          AND cm.type = 'assignment'
          AND cm.is_active = false
        ORDER BY cm.closed_at DESC NULLS LAST, cm.created_at DESC
        """,
        case_id,
        schema_name=schema_name,
    )

    closed_sectors: List[Dict[str, Any]] = []
    if closed_rows:
        closed_ids = [str(r["id"]) for r in closed_rows]

        ctasks_rows = await fetch_all(
            """
            SELECT
                cat.id,
                cat.assignment_id,
                cat.assigned_user_id,
                cat.reason,
                cat.created_at,
                cat.closed_at,
                cat.closed_by,
                cat.status,
                u.full_name AS user_full_name,
                u.profile_picture_url AS user_profile_picture_url,
                cb.full_name AS closer_full_name,
                cb.profile_picture_url AS closer_profile_picture_url
            FROM case_assignment_tasks cat
            LEFT JOIN users u ON cat.assigned_user_id = u.id
            LEFT JOIN users cb ON cat.closed_by = cb.id
            WHERE cat.assignment_id = ANY($1::uuid[])
            ORDER BY cat.created_at ASC
            """,
            closed_ids,
            schema_name=schema_name,
        )

        tasks_by_assignment: Dict[str, List[Dict]] = {aid: [] for aid in closed_ids}
        closer_by_assignment: Dict[str, Dict] = {}
        closer_ts_by_assignment: Dict[str, Any] = {}
        for t in ctasks_rows:
            aid = str(t["assignment_id"])
            assigned_user = None
            if t["assigned_user_id"]:
                assigned_user = {
                    "id": str(t["assigned_user_id"]),
                    "full_name": t["user_full_name"],
                    "profile_picture_url": t["user_profile_picture_url"],
                }
            tasks_by_assignment[aid].append({
                "id": str(t["id"]),
                "assigned_user": assigned_user,
                "reason": t["reason"],
                "created_at": t["created_at"].isoformat() if t["created_at"] else None,
                "closed_at": t["closed_at"].isoformat() if t["closed_at"] else None,
                "status": t["status"],
            })
            if t["closed_by"]:
                prev = closer_ts_by_assignment.get(aid)
                if prev is None or (t["closed_at"] and t["closed_at"] > prev):
                    closer_ts_by_assignment[aid] = t["closed_at"]
                    closer_by_assignment[aid] = {
                        "id": str(t["closed_by"]),
                        "full_name": t["closer_full_name"],
                        "profile_picture_url": t["closer_profile_picture_url"],
                    }

        for row in closed_rows:
            aid = str(row["id"])
            closed_sectors.append({
                "id": aid,
                "sector": {
                    "id": str(row["sector_id"]),
                    "acronym": row["department_acronym"] + "#" + row["sector_acronym"],
                    "color": row["sector_color"],
                    "department_name": row["department_name"],
                    "department_acronym": row["department_acronym"],
                },
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "closed_at": row["closed_at"].isoformat() if row["closed_at"] else None,
                "closing_reason": row["closing_reason"],
                "closed_by": closer_by_assignment.get(aid),
                "tasks": tasks_by_assignment.get(aid, []),
            })

    transfer_rows = await fetch_all(
        """
        SELECT
            cm.id,
            cm.reason,
            cm.created_at,
            cm.user_id,
            u.full_name AS user_full_name,
            u.profile_picture_url AS user_profile_picture_url,
            fs.id AS from_sector_id,
            fs.acronym AS from_sector_acronym,
            fs.primary_color AS from_sector_color,
            fd.name AS from_department_name,
            fd.acronym AS from_department_acronym,
            ts.id AS to_sector_id,
            ts.acronym AS to_sector_acronym,
            ts.primary_color AS to_sector_color,
            td.name AS to_department_name,
            td.acronym AS to_department_acronym
        FROM case_movements cm
        LEFT JOIN users u ON cm.user_id = u.id
        LEFT JOIN sectors fs ON cm.creator_sector_id = fs.id
        LEFT JOIN departments fd ON fs.department_id = fd.id
        LEFT JOIN sectors ts ON cm.assigned_sector_id = ts.id
        LEFT JOIN departments td ON ts.department_id = td.id
        WHERE cm.case_id = $1
          AND cm.type = 'transfer'
        ORDER BY cm.created_at DESC
        """,
        case_id,
        schema_name=schema_name,
    )

    def _sector_obj(prefix_id, acr, color, dept_name, dept_acr):
        if not prefix_id:
            return None
        return {
            "id": str(prefix_id),
            "acronym": (dept_acr + "#" + acr) if dept_acr and acr else (acr or ""),
            "color": color,
            "department_name": dept_name,
            "department_acronym": dept_acr,
        }

    transfers: List[Dict[str, Any]] = []
    for row in transfer_rows:
        user_obj = None
        if row["user_id"]:
            user_obj = {
                "id": str(row["user_id"]),
                "full_name": row["user_full_name"],
                "profile_picture_url": row["user_profile_picture_url"],
            }
        transfers.append({
            "id": str(row["id"]),
            "from_sector": _sector_obj(
                row["from_sector_id"], row["from_sector_acronym"], row["from_sector_color"],
                row["from_department_name"], row["from_department_acronym"],
            ),
            "to_sector": _sector_obj(
                row["to_sector_id"], row["to_sector_acronym"], row["to_sector_color"],
                row["to_department_name"], row["to_department_acronym"],
            ),
            "reason": row["reason"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "user": user_obj,
        })

    creation_rows = await fetch_all(
        """
        SELECT
            cm.id,
            cm.reason,
            cm.created_at,
            cm.user_id,
            u.full_name AS user_full_name,
            u.profile_picture_url AS user_profile_picture_url,
            s.id AS sector_id,
            s.acronym AS sector_acronym,
            s.primary_color AS sector_color,
            d.name AS department_name,
            d.acronym AS department_acronym
        FROM case_movements cm
        LEFT JOIN users u ON cm.user_id = u.id
        LEFT JOIN sectors s
            ON COALESCE(cm.assigned_sector_id, cm.admin_sector_id, cm.creator_sector_id) = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE cm.case_id = $1
          AND cm.type = 'creation'
        ORDER BY cm.created_at ASC
        """,
        case_id,
        schema_name=schema_name,
    )

    creations: List[Dict[str, Any]] = []
    for row in creation_rows:
        user_obj = None
        if row["user_id"]:
            user_obj = {
                "id": str(row["user_id"]),
                "full_name": row["user_full_name"],
                "profile_picture_url": row["user_profile_picture_url"],
            }
        creations.append({
            "id": str(row["id"]),
            "sector": _sector_obj(
                row["sector_id"], row["sector_acronym"], row["sector_color"],
                row["department_name"], row["department_acronym"],
            ),
            "reason": row["reason"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "user": user_obj,
        })

    return {
        "closed_sectors": closed_sectors,
        "transfers": transfers,
        "creations": creations,
    }


async def get_assignable_users(
    case_id: str,
    q: str,
    user_id: str,
    *,
    schema_name: str,
    sector_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from services.cases.permissions import can_user_edit_case
    from services.case_queries import get_assignable_users_query

    if len(q.strip()) < 2:
        return []

    if not await can_user_edit_case(case_id, user_id, schema_name=schema_name):
        raise AuthorizationError("Sin permisos para buscar usuarios asignables.")

    with_sector = sector_id is not None
    query_params: list = [f"%{q.strip()}%"]
    if with_sector:
        query_params.append(sector_id)

    rows = await fetch_all(
        get_assignable_users_query(with_sector=with_sector),
        *query_params,
        schema_name=schema_name,
    )

    users_map: Dict[str, Dict] = {}
    for row in rows:
        uid = str(row["user_id"])
        if uid not in users_map:
            users_map[uid] = {
                "user_id": uid,
                "full_name": row["full_name"],
                "profile_picture_url": row["profile_picture_url"],
                "sectors": [],
            }
        users_map[uid]["sectors"].append({
            "sector_id": str(row["sector_id"]),
            "acronym": str(row["department_acronym"]) + "#" + str(row["sector_acronym"]),
            "department": row["department_name"],
            "can_edit": bool(row["can_edit"]),
        })

    return list(users_map.values())
