from typing import Dict, Any, Optional
from shared.logging import get_logger
from database import fetch_all, fetch_one

logger = get_logger(__name__)


def _is_my_turn_condition(alias: str) -> str:
    return f"""
        NOT EXISTS (
            SELECT 1 FROM document_signers ds2
            WHERE ds2.document_id = {alias}.document_id
              AND ds2.status = 'pending'
              AND (
                  ({alias}.is_numerator = true AND ds2.is_numerator = false)
                  OR
                  ({alias}.is_numerator = false AND ds2.is_numerator = false
                   AND ds2.signing_order < {alias}.signing_order)
              )
        )
    """


_PENDING_SIGNATURES_QUERY = f"""
    WITH pending_docs AS (
        SELECT
            ds.id as signer_id,
            ds.document_id,
            ds.is_numerator,
            ds.signing_order,
            ds.status as signer_status,
            d.id as draft_id,
            d.reference,
            d.document_number,
            d.sent_to_sign_at,
            d.status as doc_status,
            d.short_resume,
            dt.acronym as document_type_acronym,
            dt.name as document_type_name,
            COALESCE(u_creator.full_name, c_creator.full_name) as creator_name,
            u_creator.profile_picture_url as creator_photo
        FROM document_signers ds
        JOIN document_draft d ON ds.document_id = d.id
        JOIN document_types dt ON d.document_type_id = dt.id
        LEFT JOIN users u_creator ON d.created_by = u_creator.id
        LEFT JOIN citizens c_creator ON d.created_by_citizen = c_creator.id
        WHERE ds.user_id = $1
          AND ds.status = 'pending'
          AND d.status = 'sent_to_sign'
    )
    SELECT
        pd.*,
        CASE
            WHEN pd.is_numerator = true THEN 'numerator'
            ELSE 'signer'
        END as signer_role,
        -- Siempre true (el WHERE de abajo filtra con la misma condición). Se
        -- mantiene porque el contrato del endpoint REST lo expone.
        {_is_my_turn_condition('pd')} as is_my_turn
    FROM pending_docs pd
    WHERE {_is_my_turn_condition('pd')}
    ORDER BY pd.sent_to_sign_at DESC
"""


_PENDING_SIGNATURES_COUNT_QUERY = f"""
    SELECT COUNT(*) AS total
    FROM document_signers ds
    JOIN document_draft d ON ds.document_id = d.id
    JOIN document_types dt ON d.document_type_id = dt.id
    WHERE ds.user_id = $1
      AND ds.status = 'pending'
      AND d.status = 'sent_to_sign'
      AND {_is_my_turn_condition('ds')}
"""


async def count_pending_signatures_for_user(user_id: str, *, schema_name: str) -> int:
    if not user_id:
        raise ValueError("user_id es requerido")

    row = await fetch_one(_PENDING_SIGNATURES_COUNT_QUERY, user_id, schema_name=schema_name)
    return int(row["total"]) if row else 0


async def get_pending_signatures_for_user(
    user_id: str,
    *,
    schema_name: str,
    limit: Optional[int] = None,
    conn=None,
) -> Dict[str, Any]:
    if not user_id:
        raise ValueError("user_id es requerido")

    query = _PENDING_SIGNATURES_QUERY
    params = [user_id]
    if limit is not None:
        params.append(limit)
        query = f"{query} LIMIT ${len(params)}"

    if conn is not None:
        results = await conn.fetch(query, *params)
    else:
        results = await fetch_all(query, *params, schema_name=schema_name)

    pending_signatures = []
    for row in results:
        pending_signatures.append({
            "document_id": str(row["document_id"]),
            "reference": row["reference"],
            "document_number": row["document_number"],
            "document_type": {
                "acronym": row["document_type_acronym"],
                "name": row["document_type_name"],
            },
            "signer_role": row["signer_role"],
            "signing_order": row["signing_order"],
            "sent_to_sign_at": str(row["sent_to_sign_at"]) if row["sent_to_sign_at"] else None,
            "short_resume": row["short_resume"],
            "creator": {
                "name": row["creator_name"],
                "photo_url": row["creator_photo"],
            },
        })

    logger.info(f"Found {len(pending_signatures)} pending signatures for user {user_id}")

    return {
        "pending_signatures": pending_signatures,
        "total": len(pending_signatures),
    }
