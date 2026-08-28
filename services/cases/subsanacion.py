
import uuid
from typing import Dict, Any
from database import fetch_all, transaction
from shared.exceptions import ValidationError, NotFoundError, AuthorizationError
from services.cases.permissions import get_user_editable_sector_ids

async def subsanar_document_service(
    case_id: str,
    official_document_id_erroneo: str,
    official_document_id_justifica: str,
    user_id: str,
    *,
    schema_name: str
) -> Dict[str, Any]:

    case_result = await fetch_all("SELECT id FROM cases WHERE id = $1", case_id, schema_name=schema_name)

    if not case_result:
        raise NotFoundError(f"Expediente no encontrado: {case_id}")

    user_sector_ids = await get_user_editable_sector_ids(user_id, schema_name=schema_name)

    if not user_sector_ids:
        raise AuthorizationError("Usuario sin sectores asignados")

    admin_check = """
        SELECT 1 FROM cases c
        WHERE c.id = $1
        AND (
            EXISTS (
                SELECT 1 FROM case_movements cm
                WHERE cm.case_id = c.id
                AND cm.type = 'transfer'
                AND cm.is_active = false
                AND cm.admin_sector_id = ANY($2::uuid[])
                AND cm.closed_at = (
                    SELECT MAX(cm2.closed_at)
                    FROM case_movements cm2
                    WHERE cm2.case_id = c.id
                    AND cm2.type = 'transfer'
                    AND cm2.is_active = false
                )
            )
            OR
            (
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'creation'
                    AND cm.admin_sector_id = ANY($3::uuid[])
                )
                AND NOT EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'transfer'
                )
            )
        )
    """
    admin_result = await fetch_all(admin_check, case_id, user_sector_ids, user_sector_ids, schema_name=schema_name)

    if not admin_result:
        raise AuthorizationError("Solo el sector administrador puede subsanar documentos en este expediente")

    admin_sector_query = """
        SELECT COALESCE(
            (SELECT cm.admin_sector_id FROM case_movements cm
             WHERE cm.case_id = $1 AND cm.type = 'transfer' AND cm.is_active = false
             ORDER BY cm.closed_at DESC LIMIT 1),
            (SELECT cm.admin_sector_id FROM case_movements cm
             WHERE cm.case_id = $2 AND cm.type = 'creation' LIMIT 1)
        ) as admin_sector_id
    """
    sector_result = await fetch_all(admin_sector_query, case_id, case_id, schema_name=schema_name)
    admin_sector_id = str(sector_result[0]['admin_sector_id'])

    doc_erroneo_query = """
        SELECT
            cod.id,
            cod.official_document_id,
            cod.is_active,
            od.official_number,
            od.reference
        FROM case_official_documents cod
        JOIN official_documents od ON cod.official_document_id = od.id
        WHERE cod.case_id = $1
          AND cod.official_document_id = $2
          AND od.signed_at IS NOT NULL
    """
    doc_erroneo_result = await fetch_all(
        doc_erroneo_query,
        case_id, official_document_id_erroneo,
        schema_name=schema_name
    )

    if not doc_erroneo_result:
        raise NotFoundError("El documento erróneo no está vinculado a este expediente")

    doc_erroneo_data = doc_erroneo_result[0]

    if not doc_erroneo_data['is_active']:
        raise ValidationError(
            f"El documento {doc_erroneo_data['official_number']} ya fue subsanado previamente"
        )

    doc_justifica_result = await fetch_all(
        "SELECT id, official_number, reference FROM official_documents WHERE id = $1 AND signed_at IS NOT NULL",
        official_document_id_justifica,
        schema_name=schema_name
    )

    if not doc_justifica_result:
        raise NotFoundError("El documento que justifica no existe en el sistema")

    doc_justifica_data = doc_justifica_result[0]

    duplicate_result = await fetch_all(
        "SELECT 1 FROM case_official_documents WHERE case_id = $1 AND official_document_id = $2 AND is_active = true",
        case_id, official_document_id_justifica,
        schema_name=schema_name
    )

    if duplicate_result:
        raise ValidationError(
            f"El documento {doc_justifica_data['official_number']} ya está vinculado al expediente"
        )

    async with transaction(schema_name=schema_name, user_id=user_id, auth_source="jwt") as conn:
        deactivate_row = await conn.fetchrow(
            """
            UPDATE case_official_documents
            SET
                is_active = false,
                deactivated_at = NOW(),
                deactivated_by_user_id = $1
            WHERE id = $2
            RETURNING deactivated_at
            """,
            user_id, doc_erroneo_data['id']
        )
        deactivated_at = deactivate_row['deactivated_at']

        await conn.execute("SELECT 1 FROM cases WHERE id = $1 FOR UPDATE", case_id)

        max_order_row = await conn.fetchrow(
            "SELECT COALESCE(MAX(order_number), 0) as max_order FROM case_official_documents WHERE case_id = $1",
            case_id
        )
        next_order = (max_order_row['max_order'] + 1) if max_order_row else 1

        link_id = str(uuid.uuid4())
        linking_row = await conn.fetchrow(
            """
            INSERT INTO case_official_documents (
                id, case_id, official_document_id,
                linking_user_id, order_number,
                linking_date, is_active
            ) VALUES (
                $1, $2, $3, $4, $5, NOW(), true
            )
            RETURNING linking_date
            """,
            link_id, case_id, official_document_id_justifica, user_id, next_order
        )
        linking_date = linking_row['linking_date']

        movement_link_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO case_movements (
                id, case_id, type, user_id,
                creator_sector_id, admin_sector_id,
                supporting_document_id,
                reason, is_active, closed_at, closing_reason
            ) VALUES (
                $1, $2, 'document_link', $3, $4, $5, $6, $7, false, NOW(), 'Acción completada'
            )
            """,
            movement_link_id, case_id, user_id, admin_sector_id, admin_sector_id,
            official_document_id_justifica,
            f"Vinculó documento: {doc_justifica_data['official_number']} ({doc_justifica_data['reference']})"
        )

        subsanacion_movement_id = str(uuid.uuid4())
        subsanacion_reason = f"subsanó el documento {doc_erroneo_data['official_number']}, vinculando el documento {doc_justifica_data['official_number']}"

        await conn.execute(
            """
            INSERT INTO case_movements (
                id, case_id, type, user_id,
                creator_sector_id, admin_sector_id,
                reason, is_active, closed_at, closing_reason
            ) VALUES (
                $1, $2, 'subsanacion', $3, $4, $5, $6, false, NOW(), 'Acción completada'
            )
            """,
            subsanacion_movement_id, case_id, user_id, admin_sector_id, admin_sector_id, subsanacion_reason
        )

    return {
        "deactivated_document": {
            "id": str(doc_erroneo_data['id']),
            "official_number": doc_erroneo_data['official_number'],
            "reference": doc_erroneo_data['reference'],
            "deactivated_at": deactivated_at.isoformat() if deactivated_at else None,
            "deactivated_by_user_id": user_id
        },
        "linked_document": {
            "link_id": link_id,
            "official_number": doc_justifica_data['official_number'],
            "document_reference": doc_justifica_data['reference'],
            "order_number": next_order,
            "linking_date": linking_date.isoformat() if hasattr(linking_date, 'isoformat') else str(linking_date)
        }
    }
