"""Servicios para el proceso de firma de documentos."""

from shared.logging import get_logger
import os
import uuid
from typing import Dict, Any, Optional, List
from database import get_db_connection, execute_transaction, execute_single_update, execute_query
from shared.exceptions import (
    DocumentNotFoundError, ValidationError, DocumentStateError,
    DocumentAlreadySignedError, InvalidSignatureOrderError, DocumentAlreadyRejectedError, AuthorizationError,
    ExternalServiceError
)
# Imports de validate_document_id y validate_user_id removidos - ya no se usan
from fastapi.concurrency import run_in_threadpool
from services.shared.external_api import generate_final_document_pdf, call_legal_orchestrator_sign_document
from services.shared.signer_data import get_signer_data
from services.shared.resume_trigger import enqueue_resume_fire_and_forget
from services.documents.core.queries import (
    get_document_for_signing_start_query,
    get_document_signers_for_pdf_query,
    get_inactive_signers_query,
    update_document_to_sent_to_sign_query,
    update_document_signers_order_query,
    get_document_draft_status_query,
    get_user_full_name_query,
    update_signer_status_to_signed_query
)
from config.constants import (
    EDITABLE_DOCUMENT_STATES,
    START_SIGNING_SUCCESS_MESSAGE,
    START_SIGNING_ONLY_CREATOR_ERROR,
    START_SIGNING_PDF_GENERATION_ERROR
)

logger = get_logger(__name__)

