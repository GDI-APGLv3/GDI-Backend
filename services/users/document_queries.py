"""
Queries SQL para documentos de usuario.
Centraliza todas las consultas SQL relacionadas con documentos.
Optimizado: CTE de firmantes + count query simplificada.
"""

from typing import List, Dict, Any
from database import execute_query

class DocumentQueries:
    """Maneja todas las consultas SQL para documentos de usuario."""

    @staticmethod
    def _signer_stats_cte() -> str:
        """CTE reutilizable que pre-calcula estadísticas de firmantes por documento.
        Ejecutada 1 sola vez y referenciada en ambas ramas del UNION ALL.
        Reemplaza ~6 subqueries correlacionadas por fila."""
        return """
        signer_stats AS (
            SELECT
                ds.document_id,
                COUNT(*) as total_signers,
                COUNT(*) FILTER (WHERE ds.signed_at IS NOT NULL) as total_signed,
                COUNT(*) FILTER (WHERE ds.is_numerator = false) as common_signers,
                COUNT(*) FILTER (WHERE ds.is_numerator = false AND ds.signed_at IS NOT NULL) as common_signed,
                bool_or(ds.is_numerator AND ds.signed_at IS NULL) as has_unsigned_numerator,
                bool_or(ds.is_numerator) as has_numerator
            FROM document_signers ds
            GROUP BY ds.document_id
        )"""

    @staticmethod
    def get_user_documents_query() -> str:
        """Query principal para obtener documentos de usuario.
        Optimizada con CTE de firmantes."""
        return """
        WITH {signer_stats_cte}
        SELECT * FROM (
            -- Documentos draft
            SELECT DISTINCT
                d.id as id,
                d.reference,
                d.status::text,
                d.last_modified_at as created_at,
                d.last_modified_at as updated_at,
                dt.name as document_type_name,
                dt.acronym as document_type_acronym,
                u_creator.full_name as creator_name,
                (d.created_by = %s) as is_creator,
                COALESCE(ds.is_numerator, false) as is_numerator_for_user,
                COALESCE(ds.signed_at IS NOT NULL, false) as user_already_signed,
                COALESCE(ss.total_signed, 0) as total_signatures,
                COALESCE(ss.total_signers, 0) as required_signatures,
                -- Todos los firmantes comunes firmaron
                COALESCE(ss.common_signers = ss.common_signed, true) as all_common_signers_completed,
                CASE
                    WHEN d.status = 'signed' AND NOT COALESCE(ss.has_unsigned_numerator, false) THEN true
                    ELSE false
                END as is_completed,
                CASE WHEN d.status = 'rejected' THEN true ELSE false END as is_rejected,
                CASE
                    WHEN d.status = 'signed' AND COALESCE(ss.has_numerator, false) THEN true
                    ELSE false
                END as has_pending_numeration,
                d.created_by as last_editor_id,
                u_creator.full_name as last_editor_name,
                u_creator.profile_picture_url as last_editor_profile_picture_url,
                NULL as official_number,
                -- Prioridad: puede firmar ahora
                CASE
                    WHEN d.status = 'sent_to_sign'
                         AND COALESCE(ds.is_numerator, false) = false
                         AND COALESCE(ds.signed_at IS NOT NULL, false) = false
                    THEN 1
                    WHEN d.status IN ('sent_to_sign', 'signed')
                         AND COALESCE(ds.is_numerator, false) = true
                         AND COALESCE(ds.signed_at IS NOT NULL, false) = false
                         AND COALESCE(ss.common_signers = ss.common_signed, true)
                    THEN 1
                    ELSE 0
                END as can_sign_now
            FROM document_draft d
            JOIN document_types dt ON d.document_type_id = dt.id
            JOIN users u_creator ON d.created_by = u_creator.id
            LEFT JOIN document_signers ds ON d.id = ds.document_id AND ds.user_id = %s
            LEFT JOIN signer_stats ss ON ss.document_id = d.id
            WHERE (
                d.created_by = %s
                OR
                (EXISTS (
                    SELECT 1 FROM document_signers s
                    WHERE s.document_id = d.id AND s.user_id = %s
                ) AND d.created_by != %s AND d.status IN ('sent_to_sign', 'signed'))
            )
            AND d.is_deleted = false
            AND NOT EXISTS (
                SELECT 1 FROM official_documents od WHERE od.id = d.id
            )

            UNION ALL

            -- Documentos oficiales
            SELECT DISTINCT
                o.id as id,
                o.reference,
                'signed' as status,
                d.last_modified_at as created_at,
                o.signed_at as updated_at,
                dt.name as document_type_name,
                dt.acronym as document_type_acronym,
                u_creator.full_name as creator_name,
                (d.created_by = %s) as is_creator,
                COALESCE(ds.is_numerator, false) as is_numerator_for_user,
                true as user_already_signed,
                COALESCE(ss.total_signed, 0) as total_signatures,
                COALESCE(ss.total_signers, 0) as required_signatures,
                true as all_common_signers_completed,
                true as is_completed,
                false as is_rejected,
                false as has_pending_numeration,
                o.numerator_id as last_editor_id,
                u_numerator.full_name as last_editor_name,
                u_numerator.profile_picture_url as last_editor_profile_picture_url,
                o.official_number,
                0 as can_sign_now
            FROM official_documents o
            JOIN document_draft d ON o.id = d.id
            JOIN document_types dt ON o.document_type_id = dt.id
            JOIN users u_creator ON d.created_by = u_creator.id
            LEFT JOIN users u_numerator ON o.numerator_id = u_numerator.id
            LEFT JOIN document_signers ds ON ds.document_id = o.id AND ds.user_id = %s
            LEFT JOIN signer_stats ss ON ss.document_id = o.id
            WHERE (
                d.created_by = %s
                OR
                (EXISTS (
                    SELECT 1 FROM document_signers s
                    WHERE s.document_id = o.id AND s.user_id = %s
                ) AND d.created_by != %s)
            )
            AND d.is_deleted = false
        ) AS combined_documents
        WHERE {{where_clause}}
        ORDER BY can_sign_now DESC, updated_at DESC, created_at DESC
        LIMIT %s OFFSET %s
        """.format(signer_stats_cte=DocumentQueries._signer_stats_cte())

    @staticmethod
    def get_count_query() -> str:
        """Query simplificada para contar documentos.
        Sin subqueries innecesarias (required_signatures, etc.)."""
        return """
        SELECT COUNT(*) FROM (
            -- Documentos draft
            SELECT d.id,
                   d.status::text as status,
                   d.last_modified_at as created_at,
                   COALESCE(ds.is_numerator, false) as is_numerator_for_user,
                   COALESCE(ds.signed_at IS NOT NULL, false) as user_already_signed,
                   (SELECT COUNT(*) FROM document_signers signers WHERE signers.document_id = d.id) as required_signatures,
                   d.reference,
                   NULL as official_number,
                   dt.acronym as document_type_acronym
            FROM document_draft d
            JOIN document_types dt ON d.document_type_id = dt.id
            LEFT JOIN document_signers ds ON d.id = ds.document_id AND ds.user_id = %s
            WHERE (
                d.created_by = %s
                OR
                (EXISTS (
                    SELECT 1 FROM document_signers s
                    WHERE s.document_id = d.id AND s.user_id = %s
                ) AND d.created_by != %s AND d.status IN ('sent_to_sign', 'signed'))
            )
            AND d.is_deleted = false
            AND NOT EXISTS (
                SELECT 1 FROM official_documents od WHERE od.id = d.id
            )

            UNION ALL

            -- Documentos oficiales
            SELECT o.id,
                   'signed' as status,
                   d.last_modified_at as created_at,
                   COALESCE(ds.is_numerator, false) as is_numerator_for_user,
                   true as user_already_signed,
                   (SELECT COUNT(*) FROM document_signers signers WHERE signers.document_id = o.id) as required_signatures,
                   o.reference,
                   o.official_number,
                   dt.acronym as document_type_acronym
            FROM official_documents o
            JOIN document_draft d ON o.id = d.id
            JOIN document_types dt ON o.document_type_id = dt.id
            LEFT JOIN document_signers ds ON ds.document_id = o.id AND ds.user_id = %s
            WHERE (
                d.created_by = %s
                OR
                (EXISTS (
                    SELECT 1 FROM document_signers s
                    WHERE s.document_id = o.id AND s.user_id = %s
                ) AND d.created_by != %s)
            )
            AND d.is_deleted = false
        ) AS combined_count
        WHERE {where_clause}
        """
    
    @staticmethod
    def execute_documents_query(user_id: str, where_clause: str,
                              filter_params: List[str], page_size: int,
                              offset: int, schema_name: str) -> List[Dict[str, Any]]:
        """
        Ejecuta la consulta principal de documentos.

        Args:
            user_id: ID del usuario
            where_clause: Cláusula WHERE construida
            filter_params: Parámetros de los filtros
            page_size: Tamaño de página
            offset: Offset para paginación
            schema_name: Nombre del schema a usar

        Returns:
            Lista de documentos
        """
        query = DocumentQueries.get_user_documents_query().format(where_clause=where_clause)

        # Parámetros exactos: 10 user_id fijos + filter_params + LIMIT + OFFSET
        final_params = [
            # Para consulta draft (5 user_id)
            user_id,  # 1. is_creator
            user_id,  # 2. ds.user_id (LEFT JOIN)
            user_id,  # 3. created_by (WHERE)
            user_id,  # 4. s.user_id (EXISTS)
            user_id,  # 5. created_by != (WHERE)
            # Para consulta official (5 user_id)
            user_id,  # 6. is_creator
            user_id,  # 7. ds.user_id (LEFT JOIN)
            user_id,  # 8. created_by (WHERE)
            user_id,  # 9. s.user_id (EXISTS)
            user_id   # 10. created_by != (WHERE)
        ] + filter_params + [page_size, offset]

        return execute_query(query, final_params, schema_name=schema_name)
    
    @staticmethod
    def execute_count_query(user_id: str, where_clause: str, filter_params: List[str], schema_name: str) -> int:
        """
        Ejecuta la consulta de conteo.

        Args:
            user_id: ID del usuario
            where_clause: Cláusula WHERE construida
            filter_params: Parámetros de los filtros
            schema_name: Nombre del schema a usar

        Returns:
            Número total de documentos
        """
        query = DocumentQueries.get_count_query().format(where_clause=where_clause)

        # Parámetros para count: draft(4x) + official(4x) + filter_params
        final_params = [
            # Para consulta draft (4 user_id)
            user_id,  # ds.user_id (LEFT JOIN)
            user_id,  # created_by (WHERE)
            user_id,  # s.user_id (EXISTS)
            user_id,  # created_by != (WHERE)
            # Para consulta official (4 user_id)
            user_id,  # ds.user_id (LEFT JOIN)
            user_id,  # created_by (WHERE)
            user_id,  # s.user_id (EXISTS)
            user_id   # created_by != (WHERE)
        ] + filter_params

        result = execute_query(query, final_params, schema_name=schema_name)
        return result[0]['count'] if result else 0