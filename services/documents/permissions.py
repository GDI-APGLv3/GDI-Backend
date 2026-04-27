"""
Permisos de documentos.
Verifica si un usuario puede ver un documento segun reglas de negocio.
"""

import logging

from database import execute_query

logger = logging.getLogger(__name__)


def can_user_view_document(document_id: str, user_id: str, *, schema_name: str) -> bool:
    """
    Verifica si el usuario puede ver el documento.

    Condiciones (al menos una debe cumplirse):
    1. Flag global: users.can_global_search_documents = true
    2. Es creador: document_draft.created_by = user_id
    3. Es firmante: existe fila en document_signers con user_id
    4. Esta en sectores del usuario:
       - Docs oficiales: signer_sector_ids overlaps con sectores del usuario
       - Borradores: sector del creador esta en sectores del usuario

    Args:
        document_id: UUID del documento
        user_id: UUID del usuario
        schema_name: Schema del tenant (keyword-only)

    Returns:
        bool: True si el usuario puede ver el documento
    """
    try:
        from services.case_queries import get_user_sectors_for_case_query
        USER_SECTORS = get_user_sectors_for_case_query()

        query = f"""
            SELECT EXISTS(
                -- 1. Flag global
                SELECT 1 FROM document_draft d
                JOIN users u ON u.id = %s
                WHERE d.id = %s AND u.can_global_search_documents = true AND d.is_deleted = false

                UNION ALL

                -- 2. Es creador
                SELECT 1 FROM document_draft
                WHERE id = %s AND created_by = %s AND is_deleted = false

                UNION ALL

                -- 3. Es firmante
                SELECT 1 FROM document_signers
                WHERE document_id = %s AND user_id = %s

                UNION ALL

                -- 4a. Doc oficial firmado: signer_sector_ids overlaps con sectores del usuario
                SELECT 1 FROM official_documents od
                JOIN ({USER_SECTORS}) us ON od.signer_sector_ids && ARRAY[us.sector_id]::uuid[]
                WHERE od.id = %s
                  AND od.signed_at IS NOT NULL

                UNION ALL

                -- 4b. Borrador: sector del creador esta en sectores del usuario
                SELECT 1 FROM document_draft d
                JOIN users creator ON d.created_by = creator.id
                JOIN ({USER_SECTORS}) us ON creator.sector_id = us.sector_id
                WHERE d.id = %s AND d.is_deleted = false
            ) as has_access
        """
        params = (
            user_id, document_id,           # flag global
            document_id, user_id,           # creador
            document_id, user_id,           # firmante
            user_id, user_id, document_id,  # oficial+sectores (USER_SECTORS usa 2 %s)
            user_id, user_id, document_id,  # borrador+sectores (USER_SECTORS usa 2 %s)
        )
        result = execute_query(query, params, schema_name=schema_name)
        return result and result[0].get('has_access', False)

    except Exception as e:
        logger.error(f"Error checking document view permissions: {str(e)}")
        return False