async def start_document_signing_process(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Inicia el proceso de firma para un documento específico.

    Args:
        document_id: UUID del documento
        user_id: UUID del usuario que inicia el proceso
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con los datos del proceso iniciado

    Raises:
        DocumentNotFoundError: Si el documento no existe
        DocumentStateError: Si el documento no puede firmarse
        AuthorizationError: Si el usuario no es el creador
        ValidationError: Si faltan datos requeridos
        ExternalServiceError: Si falla la generación del PDF
    """
    # Nota: Las validaciones de validate_document_id y validate_user_id fueron
    # removidas porque son redundantes. La query principal (línea 67) ya verifica
    # existencia del documento, y la validación del usuario se hace en línea 87.
    # Además, validate_document_id causaba bug 404 por problemas de ContextVar.

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Obtener información del documento y verificar estado
            cursor.execute(get_document_for_signing_start_query(), (document_id,))
            document = cursor.fetchone()
            if not document:
                raise DocumentNotFoundError(document_id)

            logger.info(f"Iniciando proceso de firma para documento {document_id}")

            if not document.get('type_acronym') or not document.get('type_name'):
                logger.warning(
                    f"Documento {document_id} sin datos de tipo de documento. "
                    f"document_type_id: {document.get('document_type_id')}"
                )

            if document['status'] not in EDITABLE_DOCUMENT_STATES:
                raise DocumentStateError(
                    f"Documento en estado '{document['status']}' no puede iniciarse para firma",
                    current_state=document['status'],
                    required_state=" o ".join(EDITABLE_DOCUMENT_STATES)
                )

            if document['created_by'] != user_id:
                raise AuthorizationError(START_SIGNING_ONLY_CREATOR_ERROR)

            # Obtener logo del municipio desde settings
            cursor.execute("SELECT logo_url FROM settings LIMIT 1")
            settings_result = cursor.fetchone()
            logo_url = settings_result['logo_url'] if settings_result and settings_result.get('logo_url') else None

            document_data = {
                "document_id": document_id,
                "reference": document['reference'],
                "content": document['content'],
                "type_name": document['type_name'],
                "type_acronym": document['type_acronym'],
                "municipality_logo_url": logo_url
            }

            # Si es NOTA, validar recipients antes de firmar
            # (El header de recipients lo genera PDFComposer con /note/)
            if document.get('type_acronym') == 'NOTA':
                from services.notes.validation import validate_nota_recipients_for_signing
                validate_nota_recipients_for_signing(document_id, schema_name=schema_name)

            # Obtener firmantes
            cursor.execute(get_document_signers_for_pdf_query(), (document_id,))
            all_signers = cursor.fetchall()
            signers_for_pdf = [
                {
                    "user_id": signer['user_id'],
                    "user_name": signer['user_name'],
                    "signing_order": signer['signing_order'],
                    "is_numerator": signer['is_numerator']
                }
                for signer in all_signers
            ]

            # Generar PDF
            pdf_result = await generate_final_document_pdf(document_id, document_data, signers_for_pdf, schema_name=schema_name)

            logger.info(f"PDF generado para documento {document_id}")

            # Validar que el PDF se generó correctamente
            if not pdf_result or not pdf_result.get('document_generate_id'):
                logger.error(
                    f"Error en generación de PDF. pdf_result: {pdf_result}, "
                    f"document_generate_id: {pdf_result.get('document_generate_id') if pdf_result else 'No pdf_result'}"
                )
                raise ExternalServiceError(START_SIGNING_PDF_GENERATION_ERROR)

            # Obtener usuarios inactivos para enviar invitaciones
            cursor.execute(get_inactive_signers_query(), (document_id,))
            inactive_signers = cursor.fetchall()

            # Actualizar estado del documento
            cursor.execute(
                update_document_to_sent_to_sign_query(),
                (user_id, document_id)
            )
            conn.commit()

            # Enviar invitaciones a usuarios inactivos
            invitations_sent = 0
            if inactive_signers:
                invitations_sent = await _send_user_invitations(inactive_signers, document_id, user_id, schema_name=schema_name)

            # Fire-and-forget: encola generación de resumen async
            enqueue_resume_fire_and_forget(document_id, schema_name)

            logger.info(f"Proceso de firma iniciado exitosamente para documento {document_id}")

            return {
                "success": True,
                "message": START_SIGNING_SUCCESS_MESSAGE,
                "document_generate_id": pdf_result.get('document_generate_id'),
                "document_url": pdf_result.get('document_url'),
                "api_mode": pdf_result.get('api_mode', 'unknown'),
                "invitations_sent": invitations_sent
            }

def get_document_signature_details(document_id: str, user_id: str) -> Dict[str, Any]:
    """
    Obtiene los detalles necesarios para mostrar la pantalla de firma.

    Utiliza arquitectura modular delegando la construcción de respuesta
    al servicio especializado signature_details_builder.

    Args:
        document_id: UUID del documento (ya validado por el endpoint)
        user_id: UUID del usuario (ya validado por el endpoint)

    Returns:
        Dict con todos los datos para la pantalla de firma
    """
    from services.documents.signing.details_builder import build_signature_details_response
    return build_signature_details_response(document_id, user_id)

async def sign_document(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Ejecuta la firma de un documento por un firmante común (NO numerador).

    Phase 4: Integración directa con Notary API sin Legal Orchestrator.

    Flujo:
    1. Verifica que el usuario sea firmante del documento y no haya firmado ya
    2. Descarga PDF desde Cloudflare R2 bucket 'tosign'
    3. Obtiene datos del firmante desde BD
    4. Firma PDF con Notary API (official_number="" y city="")
    5. Sobrescribe PDF en R2 bucket 'tosign' (para siguiente firmante)
    6. Actualiza solo el estado del firmante (documento sigue 'sent_to_sign')

    Args:
        document_id: UUID del documento a firmar (ya validado por el endpoint)
        user_id: UUID del usuario que firma (ya validado por el endpoint)
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con el resultado de la operación:
        - success: bool
        - message: str
        - signature_id: str
        - document_status: str (siempre 'sent_to_sign')

    Raises:
        ValidationError: Si falla descarga, firma, o actualización
        AuthorizationError: Si el usuario no es firmante o ya firmó
    """
    import httpx

    logger.info(f"Iniciando firma de firmante común para documento {document_id[:8]}... por usuario {user_id[:8]}...")

    try:
        # ============================================================
        # PASO 0: VALIDAR QUE USUARIO SEA FIRMANTE Y NO HAYA FIRMADO
        # ============================================================
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Verificar que el usuario existe en document_signers para este documento
                cursor.execute(
                    """
                    SELECT signing_order, signed_at, is_numerator
                    FROM document_signers
                    WHERE document_id = %s AND user_id = %s
                    """,
                    (document_id, user_id)
                )
                signer_record = cursor.fetchone()

                if not signer_record:
                    raise AuthorizationError(
                        f"Usuario {user_id} no es firmante del documento {document_id}"
                    )

                # Verificar que no haya firmado ya
                if signer_record['signed_at'] is not None:
                    raise DocumentAlreadySignedError(
                        f"Usuario {user_id} ya firmó este documento"
                    )

                logger.info(f"Validación OK: usuario es firmante y no ha firmado aún")
                logger.info(f"  signing_order: {signer_record['signing_order']}, is_numerator: {signer_record['is_numerator']}")

        # ============================================================
        # PASO 1: DESCARGAR PDF DESDE R2 TOSIGN
        # ============================================================
        logger.info("Descargando PDF desde R2 bucket tosign...")

        from services.storage.cloudflare import get_tenant_r2_client
        r2_client = get_tenant_r2_client(schema_name=schema_name)

        # Filename: document_id sin guiones
        filename = document_id.replace('-', '') + '.pdf'

        # Obtener URL firmada temporal
        pdf_url = await run_in_threadpool(r2_client.get_tosign_url, filename)
        if not pdf_url:
            raise ValidationError(f"No se pudo obtener URL del PDF desde R2 tosign: {filename}")

        logger.info(f"Descargando: {filename}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            pdf_response = await client.get(pdf_url)
            pdf_response.raise_for_status()
            pdf_bytes = pdf_response.content

        logger.info(f"PDF descargado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

        # ============================================================
        # PASO 2: OBTENER DATOS DEL FIRMANTE
        # ============================================================
        logger.info("Paso 2/4: Obteniendo datos del firmante...")

        # Obtener datos del firmante usando función compartida
        signer_data = get_signer_data(user_id, schema_name=schema_name)
        signer_name = signer_data['full_name']
        signer_seal = signer_data['seal']
        signer_department = signer_data['department_name']
        signer_municipality = signer_data['municipality_name']

        logger.info(f"Firmante: {signer_name}")
        logger.info(f"Sello: {signer_seal}")
        logger.info(f"Departamento: {signer_department}")

        # ============================================================
        # PASO 3: FIRMAR CON NOTARY API
        # ============================================================
        logger.info("Paso 3/4: Firmando con Notary API...")

        from services.shared.notary_api import call_notary_sign_pdf

        # Firmante común: official_number="" y city=""
        signed_pdf_bytes = await call_notary_sign_pdf(
            pdf_bytes=pdf_bytes,
            signer_name=signer_name,
            signer_seal=signer_seal,
            signer_department=signer_department,
            signer_municipality=signer_municipality,
            official_number="",  # Vacío para firmante común
            city="",             # Vacío para firmante común
            tenant_id=schema_name  # Para firma PAdES
        )

        logger.info(f"PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

        # ============================================================
        # PASO 4: SOBRESCRIBIR PDF EN R2 TOSIGN
        # ============================================================
        logger.info("Paso 4/4: Sobrescribiendo PDF en R2 tosign...")

        await run_in_threadpool(r2_client.upload_tosign, signed_pdf_bytes, filename)
        logger.info(f"PDF sobrescrito en R2 tosign: {filename}")

        # ============================================================
        # PASO 5: ACTUALIZAR SOLO ESTADO DEL FIRMANTE
        # ============================================================
        logger.info("Paso 5/5: Actualizando estado del firmante...")

        signature_id = str(uuid.uuid4())

        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Actualizar estado del firmante
                cursor.execute(
                    update_signer_status_to_signed_query(),
                    (document_id, user_id)
                )

                if cursor.rowcount == 0:
                    raise ValidationError("No se pudo actualizar el estado del firmante")

                # Confirmar cambios
                conn.commit()

                # Obtener estado del documento (NO lo cambia, solo lo lee)
                cursor.execute(get_document_draft_status_query(), (document_id,))
                doc_result = cursor.fetchone()
                final_status = doc_result['status'] if doc_result else 'unknown'

        logger.info("Firmante actualizado a 'signed'")
        logger.info(f"Documento permanece en estado: {final_status}")
        logger.info("Proceso de firma común completado exitosamente")

        return {
            "success": True,
            "message": "Documento firmado exitosamente",
            "signature_id": signature_id,
            "document_status": final_status,  # Siempre 'sent_to_sign'
            "signing_result": {
                "success": True,
                "api_mode": "direct_notary",
                "signed_pdf_size": len(signed_pdf_bytes)
            }
        }

    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise ValidationError(f"Error al firmar documento: {str(e)}")

async def _send_user_invitations(inactive_signers: List[Dict], document_id: str, creator_user_id: str, *, schema_name: str) -> int:
    """
    Envía invitaciones por email a usuarios inactivos (estado=2) usando API externa.

    Args:
        inactive_signers: Lista de usuarios inactivos con user_id, email, full_name
        document_id: ID del documento para contexto
        creator_user_id: ID del usuario que inició el proceso de firma
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        int: Número de invitaciones enviadas exitosamente
    """
    import os
    import httpx

    invitations_sent = 0

    # Obtener configuración del servicio de emails
    email_service_url = os.getenv('EMAIL_SERVICE_URL')
    email_api_key = os.getenv('EMAIL_API_KEY')

    # Si no está configurado, advertir pero NO enviar emails
    if not email_service_url or not email_api_key:
        logger.warning("EMAIL_SERVICE_URL o EMAIL_API_KEY no configurado. No se enviarán invitaciones por email.")
        return 0

    # Obtener el nombre del usuario que inició el proceso de firma
    creator_full_name = "Usuario creador"  # Valor por defecto
    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(get_user_full_name_query(), (creator_user_id,))
                creator_result = cursor.fetchone()
                if creator_result:
                    creator_full_name = creator_result['full_name']
                    logger.debug(f"Usuario creador encontrado: {creator_full_name}")
                else:
                    logger.warning(f"No se encontró usuario creador con ID: {creator_user_id}")
    except Exception as e:
        logger.error(f"Error obteniendo nombre del creador: {str(e)}")

    logger.info(f"Iniciando envío de invitaciones. Servicio: {email_service_url}")
    logger.info(f"Usuarios inactivos a invitar: {len(inactive_signers)}")

    try:
        for signer in inactive_signers:
            try:
                # Preparar el contenido del email con el nuevo texto de GDI
                subject = f"{creator_full_name} te invitó a conocer GDI 🧪"

                body = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>{creator_full_name} te invitó a conocer GDI 🧪</title>
                </head>
                <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8f9fa; color: #333333;">
                    <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                        <!-- Header con el color azul de GDI #16158C -->
                        <div style="background-color: #16158C; padding: 30px 20px; text-align: center;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 28px; font-weight: 600;">
                                ¡Hola! 👋
                            </h1>
                            <p style="margin: 10px 0 0 0; color: #ffffff; font-size: 16px; opacity: 0.9;">
                                Gestión Documental Inteligente
                            </p>
                        </div>

                        <!-- Contenido principal -->
                        <div style="padding: 40px 30px;">

                            <p style="margin: 0 0 20px 0; color: #34495e; font-size: 18px; line-height: 1.6;">
                                <strong>{creator_full_name}</strong> te invitó a conocer <strong>GDI</strong>,
                                la plataforma de Gestión Documental Inteligente para las municipalidades del futuro.
                            </p>

                            <p style="margin: 0 0 20px 0; color: #34495e; font-size: 16px; line-height: 1.6;">
                                Ingresá para descubrir cómo se crean expedientes, documentos y flujos de trabajo inteligentes.
                            </p>

                            <div style="background-color: #fff3cd; border: 1px solid #ffeaa7; border-radius: 6px; padding: 15px; margin: 20px 0;">
                                <p style="margin: 0; color: #856404; font-size: 14px;">
                                    <strong>🧪 Estás accediendo a una versión de testeo —usá datos ficticios—</strong>
                                </p>
                            </div>

                            <!-- Botón de acción principal -->
                            <div style="text-align: center; margin: 35px 0;">
                                <a href="https://nuevogdi.framer.website/"
                                   style="display: inline-block; background-color: #16158C;
                                          color: white; text-decoration: none; padding: 16px 32px;
                                          border-radius: 8px; font-weight: 600; font-size: 16px;
                                          box-shadow: 0 4px 15px rgba(22, 21, 140, 0.3);">
                                    🚀 EXPLORAR GDI
                                </a>
                            </div>

                            <!-- Descripción de GDI -->
                            <div style="background-color: #f8f9fa; border-radius: 8px; padding: 25px; margin: 30px 0; border-left: 4px solid #16158C;">
                                <p style="margin: 0; color: #34495e; font-size: 15px; line-height: 1.6;">
                                    <strong>GDI</strong> es una plataforma <strong>open source con IA nativa</strong> que transforma la gestión pública,
                                    haciendo los procesos más ágiles, colaborativos y eficientes.
                                </p>
                            </div>

                            <!-- Redes sociales en texto simple -->
                            <div style="margin: 30px 0; text-align: center;">
                                <p style="margin: 0 0 15px 0; color: #34495e; font-size: 16px; font-weight: 500;">
                                    Seguinos en:
                                </p>
                                <p style="margin: 0 0 20px 0; color: #16158C; font-size: 15px; line-height: 1.8;">
                                    <a href="https://www.linkedin.com/company/gdilatam/posts/?feedView=all" style="color: #16158C; text-decoration: none; margin-right: 10px;">LinkedIn</a> ·
                                    <a href="https://www.youtube.com/@Gesti%C3%B3nDocumentalInteligente" style="color: #16158C; text-decoration: none; margin: 0 10px;">YouTube</a> ·
                                    <a href="https://www.instagram.com/gdi.latam/" style="color: #16158C; text-decoration: none; margin: 0 10px;">Instagram</a> ·
                                    <a href="https://github.com/GestionDocumentalInteligente" style="color: #16158C; text-decoration: none; margin-left: 10px;">GitHub</a>
                                </p>
                                <p style="margin: 0; color: #16158C; font-size: 15px;">
                                    🌐 <a href="https://gdilatam.framer.website/" style="color: #16158C; text-decoration: none;">gdilatam.com</a>
                                </p>
                            </div>


                        </div>

                        <!-- Footer -->
                        <div style="background-color: #16158C; padding: 20px 30px; text-align: center;">
                            <p style="margin: 0; color: #ffffff; font-size: 14px;">
                                © 2025 GDI – Gestión Documental Inteligente
                            </p>
                        </div>
                    </div>
                </body>
                </html>
                """

                # Preparar payload para la API
                email_payload = {
                    "to": signer['email'],
                    "subject": subject,
                    "body": body.strip(),
                    "from_name": "GDI Latam"
                }

                logger.info(f"Enviando a {signer['email']} ({signer['full_name']})")

                # Hacer llamada HTTP a la API de emails
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{email_service_url}/send-email",
                        json=email_payload,
                        headers={
                            "Content-Type": "application/json",
                            "X-API-Key": email_api_key
                        }
                    )

                    if response.status_code == 200:
                        logger.info(f"Email enviado exitosamente a {signer['email']}")
                        invitations_sent += 1
                    else:
                        logger.error(f"Error HTTP {response.status_code} al enviar a {signer['email']}")
                        logger.error(f"Respuesta: {response.text}")

            except Exception as e:
                logger.error(f"Error enviando a {signer['email']}: {str(e)}")
                # Continuar con el siguiente usuario sin detener el proceso
                continue

    except Exception as e:
        logger.error(f"Error general en envío de invitaciones: {str(e)}")
        # No propagar el error - las invitaciones son "best effort"
        # El proceso de firma debe continuar aunque fallen las invitaciones

    logger.info(f"Total invitaciones enviadas exitosamente: {invitations_sent}/{len(inactive_signers)}")
    return invitations_sent
