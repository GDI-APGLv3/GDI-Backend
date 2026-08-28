
from shared.logging import get_logger
from shared.exceptions import reraise_if_transient

from asyncpg.exceptions import UndefinedColumnError

from database import fetch_val, fetch_all

logger = get_logger(__name__)


async def can_user_view_document(document_id: str, user_id: str, *, schema_name: str) -> bool:
    try:
        query = """
            SELECT (
                (
                    -- Documento PUBLICO: el OR completo de siempre
                    NOT EXISTS (
                        SELECT 1 FROM official_documents od_type
                        JOIN document_types dt_type ON dt_type.id = od_type.document_type_id
                        WHERE od_type.id = $2::uuid AND dt_type.is_reserved = true
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM document_draft dd_type
                        LEFT JOIN document_types dt_type2 ON dt_type2.id = dd_type.document_type_id
                        WHERE dd_type.id = $2::uuid AND COALESCE(dt_type2.is_reserved, false) = true
                    )
                    AND (
                        -- 1. Flag global
                        EXISTS (
                            SELECT 1 FROM document_draft d
                            JOIN users u ON u.id = $1::uuid
                            WHERE d.id = $2::uuid AND u.can_global_search_documents = true AND d.is_deleted = false
                        )

                        -- 2. Es creador
                        OR EXISTS (
                            SELECT 1 FROM document_draft
                            WHERE id = $2::uuid AND created_by = $1::uuid AND is_deleted = false
                        )

                        -- 3. Es firmante
                        OR EXISTS (
                            SELECT 1 FROM document_signers
                            WHERE document_id = $2::uuid AND user_id = $1::uuid
                        )

                        -- 4a. Doc oficial firmado: signer_sector_ids overlaps con sectores del usuario
                        -- IMPORTANTE: el operador && exige UN solo array a la derecha. Por eso
                        -- se hace UN unico ARRAY_AGG sobre el UNION de sector_ids del usuario
                        -- (sector principal + sectores con can_view). Antes habia DOS ARRAY_AGG
                        -- unidos por UNION, que devolvian 2 filas y rompian con
                        -- CardinalityViolationError (atrapado por el try/except -> 403 falso
                        -- para usuarios solo-lectura que no son creador ni firmante).
                        OR EXISTS (
                            SELECT 1 FROM official_documents od
                            WHERE od.id = $2::uuid
                              AND od.signed_at IS NOT NULL
                              AND od.signer_sector_ids && (
                                  SELECT ARRAY_AGG(sid) FROM (
                                      SELECT s.id AS sid FROM users u
                                      JOIN sectors s ON u.sector_id = s.id
                                      WHERE u.id = $1::uuid AND s.is_active = true
                                      UNION
                                      SELECT s2.id FROM users u
                                      JOIN user_sector_permissions usp ON u.id = usp.user_id
                                      JOIN sectors s2 ON usp.sector_id = s2.id
                                      WHERE u.id = $1::uuid AND s2.is_active = true AND usp.can_view = true
                                  ) user_sectors
                              )
                        )

                        -- 4b. Borrador: sector del creador esta en sectores del usuario
                        OR EXISTS (
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
                        )

                        -- 6. Via legajo/RLM: documento OFICIAL FIRMADO vinculado a un
                        -- record cuya registry_family el usuario puede ver (can_view).
                        -- Espeja el predicado de visibilidad por legajo del bloque OR de
                        -- SEMANTIC_SEARCH_SQL (services/search/queries.py) y de
                        -- LOOKUP_DOCUMENT_SQL, para que lo que aparece en la busqueda por
                        -- legajo se pueda abrir. El JOIN a official_documents con
                        -- signed_at IS NOT NULL mantiene la paridad estricta con la
                        -- busqueda (solo oficiales firmados) y NO reabre borradores via
                        -- legajo (los borradores siguen protegidos por casos 1-4b).
                        OR EXISTS (
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
                        )
                    )
                )
                OR
                (
                    -- Documento RESERVADO: SOLO firmante (3) o creador-del-draft (2, C1)
                    (
                        EXISTS (
                            SELECT 1 FROM official_documents od_type
                            JOIN document_types dt_type ON dt_type.id = od_type.document_type_id
                            WHERE od_type.id = $2::uuid AND dt_type.is_reserved = true
                        )
                        OR EXISTS (
                            SELECT 1 FROM document_draft dd_type
                            LEFT JOIN document_types dt_type2 ON dt_type2.id = dd_type.document_type_id
                            WHERE dd_type.id = $2::uuid AND COALESCE(dt_type2.is_reserved, false) = true
                        )
                    )
                    AND (
                        EXISTS (
                            SELECT 1 FROM document_signers
                            WHERE document_id = $2::uuid AND user_id = $1::uuid
                        )
                        OR EXISTS (
                            SELECT 1 FROM document_draft
                            WHERE id = $2::uuid AND created_by = $1::uuid AND is_deleted = false
                        )
                    )
                )
            ) as has_access
        """
        result = await fetch_val(query, user_id, document_id, schema_name=schema_name)
        if bool(result):
            return True

        return await _can_view_via_linked_case(document_id, user_id, schema_name=schema_name)

    except UndefinedColumnError as e:
        logger.error(
            f"Columna faltante evaluando permisos de documento {document_id[:8]} "
            f"(user {user_id[:8]}): {str(e)}. Verificar migracion 082."
        )
        raise
    except Exception as e:
        reraise_if_transient(e, context=f"permisos de vista del documento {document_id[:8]}")
        logger.error(f"Error checking document view permissions: {str(e)}")
        return False


async def _can_view_via_linked_case(document_id: str, user_id: str, *, schema_name: str) -> bool:
    from services.cases.permissions import can_user_view_case

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
