"""Preview Data Fetcher - REFACTORIZADO"""

from shared.logging import get_logger
from typing import Dict, Any
from database import get_db_connection
from shared.exceptions import DocumentNotFoundError
from config.constants import DEFAULT_LOGO_URL
from services.shared.user_data import get_user_complete_data, get_document_signers_for_preview
from services.documents.core.builder import DocumentBuilder
from services.documents.core.queries import get_preview_document_info_query
from services.documents.catalog.states import get_display_state_name
from .document_builder import DocumentResponseBuilder

logger = get_logger(__name__)


class PreviewDataFetcher:
    """Obtiene y estructura datos para generar un preview."""

    def __init__(self, *, schema_name: str):
        """Inicializa el fetcher con el schema del tenant."""
        self.schema_name = schema_name

    def get_complete_document_data(self, document_id: str) -> Dict[str, Any]:
        """Obtiene todos los datos necesarios para el preview del documento."""
        logger.info(f"Obteniendo datos completos para preview de documento {document_id}")

        document_info = self._fetch_document_basic_info(document_id)
        creator_data = get_user_complete_data(str(document_info['created_by']), schema_name=self.schema_name)
        signers_data = get_document_signers_for_preview(document_id, schema_name=self.schema_name)

        builder = DocumentResponseBuilder()
        preview_data = builder.build_preview_response(
            document_info=document_info,
            creator_data=creator_data,
            signers_data=signers_data
        )

        logger.info(f"Datos de preview obtenidos exitosamente para documento {document_id}")
        return preview_data

    def _fetch_document_basic_info(self, document_id: str) -> Dict[str, Any]:
        """Obtiene información básica del documento."""
        with get_db_connection(self.schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(get_preview_document_info_query(), (document_id,))
                result = cursor.fetchone()
                
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
                
                content_text = DocumentBuilder._extract_content(doc_data['content'])
                display_status = get_display_state_name(doc_data['status'], schema_name=self.schema_name)

                # Obtener logo del municipio desde settings
                cursor.execute("SELECT logo_url FROM settings LIMIT 1")
                settings_result = cursor.fetchone()
                logo_url = settings_result['logo_url'] if settings_result and settings_result.get('logo_url') else DEFAULT_LOGO_URL

                return {
                    **doc_data,
                    'content': content_text,
                    'display_status': display_status,
                    'document_type_acronym': doc_data.get('type_acronym'),
                    'document_type_name': doc_data.get('type_name'),
                    'municipality_logo_url': logo_url
                }