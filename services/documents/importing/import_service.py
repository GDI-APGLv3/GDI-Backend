"""
Servicio para creacion y gestion de documentos importados (PDFs subidos).

Los documentos importados permiten a usuarios subir un PDF existente
y luego firmarlo digitalmente. El PDF es procesado por PDFComposer
para agregar una hoja final con informacion para firmas.
"""

from typing import Dict, Any
import uuid
import os
from fastapi import UploadFile

from shared.logging import get_logger
from shared.exceptions import ValidationError, DatabaseError, DocumentNotFoundError, DocumentStateError
from config.constants import DEFAULT_LOGO_URL
from services.shared.pdfcomposer_api import call_pdfcomposer_import
from services.storage.cloudflare import get_tenant_r2_client
from database import execute_query, execute_transaction, execute_update

logger = get_logger(__name__)


async def create_imported_document(
    user_id: str,
    document_type_acronym: str,
    reference: str,
    pdf_file: UploadFile,
    schema_name: str
) -> Dict[str, Any]:
    """
    Crea un nuevo documento importado subiendo un PDF.

    Flujo:
    1. Validar tipo de documento tiene type='Importado'
    2. Validar archivo es PDF y <=10MB
    3. Obtener logo del municipio desde settings
    4. Llamar PDFComposer /import/ para procesar PDF
    5. Subir PDF procesado a R2 bucket 'tosign'
    6. Crear registro en document_draft (content=NULL)
    7. Asignar creador como firmante numerador

    Args:
        user_id: UUID del usuario creador
        document_type_acronym: Acronimo del tipo de documento
        reference: Referencia del documento
        pdf_file: Archivo PDF subido
        schema_name: Schema del tenant

    Returns:
        Dict con document_id, status, pdf_url, message

    Raises:
        ValidationError: Si tipo no es Importado, archivo invalido, etc.
        DatabaseError: Si falla operacion en BD
    """
    logger.info(f"Creando documento importado tipo {document_type_acronym}")
    logger.info(f"Usuario: {user_id}")
    logger.info(f"Referencia: {reference}")
    logger.info(f"Schema: {schema_name}")

    # 1. Validar tipo de documento tiene type='Importado'
    query_type = """
        SELECT dt.id, dt.name, dt.acronym, dt.type
        FROM document_types dt
        WHERE dt.acronym = %s AND dt.is_active = true
    """
    type_result = execute_query(query_type, (document_type_acronym,), schema_name=schema_name)

    if not type_result:
        raise ValidationError(f"Tipo de documento '{document_type_acronym}' no encontrado o inactivo")

    doc_type = type_result[0]

    if doc_type['type'] != 'Importado':
        raise ValidationError(
            f"El tipo de documento '{document_type_acronym}' no es de tipo Importado. "
            f"Tipo actual: {doc_type['type']}"
        )

    # 2. Validar archivo PDF
    if not pdf_file.content_type or pdf_file.content_type != 'application/pdf':
        raise ValidationError(f"El archivo debe ser un PDF (content-type: {pdf_file.content_type})")

    # Leer contenido del archivo
    pdf_bytes = await pdf_file.read()
    pdf_size = len(pdf_bytes)

    logger.info(f"Archivo recibido: {pdf_file.filename}")
    logger.info(f"Tamano: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

    # Validar tamano maximo (10MB)
    MAX_SIZE = 10 * 1024 * 1024
    if pdf_size > MAX_SIZE:
        raise ValidationError(f"PDF excede tamano maximo (10MB). Tamano: {pdf_size/1024/1024:.2f}MB")

    # Validar magic bytes
    if not pdf_bytes.startswith(b'%PDF'):
        raise ValidationError("El archivo no es un PDF valido")

    # 3. Obtener logo del municipio desde settings
    query_settings = "SELECT logo_url FROM settings LIMIT 1"
    settings_result = execute_query(query_settings, schema_name=schema_name)

    logo_url = settings_result[0]['logo_url'] if settings_result else None
    if not logo_url:
        # Fallback a logo por defecto
        logo_url = DEFAULT_LOGO_URL
        logger.warning(f"No se encontro logo en settings, usando default")

    # 4. Llamar PDFComposer /import/ para procesar PDF
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

    # 5. Subir PDF procesado a R2 bucket 'tosign'
    document_id = str(uuid.uuid4())
    document_id_no_hyphens = document_id.replace('-', '')
    r2_filename = f"{document_id_no_hyphens}.pdf"

    logger.info(f"Subiendo PDF a R2...")
    logger.info(f"Document ID: {document_id}")
    logger.info(f"R2 filename: {r2_filename}")

    try:
        r2_client = get_tenant_r2_client(schema_name=schema_name)
        upload_result = r2_client.upload_tosign(processed_pdf, r2_filename)
        logger.info(f"PDF subido a R2: {upload_result['filename']}")
    except Exception as e:
        logger.error(f"Error subiendo a R2: {str(e)}")
        raise DatabaseError(f"Error al subir PDF a almacenamiento: {str(e)}")

    # 6 y 7. Crear documento y asignar firmante en UNA SOLA transaccion
    # IMPORTANTE: Ambos INSERTs deben estar en la misma transaccion
    # para evitar errores de FK (document_signers referencia document_draft)
    query_insert = """
        INSERT INTO document_draft (
            id, document_type_id, reference, created_by,
            last_modified_at, status, content
        ) VALUES (
            %s, %s, %s, %s,
            CURRENT_TIMESTAMP, 'draft', NULL
        )
        RETURNING id, last_modified_at
    """

    query_signer = """
        INSERT INTO document_signers (
            document_id, user_id, signing_order, is_numerator
        ) VALUES (%s, %s, %s, %s)
    """

    try:
        with execute_transaction(schema_name=schema_name) as (conn, cursor):
            # INSERT documento
            cursor.execute(query_insert, (document_id, doc_type['id'], reference, user_id))
            logger.info(f"Documento creado en BD: {document_id}")

            # INSERT firmante (en la misma transaccion)
            cursor.execute(query_signer, (document_id, user_id, 1, True))
            logger.info(f"Creador asignado como firmante numerador")
            # Auto-commit al salir del context manager
    except Exception as e:
        # Si falla BD, intentar eliminar PDF de R2
        try:
            r2_client.delete_tosign(r2_filename)
            logger.info(f"PDF eliminado de R2 por rollback")
        except Exception:
            pass

        logger.error(f"Error en transaccion BD: {str(e)}")
        raise DatabaseError(f"Error al crear documento: {str(e)}")

    # Generar URL firmada para preview
    pdf_url = r2_client.get_tosign_url(r2_filename)

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
    """
    Reemplaza el PDF de un documento importado existente.

    Solo permitido si:
    - El documento existe
    - El usuario tiene permiso (es creador o tiene acceso)
    - El documento esta en estado draft
    - El tipo de documento es Importado

    Flujo:
    1. Validar documento existe y permisos
    2. Validar estado = draft
    3. Validar tipo = Importado
    4. Procesar nuevo PDF con PDFComposer
    5. Eliminar PDF anterior de R2
    6. Subir nuevo PDF a R2
    7. Actualizar timestamp en BD

    Args:
        document_id: UUID del documento
        pdf_file: Nuevo archivo PDF
        user_id: UUID del usuario
        schema_name: Schema del tenant

    Returns:
        Dict con success, pdf_url, message

    Raises:
        DocumentNotFoundError: Si documento no existe
        ValidationError: Si estado invalido, tipo incorrecto, etc.
        DatabaseError: Si falla operacion
    """
    logger.info(f"Reemplazando PDF de documento {document_id}")
    logger.info(f"  Usuario: {user_id}")
    logger.info(f"  Schema: {schema_name}")

    # 1. Validar documento existe y obtener datos
    query_doc = """
        SELECT
            dd.id, dd.status, dd.reference, dd.created_by,
            dt.id as type_id, dt.name as type_name, dt.acronym as type_acronym, dt.type as source_type
        FROM document_draft dd
        JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE dd.id = %s
    """
    doc_result = execute_query(query_doc, (document_id,), schema_name=schema_name)

    if not doc_result:
        raise DocumentNotFoundError(f"Documento {document_id} no encontrado")

    doc = doc_result[0]

    # 2. Validar estado = draft
    if doc['status'] != 'draft':
        raise DocumentStateError(
            f"Solo se puede reemplazar PDF en documentos en estado draft. Estado actual: {doc['status']}",
            doc['status']
        )

    # 3. Validar tipo = Importado
    if doc['source_type'] != 'Importado':
        raise ValidationError(
            f"Solo se puede reemplazar PDF en documentos de tipo Importado. "
            f"Tipo actual: {doc['source_type']}"
        )

    # Validar permisos (simplificado: solo el creador puede reemplazar)
    if doc['created_by'] != user_id:
        raise ValidationError("Solo el creador del documento puede reemplazar el PDF")

    # Validar archivo PDF
    if not pdf_file.content_type or pdf_file.content_type != 'application/pdf':
        raise ValidationError(f"El archivo debe ser un PDF (content-type: {pdf_file.content_type})")

    # Leer contenido del archivo
    pdf_bytes = await pdf_file.read()
    pdf_size = len(pdf_bytes)

    logger.info(f"Nuevo archivo recibido: {pdf_file.filename}")
    logger.info(f"  Tamano: {pdf_size} bytes ({pdf_size/1024:.2f} KB)")

    # Validar tamano maximo (10MB)
    MAX_SIZE = 10 * 1024 * 1024
    if pdf_size > MAX_SIZE:
        raise ValidationError(f"PDF excede tamano maximo (10MB). Tamano: {pdf_size/1024/1024:.2f}MB")

    # Validar magic bytes
    if not pdf_bytes.startswith(b'%PDF'):
        raise ValidationError("El archivo no es un PDF valido")

    # Obtener logo del municipio
    query_settings = "SELECT logo_url FROM settings LIMIT 1"
    settings_result = execute_query(query_settings, schema_name=schema_name)

    logo_url = settings_result[0]['logo_url'] if settings_result else None
    if not logo_url:
        logo_url = DEFAULT_LOGO_URL

    # 4. Procesar nuevo PDF con PDFComposer
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

    # 5 y 6. Eliminar PDF anterior y subir nuevo
    document_id_no_hyphens = document_id.replace('-', '')
    r2_filename = f"{document_id_no_hyphens}.pdf"

    logger.info(f"Reemplazando PDF en R2...")
    logger.info(f"  R2 filename: {r2_filename}")

    try:
        r2_client = get_tenant_r2_client(schema_name=schema_name)

        # Eliminar anterior (soft-fail si no existe)
        r2_client.delete_tosign(r2_filename)
        logger.info(f"PDF anterior eliminado de R2")

        # Subir nuevo
        upload_result = r2_client.upload_tosign(processed_pdf, r2_filename)
        logger.info(f"Nuevo PDF subido a R2: {upload_result['filename']}")
    except Exception as e:
        logger.error(f"Error en operacion R2: {str(e)}")
        raise DatabaseError(f"Error al actualizar PDF en almacenamiento: {str(e)}")

    # 7. Actualizar timestamp en BD (usa execute_update que hace commit)
    query_update = """
        UPDATE document_draft
        SET last_modified_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """

    try:
        execute_update(query_update, (document_id,), schema_name=schema_name)
        logger.info(f"Timestamp actualizado en BD")
    except Exception as e:
        logger.error(f"Error actualizando BD: {str(e)}")
        raise DatabaseError(f"Error al actualizar documento: {str(e)}")

    # Generar URL firmada para nuevo PDF
    pdf_url = r2_client.get_tosign_url(r2_filename)

    logger.info(f"====================================")
    logger.info(f"PDF reemplazado exitosamente")
    logger.info(f"  Document ID: {document_id}")
    logger.info(f"====================================")

    return {
        "success": True,
        "pdf_url": pdf_url,
        "message": "PDF reemplazado exitosamente"
    }
