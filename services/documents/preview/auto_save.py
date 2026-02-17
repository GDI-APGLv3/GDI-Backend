"""Auto Save Handler - Manejo del guardado automático de documentos."""

from shared.logging import get_logger
from database import get_db_connection
from shared.exceptions import DocumentNotFoundError
from config.constants import EDITABLE_DOCUMENT_STATES

logger = get_logger(__name__)


class AutoSaveHandler:
    """Maneja la lógica de auto-guardado para documentos en estados editables."""

    def __init__(self, *, schema_name: str):
        """Inicializa el handler con el schema del tenant."""
        self.schema_name = schema_name

    async def handle_auto_save_if_needed(self, document_id: str) -> None:
        """Verifica que el documento esté en estado editable."""
        document_status = self._get_document_status(document_id)

        if document_status not in EDITABLE_DOCUMENT_STATES:
            logger.info(f"Documento {document_id} no requiere auto-save (estado: {document_status})")
            return

        logger.info(f"Documento {document_id} verificado para auto-save (estado: {document_status})")

    def _get_document_status(self, document_id: str) -> str:
        """Obtiene el estado actual del documento."""
        with get_db_connection(self.schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM document_draft WHERE id = %s",
                    (document_id,)
                )
                result = cursor.fetchone()

                if not result:
                    raise DocumentNotFoundError(f"Documento {document_id} no encontrado")

                return result['status']
