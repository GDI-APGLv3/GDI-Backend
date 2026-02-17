"""
Repository para operaciones de documentos - REFACTORIZADO
Centraliza acceso a datos siguiendo el patron Repository.

UBICACION: services/documents/core/repository.py
MIGRADO: Fase 2.2 del refactoring
"""
from typing import Dict, Any, List, Optional
from database import get_db_connection
from shared.exceptions import DocumentNotFoundError


class DocumentRepository:
    """
    Repository para acceso a datos de documentos.
    Aplica Single Responsibility: Solo acceso a datos.

    IMPORTANTE: Todas las funciones requieren schema_name para multi-tenant.
    Compatible con PgBouncer transaction mode (SET LOCAL).
    """

    @staticmethod
    def get_basic_details(document_id: str, *, schema_name: str) -> Dict[str, Any]:
        """Obtiene datos basicos del documento."""
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        d.id as id,
                        d.reference,
                        d.content,
                        d.status,
                        d.created_by as creator_id,
                        d.last_modified_at,
                        dt.name as document_type_name,
                        dt.acronym as document_type_acronym,
                        u.full_name as creator_name
                    FROM document_draft d
                        LEFT JOIN document_types dt ON d.document_type_id = dt.id
                        LEFT JOIN users u ON d.created_by = u.id
                    WHERE d.id = %s
                """, (document_id,))

                document = cursor.fetchone()
                if not document:
                    raise DocumentNotFoundError(document_id)

                return document

    @staticmethod
    def get_signers(document_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
        """Obtiene firmantes del documento con informacion completa."""
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        ds.user_id,
                        ds.signing_order,
                        ds.is_numerator,
                        u.full_name as user_name,
                        u.email,
                        u.profile_picture_url,
                        cs.name as seal_name,
                        d.acronym as department_acronym
                    FROM document_signers ds
                        LEFT JOIN users u ON ds.user_id = u.id
                        LEFT JOIN user_seals us ON u.id = us.user_id
                        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
                        LEFT JOIN sectors s ON u.sector_id = s.id
                        LEFT JOIN departments d ON s.department_id = d.id
                    WHERE ds.document_id = %s
                    ORDER BY ds.signing_order
                """, (document_id,))

                return cursor.fetchall()

    @staticmethod
    def get_rejection_info(document_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
        """Obtiene informacion del ultimo rechazo."""
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        dr.reason,
                        dr.rejected_at,
                        dr.rejected_by,
                        u.full_name as rejected_by_name
                    FROM document_rejections dr
                        LEFT JOIN users u ON dr.rejected_by = u.id
                    WHERE dr.document_id = %s
                    ORDER BY dr.rejected_at DESC
                    LIMIT 1
                """, (document_id,))

                return cursor.fetchone()

    @staticmethod
    def get_status(document_id: str, *, schema_name: str) -> Optional[str]:
        """Obtiene solo el estado del documento (operacion rapida)."""
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM document_draft WHERE id = %s",
                    (document_id,)
                )
                result = cursor.fetchone()
                return result['status'] if result else None

    @staticmethod
    def exists(document_id: str, *, schema_name: str) -> bool:
        """Verifica si el documento existe."""
        return DocumentRepository.get_status(document_id, schema_name=schema_name) is not None
