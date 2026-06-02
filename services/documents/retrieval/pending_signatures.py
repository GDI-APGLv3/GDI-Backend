"""
Servicio para obtener documentos pendientes de firma de un usuario.

Devuelve los documentos donde el usuario es el PRÓXIMO firmante (es su turno):
- su firma está 'pending'
- el documento está 'sent_to_sign'
- no hay firmantes con signing_order menor aún pendientes (dentro de su cola
  numerador/firmante)

Misma lógica que el endpoint REST del Gateway (SET B) `documents.get_pending_signatures`.
"""
from typing import Dict, Any
from shared.logging import get_logger
from database import fetch_all

logger = get_logger(__name__)


_PENDING_SIGNATURES_QUERY = """
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
            dt.acronym as document_type_acronym,
            dt.name as document_type_name,
            u_creator.full_name as creator_name,
            u_creator.profile_picture_url as creator_photo
        FROM document_signers ds
        JOIN document_draft d ON ds.document_id = d.id
        JOIN document_types dt ON d.document_type_id = dt.id
        JOIN users u_creator ON d.created_by = u_creator.id
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
        NOT EXISTS (
            SELECT 1 FROM document_signers ds2
            WHERE ds2.document_id = pd.document_id
              AND ds2.signing_order < pd.signing_order
              AND ds2.status = 'pending'
              AND ds2.is_numerator = pd.is_numerator
        ) as is_my_turn
    FROM pending_docs pd
    WHERE NOT EXISTS (
        SELECT 1 FROM document_signers ds2
        WHERE ds2.document_id = pd.document_id
          AND ds2.signing_order < pd.signing_order
          AND ds2.status = 'pending'
          AND ds2.is_numerator = pd.is_numerator
    )
    ORDER BY pd.sent_to_sign_at DESC
"""


async def get_pending_signatures_for_user(user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Obtener documentos pendientes de firma donde es el turno del usuario.

    Args:
        user_id: UUID del usuario
        schema_name: Schema de la municipalidad (keyword-only)

    Returns:
        Dict con:
        - pending_signatures: lista de documentos pendientes
        - total: cantidad de documentos pendientes

    Raises:
        ValueError: Si user_id no proporcionado
    """
    if not user_id:
        raise ValueError("user_id es requerido")

    results = await fetch_all(_PENDING_SIGNATURES_QUERY, user_id, schema_name=schema_name)

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
