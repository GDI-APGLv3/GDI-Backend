
from typing import Dict, Any
import uuid
from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool

from shared.logging import get_logger
from shared.exceptions import ValidationError, DatabaseError, DocumentNotFoundError, DocumentStateError
from config.constants import DEFAULT_LOGO_URL
from services.shared.pdfcomposer_api import call_pdfcomposer_import
from services.storage.cloudflare import get_tenant_r2_client
from database import fetch_all, execute, transaction

logger = get_logger(__name__)


async def create_imported_document(
    user_id: str,
    document_type_acronym: str,
    reference: str,
    pdf_file: UploadFile,
    schema_name: str
) -> Dict[str, Any]:
    logger.info(f"Creando documento importado tipo {document_type_acronym}")
    logger.info(f"Usuario: {user_id}")
    logger.info(f"Referencia: {reference}")
    logger.info(f"Schema: {schema_name}")

    type_result = await fetch_all(
        """
        SELECT dt.id, dt.name, dt.acronym, dt.type
        FROM document_types dt
        WHERE dt.acronym = $1 AND dt.is_active = true
        """,
        document_type_acronym,
        schema_name=schema_name,
    )

    if not type_result:
        raise ValidationError(f"Tipo de documento '{document_type_acronym}' no encontrado o inactivo")

    doc_type = type_result[0]

    if doc_type['type'] != 'Importado':
        raise ValidationError(
            f"El tipo de documento '{document_type_acronym}' no es de tipo Importado. "
            f"Tipo actual: {doc_type['type']}"
        )

    if not pdf_file.content_type or pdf_file.content_type != 'application/pdf':
        raise ValidationError(f"El archivo debe ser un PDF (content-type: {pdf_file.content_type})")

    pdf_bytes = await pdf_file.read()
    pdf_size = len(pdf_bytes)

    logger.info(f"Archivo recibido: {pdf_file.filename}")
    logger.info(f"Tamano: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

    MAX_SIZE = 10 * 1024 * 1024
    if pdf_size > MAX_SIZE:
        raise ValidationError(f"PDF excede tamano maximo (10MB). Tamano: {pdf_size/1024/1024:.2f}MB")

    if not pdf_bytes.startswith(b'%PDF'):
        raise ValidationError("El archivo no es un PDF valido")

    settings_result = await fetch_all(
        "SELECT logo_url FROM settings LIMIT 1",
        schema_name=schema_name,
    )

    logo_url = settings_result[0]['logo_url'] if settings_result else None
    if not logo_url:
        logo_url = DEFAULT_LOGO_URL
        logger.warning(f"No se encontro logo en settings, usando default")

    logger.info(f"Procesando PDF con PDFComposer...")

    try:
        processed_pdf = await call_pdfcomposer_import(
            pdf_file=pdf_bytes,
            filename=pdf_file.filename or 'documento.pdf',
            url_logo=logo_url,
            name_acrony_type=doc_type['acronym'],
            document_type=doc_type['name'],
            reference=reference,
            schema_name=schema_name
        )
    except Exception as e:
        logger.error(f"Error procesando PDF: {str(e)}")
        raise DatabaseError(f"Error al procesar PDF: {str(e)}")

    document_id = str(uuid.uuid4())
    document_id_no_hyphens = document_id.replace('-', '')
    r2_filename = f"{document_id_no_hyphens}.pdf"

    logger.info(f"Subiendo PDF a R2...")
    logger.info(f"Document ID: {document_id}")
    logger.info(f"R2 filename: {r2_filename}")

    try:
        r2_client = await get_tenant_r2_client(schema_name=schema_name)
        upload_result = await run_in_threadpool(r2_client.upload_tosign, processed_pdf, r2_filename)
        logger.info(f"PDF subido a R2: {upload_result['filename']}")
    except Exception as e:
        logger.error(f"Error subiendo a R2: {str(e)}")
        raise DatabaseError(f"Error al subir PDF a almacenamiento: {str(e)}")

    try:
        async with transaction(schema_name=schema_name) as conn:
            await conn.execute(
                """
                INSERT INTO document_draft (
                    id, document_type_id, reference, created_by,
                    last_modified_at, status, content
                ) VALUES (
                    $1, $2, $3, $4,
                    CURRENT_TIMESTAMP, 'draft', NULL
                )
                """,
                document_id,
                doc_type['id'],
                reference,
                user_id,
            )
            logger.info(f"Documento creado en BD: {document_id}")

            await conn.execute(
                """
                INSERT INTO document_signers (
                    document_id, user_id, signing_order, is_numerator
                ) VALUES ($1, $2, $3, $4)
                """,
                document_id,
                user_id,
                1,
                True,
            )
            logger.info(f"Creador asignado como firmante numerador")
    except Exception as e:
        try:
            await run_in_threadpool(r2_client.delete_tosign, r2_filename)
            logger.info(f"PDF eliminado de R2 por rollback")
        except Exception as rollback_err:
            logger.warning(
                f"Fallo el rollback de R2 tras error en BD "
                f"(r2_filename={r2_filename}): {rollback_err}"
            )

        logger.error(f"Error en transaccion BD: {str(e)}")
        raise DatabaseError(f"Error al crear documento: {str(e)}")

    pdf_url = await run_in_threadpool(r2_client.get_tosign_url, r2_filename)

    logger.info(f"====================================")
    logger.info(f"Documento importado creado exitosamente")
    logger.info(f"  ID: {document_id}")
    logger.info(f"  Tipo: {doc_type['name']} ({doc_type['acronym']})")
    logger.info(f"  Referencia: {reference}")
    logger.info(f"====================================")

    return {
        "success": True,
        "document_id": document_id,
        "status": "draft",
        "pdf_url": pdf_url,
        "message": "Documento importado creado exitosamente"
    }


async def replace_imported_pdf(
    document_id: str,
    pdf_file: UploadFile,
    user_id: str,
    schema_name: str
) -> Dict[str, Any]:
    logger.info(f"Reemplazando PDF de documento {document_id}")
    logger.info(f"  Usuario: {user_id}")
    logger.info(f"  Schema: {schema_name}")

    doc_result = await fetch_all(
        """
        SELECT
            dd.id, dd.status, dd.reference, dd.created_by,
            dt.id as type_id, dt.name as type_name, dt.acronym as type_acronym, dt.type as source_type
        FROM document_draft dd
        JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE dd.id = $1
        """,
        document_id,
        schema_name=schema_name,
    )

    if not doc_result:
        raise DocumentNotFoundError(f"Documento {document_id} no encontrado")

    doc = doc_result[0]

    if doc['status'] != 'draft':
        raise DocumentStateError(
            f"Solo se puede reemplazar PDF en documentos en estado draft. Estado actual: {doc['status']}",
            doc['status']
        )

    if doc['source_type'] != 'Importado':
        raise ValidationError(
            f"Solo se puede reemplazar PDF en documentos de tipo Importado. "
            f"Tipo actual: {doc['source_type']}"
        )

    if doc['created_by'] != user_id:
        raise ValidationError("Solo el creador del documento puede reemplazar el PDF")

    if not pdf_file.content_type or pdf_file.content_type != 'application/pdf':
        raise ValidationError(f"El archivo debe ser un PDF (content-type: {pdf_file.content_type})")

    pdf_bytes = await pdf_file.read()
    pdf_size = len(pdf_bytes)

    logger.info(f"Nuevo archivo recibido: {pdf_file.filename}")
    logger.info(f"  Tamano: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

    MAX_SIZE = 10 * 1024 * 1024
    if pdf_size > MAX_SIZE:
        raise ValidationError(f"PDF excede tamano maximo (10MB). Tamano: {pdf_size/1024/1024:.2f}MB")

    if not pdf_bytes.startswith(b'%PDF'):
        raise ValidationError("El archivo no es un PDF valido")

    settings_result = await fetch_all(
        "SELECT logo_url FROM settings LIMIT 1",
        schema_name=schema_name,
    )

    logo_url = settings_result[0]['logo_url'] if settings_result else None
    if not logo_url:
        logo_url = DEFAULT_LOGO_URL

    logger.info(f"Procesando nuevo PDF con PDFComposer...")

    try:
        processed_pdf = await call_pdfcomposer_import(
            pdf_file=pdf_bytes,
            filename=pdf_file.filename or 'documento.pdf',
            url_logo=logo_url,
            name_acrony_type=doc['type_acronym'],
            document_type=doc['type_name'],
            reference=doc['reference'],
            schema_name=schema_name
        )
    except Exception as e:
        logger.error(f"Error procesando PDF: {str(e)}")
        raise DatabaseError(f"Error al procesar PDF: {str(e)}")

    document_id_no_hyphens = document_id.replace('-', '')
    r2_filename = f"{document_id_no_hyphens}.pdf"

    logger.info(f"Reemplazando PDF en R2...")
    logger.info(f"  R2 filename: {r2_filename}")

    try:
        r2_client = await get_tenant_r2_client(schema_name=schema_name)

        await run_in_threadpool(r2_client.delete_tosign, r2_filename)
        logger.info(f"PDF anterior eliminado de R2")

        upload_result = await run_in_threadpool(r2_client.upload_tosign, processed_pdf, r2_filename)
        logger.info(f"Nuevo PDF subido a R2: {upload_result['filename']}")
    except Exception as e:
        logger.error(f"Error en operacion R2: {str(e)}")
        raise DatabaseError(f"Error al actualizar PDF en almacenamiento: {str(e)}")

    try:
        await execute(
            """
            UPDATE document_draft
            SET last_modified_at = CURRENT_TIMESTAMP
            WHERE id = $1
            """,
            document_id,
            schema_name=schema_name,
        )
        logger.info(f"Timestamp actualizado en BD")
    except Exception as e:
        logger.error(f"Error actualizando BD: {str(e)}")
        raise DatabaseError(f"Error al actualizar documento: {str(e)}")

    pdf_url = await run_in_threadpool(r2_client.get_tosign_url, r2_filename)

    logger.info(f"====================================")
    logger.info(f"PDF reemplazado exitosamente")
    logger.info(f"  Document ID: {document_id}")
    logger.info(f"====================================")

    return {
        "success": True,
        "pdf_url": pdf_url,
        "message": "PDF reemplazado exitosamente"
    }
