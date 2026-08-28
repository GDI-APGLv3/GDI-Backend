
from shared.logging import get_logger
from typing import Dict, Any
from fastapi.concurrency import run_in_threadpool
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
    logger.info(f"Iniciando generación de preview para documento {document_id}")

    try:
        auto_save_handler = AutoSaveHandler(schema_name=schema_name)
        await auto_save_handler.handle_auto_save_if_needed(document_id)

        data_fetcher = PreviewDataFetcher(schema_name=schema_name)
        raw_document_data = await data_fetcher._fetch_document_basic_info(document_id)

        document_data = await data_fetcher.get_complete_document_data(
            document_id, document_info=raw_document_data,
        )

        _content_raw = raw_document_data.get('content')
        if isinstance(_content_raw, dict) and 'html' in _content_raw:
            from services.documents.lifecycle.images import inline_document_images_as_base64
            _content_raw['html'] = await inline_document_images_as_base64(
                _content_raw['html'], document_id, schema_name=schema_name
            )
        elif isinstance(_content_raw, str) and _content_raw:
            from services.documents.lifecycle.images import inline_document_images_as_base64
            raw_document_data['content'] = await inline_document_images_as_base64(
                _content_raw, document_id, schema_name=schema_name
            )

        source_type = raw_document_data.get('source_type', 'HTML')

        if source_type == 'Importado':
            logger.info(f"Documento {document_id} es tipo Importado, obteniendo URL de R2")

            document_id_no_hyphens = document_id.replace('-', '')
            r2_filename = f"{document_id_no_hyphens}.pdf"

            r2_client = await get_tenant_r2_client(schema_name=schema_name)
            pdf_url = await run_in_threadpool(r2_client.get_tosign_url, r2_filename)

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
            logger.info(f"Documento {document_id} es tipo HTML, generando PDF con PDFComposer")

            _base_type = (raw_document_data.get('source_type') or '').upper()

            if _base_type == 'NOTA':
                logger.info(f"Documento {document_id} es NOTA (base_type={_base_type}), usando /note-preview/")

                from services.notes.recipients import format_recipients_for_pdf
                recipients = await format_recipients_for_pdf(document_id, schema_name=schema_name)

                pdf_bytes = await call_pdfcomposer_note_preview(
                    raw_document_data,
                    para=recipients['para'],
                    cc=recipients.get('cc'),
                    schema_name=schema_name
                )
            elif _base_type == 'MEMO':
                logger.info(f"Documento {document_id} es MEMO (base_type={_base_type}), usando /note-preview/")

                from services.memos.recipients import format_memo_recipients_for_pdf
                recipients = await format_memo_recipients_for_pdf(document_id, schema_name=schema_name)

                pdf_bytes = await call_pdfcomposer_note_preview(
                    raw_document_data,
                    para=recipients['para'],
                    cc=recipients.get('cc'),
                    schema_name=schema_name
                )
            else:
                pdf_bytes = await call_pdfcomposer_preview_pdf(raw_document_data, schema_name=schema_name)

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