"""Preview Core - Funcion principal del servicio de previsualizacion.
Responsabilidad unica: Orquestar el proceso de generacion de preview.

IMPORTANTE: Preview soporta tres tipos de documentos:
- HTML: Genera PDF desde contenido con PDFComposer /preview-pdf/
- NOTA: Genera PDF con header de recipients usando PDFComposer /note-preview/
- Importado: Retorna URL del PDF ya procesado en R2
"""

from shared.logging import get_logger
from typing import Dict, Any
from shared.exceptions import DocumentStateError
from services.shared.pdfcomposer_api import (
    call_pdfcomposer_preview_pdf,
    call_pdfcomposer_note_preview
)
from services.storage.cloudflare import get_tenant_r2_client
from .data_fetcher import PreviewDataFetcher
from .auto_save import AutoSaveHandler

logger = get_logger(__name__)


async def generate_document_preview(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Genera una previsualización en PDF del documento.

    Orquesta el proceso completo: auto-save, obtención de datos y generación de PDF.

    Soporta dos tipos de documentos:
    - HTML: Genera PDF desde contenido usando PDFComposer /preview-pdf/
    - Importado: Retorna URL firmada del PDF ya procesado en R2 (bucket tosign)

    Args:
        document_id: UUID del documento a previsualizar
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con información de la previsualización generada

    Raises:
        DocumentNotFoundError: Si el documento no existe
        DocumentStateError: Si el documento no está en estado válido
    """
    logger.info(f"Iniciando generación de preview para documento {document_id}")

    try:
        # 1. Aplicar auto-guardado si es necesario (solo para HTML)
        auto_save_handler = AutoSaveHandler(schema_name=schema_name)
        await auto_save_handler.handle_auto_save_if_needed(document_id)

        # 2. Obtener datos completos del documento
        data_fetcher = PreviewDataFetcher(schema_name=schema_name)
        document_data = data_fetcher.get_complete_document_data(document_id)

        # 3. Obtener datos RAW del documento para determinar tipo
        raw_document_data = data_fetcher._fetch_document_basic_info(document_id)

        # 4. Determinar tipo de documento y generar preview
        source_type = raw_document_data.get('source_type', 'HTML')

        if source_type == 'Importado':
            # Para documentos importados: retornar URL del PDF en R2
            logger.info(f"Documento {document_id} es tipo Importado, obteniendo URL de R2")

            document_id_no_hyphens = document_id.replace('-', '')
            r2_filename = f"{document_id_no_hyphens}.pdf"

            r2_client = get_tenant_r2_client(schema_name=schema_name)
            pdf_url = r2_client.get_tosign_url(r2_filename)

            if not pdf_url:
                raise DocumentStateError(
                    "No se pudo obtener URL del PDF importado",
                    "preview_error"
                )

            logger.info(f"Preview URL generada exitosamente para documento importado {document_id}")

            return {
                "success": True,
                "message": "URL de previsualización generada exitosamente",
                "document_id": document_id,
                "document_data": document_data,
                "pdf_url": pdf_url,
                "is_imported": True
            }

        else:
            # Para documentos HTML: generar PDF con PDFComposer
            logger.info(f"Documento {document_id} es tipo HTML, generando PDF con PDFComposer")

            # Detectar si es NOTA para usar endpoint específico
            type_acronym = raw_document_data.get('type_acronym') or raw_document_data.get('document_type_acronym')

            if type_acronym == 'NOTA':
                # NOTA usa /note-preview/ con header de recipients
                logger.info(f"Documento {document_id} es NOTA, usando /note-preview/")

                from services.notes.recipients import format_recipients_for_pdf
                recipients = format_recipients_for_pdf(document_id, schema_name=schema_name)

                pdf_bytes = await call_pdfcomposer_note_preview(
                    raw_document_data,
                    para=recipients['para'],
                    cc=recipients.get('cc')
                )
            else:
                # Otros tipos usan /preview-pdf/ genérico
                pdf_bytes = await call_pdfcomposer_preview_pdf(raw_document_data)

            if not pdf_bytes:
                raise DocumentStateError("No se pudo generar el PDF de previsualización", "preview_error")

            logger.info(
                f"Preview generado exitosamente para documento {document_id} - "
                f"PDF size: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)"
            )

            return {
                "success": True,
                "message": "Previsualización generada exitosamente",
                "document_id": document_id,
                "document_data": document_data,
                "pdf_content": pdf_bytes,
                "is_imported": False
            }

    except Exception as e:
        logger.error(f"Error generando preview para documento {document_id}: {str(e)}")
        raise DocumentStateError(f"Error al generar previsualización: {str(e)}", "preview_error")