"""
Permisos de documentos.
Verifica si un usuario puede ver un documento segun reglas de negocio.
MIGRADO: Fase 6 asyncpg
"""

import logging

from database import fetch_val, fetch_all

logger = logging.getLogger(__name__)


async def can_user_view_document(document_id: str, user_id: str, *, schema_name: str) -> bool:
    """
    Verifica si el usuario puede ver el documento.

    Condiciones (al menos una debe cumplirse):
    1. Flag global: users.can_global_search_documents = true
    2. Es creador: document_draft.created_by = user_id
    3. Es firmante: existe fila en document_signers con user_id
    4. Esta en sectores del usuario:
       - Docs oficiales: signer_sector_ids overlaps con sectores del usuario
       - Borradores: sector del creador esta en sectores del usuario
    5. Via expediente: el documento esta vinculado (case_official_documents
       activo) a un expediente que el usuario puede ver
       (can_user_view_case == True). Hace coherente "si veo el expediente,
       puedo abrir sus documentos".
    6. Via legajo/RLM: el documento OFICIAL FIRMADO esta vinculado
       (record_document_links) a un registro cuya familia (registry_family) el
       usuario puede ver (registry_family_permissions.can_view = true en algun
       sector del usuario). Espeja el predicado de visibilidad por legajo del
       bloque OR de SEMANTIC_SEARCH_SQL / LOOKUP_DOCUMENT_SQL
       (services/search/queries.py): lo que aparece en la busqueda por legajo
       se puede abrir. Solo oficiales firmados (los borradores quedan
       protegidos por los casos 1-4b).

    Args:
        document_id: UUID del documento
        user_id: UUID del usuario
        schema_name: Schema del tenant (keyword-only)

    Returns:
        bool: True si el usuario puede ver el documento
    """
    try:
        # $1 = user_id, $2 = document_id (reutilizados en todo el query)
        query = """
            SELECT EXISTS(
                -- 1. Flag global
                SELECT 1 FROM document_draft d
                JOIN users u ON u.id = $1::uuid
                WHERE d.id = $2::uuid AND u.can_global_search_documents = true AND d.is_deleted = false

                UNION ALL

                -- 2. Es creador
                SELECT 1 FROM document_draft
                WHERE id = $2::uuid AND created_by = $1::uuid AND is_deleted = false

                UNION ALL

                -- 3. Es firmante
                SELECT 1 FROM document_signers
                WHERE document_id = $2::uuid AND user_id = $1::uuid

                UNION ALL

                -- 4a. Doc oficial firmado: signer_sector_ids overlaps con sectores del usuario
                SELECT 1 FROM official_documents od
                WHERE od.id = $2::uuid
                  AND od.signed_at IS NOT NULL
                  AND od.signer_sector_ids && (
                      SELECT ARRAY_AGG(s.id) FROM users u
                      JOIN sectors s ON u.sector_id = s.id
                      WHERE u.id = $1::uuid AND s.is_active = true
                      UNION
                      SELECT ARRAY_AGG(s2.id) FROM users u
                      JOIN user_sector_permissions usp ON u.id = usp.user_id
                      JOIN sectors s2 ON usp.sector_id = s2.id
                      WHERE u.id = $1::uuid AND s2.is_active = true AND usp.can_view = true
                  )

                UNION ALL

                -- 4b. Borrador: sector del creador esta en sectores del usuario
                SELECT 1 FROM document_draft d
                JOIN users creator ON d.created_by = creator.id
                WHERE d.id = $2::uuid AND d.is_deleted = false
                  AND creator.sector_id IN (
                      SELECT s.id FROM users u
                      JOIN sectors s ON u.sector_id = s.id
                      WHERE u.id = $1::uuid AND s.is_active = true
                      UNION
                      SELECT s2.id FROM users u
                      JOIN user_sector_permissions usp ON u.id = usp.user_id
                      JOIN sectors s2 ON usp.sector_id = s2.id
                      WHERE u.id = $1::uuid AND s2.is_active = true AND usp.can_view = true
                  )

                UNION ALL

                -- 6. Via legajo/RLM: documento OFICIAL FIRMADO vinculado a un
                -- record cuya registry_family el usuario puede ver (can_view).
                -- Espeja el predicado de visibilidad por legajo del bloque OR de
                -- SEMANTIC_SEARCH_SQL (services/search/queries.py) y de
                -- LOOKUP_DOCUMENT_SQL, para que lo que aparece en la busqueda por
                -- legajo se pueda abrir. El JOIN a official_documents con
                -- signed_at IS NOT NULL mantiene la paridad estricta con la
                -- busqueda (solo oficiales firmados) y NO reabre borradores via
                -- legajo (los borradores siguen protegidos por casos 1-4b).
                SELECT 1 FROM record_document_links rdl
                JOIN records r ON r.id = rdl.record_id
                JOIN registry_families rf ON rf.id = r.registry_family_id
                JOIN registry_family_permissions rfp
                  ON rfp.registry_family_id = r.registry_family_id
                JOIN official_documents od_link
                  ON od_link.id = rdl.document_id AND od_link.signed_at IS NOT NULL
                WHERE rdl.document_id = $2::uuid
                  AND rf.is_active = true
                  AND r.state = 'Activo'
                  AND rfp.can_view = true
                  AND rfp.sector_id IN (
                      SELECT s.id FROM users u
                      JOIN sectors s ON u.sector_id = s.id
                      WHERE u.id = $1::uuid AND s.is_active = true
                      UNION
                      SELECT s2.id FROM users u
                      JOIN user_sector_permissions usp ON u.id = usp.user_id
                      JOIN sectors s2 ON usp.sector_id = s2.id
                      WHERE u.id = $1::uuid AND s2.is_active = true AND usp.can_view = true
                  )
            ) as has_access
        """
        result = await fetch_val(query, user_id, document_id, schema_name=schema_name)
        if bool(result):
            return True

        # 5. Via expediente: si el documento esta vinculado a un expediente
        #    que el usuario puede ver, puede abrir el documento.
        #    Reutilizamos can_user_view_case (fuente de verdad de permisos de
        #    expediente) en vez de re-implementar la logica de movimientos.
        return await _can_view_via_linked_case(document_id, user_id, schema_name=schema_name)

    except Exception as e:
        logger.error(f"Error checking document view permissions: {str(e)}")
        return False


async def _can_view_via_linked_case(document_id: str, user_id: str, *, schema_name: str) -> bool:
    """
    True si el documento (oficial) esta vinculado via case_official_documents
    activo a algun expediente que el usuario puede ver.

    Reutiliza can_user_view_case de services.cases.permissions para mantener
    una unica fuente de verdad sobre permisos de expediente (sector asignado,
    admin por transferencia/creacion, creador, flag global de cases).
    """
    from services.cases.permissions import can_user_view_case

    # Buscar expedientes activos a los que el documento esta vinculado.
    # case_official_documents.official_document_id referencia official_documents.id,
    # que comparte id con document_draft.id (mismo UUID en todo el ciclo de vida).
    case_rows = await fetch_all(
        """
        SELECT DISTINCT cod.case_id
        FROM case_official_documents cod
        WHERE cod.official_document_id = $1::uuid
          AND cod.is_active = true
        """,
        document_id,
        schema_name=schema_name,
    )

    if not case_rows:
        return False

    for row in case_rows:
        case_id = str(row["case_id"])
        if await can_user_view_case(case_id, user_id, schema_name=schema_name):
            return True

    return False
