
from shared.logging import get_logger
from typing import Dict, Any
from shared.exceptions import DocumentNotFoundError
from config.constants import DEFAULT_LOGO_URL
from services.shared.user_data import get_user_complete_data, get_document_signers_for_preview
from services.documents.core.builder import DocumentBuilder
from services.documents.core.queries import get_preview_document_info_query
from services.documents.catalog.states import get_display_state_name
from .document_builder import DocumentResponseBuilder

logger = get_logger(__name__)


class PreviewDataFetcher:

    def __init__(self, *, schema_name: str):
        self.schema_name = schema_name

    async def get_complete_document_data(
        self, document_id: str, *, document_info: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        logger.info(f"Obteniendo datos completos para preview de documento {document_id}")

        if document_info is None:
            document_info = await self._fetch_document_basic_info(document_id)
        creator_data = await get_user_complete_data(str(document_info['created_by']), schema_name=self.schema_name)
        signers_data = await get_document_signers_for_preview(document_id, schema_name=self.schema_name)

        builder = DocumentResponseBuilder()
        preview_data = builder.build_preview_response(
            document_info=document_info,
            creator_data=creator_data,
            signers_data=signers_data
        )

        logger.info(f"Datos de preview obtenidos exitosamente para documento {document_id}")
        return preview_data

    async def _fetch_document_basic_info(self, document_id: str) -> Dict[str, Any]:
        from database import get_conn

        async with get_conn(schema_name=self.schema_name) as conn:
            result = await conn.fetchrow(get_preview_document_info_query(), document_id)

            if not result:
                raise DocumentNotFoundError(f"Documento {document_id} no encontrado")

            doc_data = dict(result)

            if not doc_data.get('type_acronym') or not doc_data.get('type_name'):
                logger.error(
                    f"Documento {document_id} sin tipo válido - "
                    f"type_id: {doc_data.get('document_type_id')}, "
                    f"acronym: {doc_data.get('type_acronym')}"
                )
                raise DocumentNotFoundError(
                    "El documento no tiene un tipo de documento válido. "
                    "Asigne un tipo antes de generar preview."
                )

            fd_row = await conn.fetchrow(
                "SELECT field_definitions FROM document_type_fields "
                "WHERE document_type_id = $1",
                doc_data.get('document_type_id'),
            )
            if fd_row is not None:
                field_defs = fd_row['field_definitions'] if fd_row else []
                raw_data = doc_data['content'] if isinstance(doc_data['content'], dict) else {}
                from services.documents.ffcc_renderer import ffcc_to_html
                content_text = ffcc_to_html(field_defs, raw_data)
                logger.info(f"Formulario controlado preview HTML generado: {len(content_text)} chars")
            else:
                content_text = DocumentBuilder._extract_content(doc_data['content'])

            display_status = await get_display_state_name(doc_data['status'], schema_name=self.schema_name, conn=conn)

            settings_result = await conn.fetchrow("SELECT logo_url FROM settings LIMIT 1")
            logo_url = settings_result['logo_url'] if settings_result and settings_result.get('logo_url') else DEFAULT_LOGO_URL

            return {
                **doc_data,
                'content': content_text,
                'display_status': display_status,
                'document_type_acronym': doc_data.get('type_acronym'),
                'document_type_name': doc_data.get('type_name'),
                'municipality_logo_url': logo_url
            }
