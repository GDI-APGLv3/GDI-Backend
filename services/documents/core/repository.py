"""
Repository para operaciones de documentos - REFACTORIZADO
Centraliza acceso a datos siguiendo el patron Repository.

UBICACION: services/documents/core/repository.py
MIGRADO: Fase 6 asyncpg
"""
from typing import Dict, Any, List, Optional
from database import fetch_all, fetch_one, fetch_val
from shared.exceptions import DocumentNotFoundError


class DocumentRepository:
    """
    Repository para acceso a datos de documentos.
    Aplica Single Responsibility: Solo acceso a datos.

    IMPORTANTE: Todas las funciones requieren schema_name para multi-tenant.
    """

    @staticmethod
    async def get_basic_details(document_id: str, *, schema_name: str) -> Dict[str, Any]:
        """Obtiene datos basicos del documento."""
        row = await fetch_one(
            """
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
            WHERE d.id = $1
            """,
            document_id,
            schema_name=schema_name,
        )
        if not row:
            raise DocumentNotFoundError(document_id)
        return dict(row)

    @staticmethod
    async def get_signers(document_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
        """Obtiene firmantes del documento con informacion completa."""
        rows = await fetch_all(
            """
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
            WHERE ds.document_id = $1
            ORDER BY ds.signing_order
            """,
            document_id,
            schema_name=schema_name,
        )
        return [dict(r) for r in rows]

    @staticmethod
    async def get_rejection_info(document_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
        """Obtiene informacion del ultimo rechazo."""
        row = await fetch_one(
            """
            SELECT
                dr.reason,
                dr.rejected_at,
                dr.rejected_by,
                u.full_name as rejected_by_name
            FROM document_rejections dr
                LEFT JOIN users u ON dr.rejected_by = u.id
            WHERE dr.document_id = $1
            ORDER BY dr.rejected_at DESC
            LIMIT 1
            """,
            document_id,
            schema_name=schema_name,
        )
        return dict(row) if row else None

    @staticmethod
    async def get_status(document_id: str, *, schema_name: str) -> Optional[str]:
        """Obtiene solo el estado del documento (operacion rapida)."""
        return await fetch_val(
            "SELECT status FROM document_draft WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )

    @staticmethod
    async def exists(document_id: str, *, schema_name: str) -> bool:
        """Verifica si el documento existe."""
        return await DocumentRepository.get_status(document_id, schema_name=schema_name) is not None
