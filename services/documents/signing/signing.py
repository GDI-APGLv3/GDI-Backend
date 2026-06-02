"""Servicios para el proceso de firma de documentos."""

from shared.logging import get_logger
import os
import uuid
from typing import Dict, Any, Optional, List
from database import fetch_one, fetch_all, execute, get_conn
from shared.exceptions import (
    DocumentNotFoundError, ValidationError, DocumentStateError,
    DocumentAlreadySignedError, InvalidSignatureOrderError, DocumentAlreadyRejectedError, AuthorizationError,
    ExternalServiceError
)
from shared.pdf_validation import has_end_text_marker
# Imports de validate_document_id y validate_user_id removidos - ya no se usan
from fastapi.concurrency import run_in_threadpool
from services.shared.external_api import generate_final_document_pdf
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
    # removidas porque son redundantes. La query principal ya verifica
    # existencia del documento, y la validación del usuario se hace después.
    # Además, validate_document_id causaba bug 404 por problemas de ContextVar.

    # Obtener información del documento y verificar estado
    document = await fetch_one(
        get_document_for_signing_start_query(),
        document_id,
        schema_name=schema_name,
    )
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
    settings_result = await fetch_one(
        "SELECT logo_url FROM settings LIMIT 1",
        schema_name=schema_name,
    )
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
    if document.get('type_acronym') == 'NOTA':
        from services.notes.validation import validate_nota_recipients_for_signing
        await validate_nota_recipients_for_signing(document_id, schema_name=schema_name)

    # Si es MEMO, validar recipients antes de firmar
    elif document.get('type_acronym') == 'MEMO':
        from services.memos.validation import validate_memo_recipients_for_signing
        await validate_memo_recipients_for_signing(document_id, schema_name=schema_name)

    # Obtener firmantes
    all_signers = await fetch_all(
        get_document_signers_for_pdf_query(),
        document_id,
        schema_name=schema_name,
    )
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

    # Validar que el PDF contiene el marker 'end-text' requerido por Notary
    document_url = pdf_result.get('document_url')
    if document_url:
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=30.0) as _client:
                _pdf_response = await _client.get(document_url)
                _pdf_response.raise_for_status()
                _pdf_bytes = _pdf_response.content
            if not has_end_text_marker(_pdf_bytes):
                logger.error(
                    f"PDF generado no contiene marker 'end-text'. "
                    f"document_id={document_id}, schema={schema_name}"
                )
                # Borrar el PDF de R2 tosign (no se va a usar, ahorra storage)
                try:
                    from services.storage.cloudflare import get_tenant_r2_client
                    _r2_client = await get_tenant_r2_client(schema_name=schema_name)
                    _filename = document_id.replace('-', '') + '.pdf'
                    await run_in_threadpool(_r2_client.delete_tosign, _filename)
                    logger.info(
                        f"PDF invalido eliminado de R2 tosign: {_filename}"
                    )
                except Exception as _del_e:
                    logger.warning(
                        f"No se pudo eliminar PDF invalido de R2 tosign "
                        f"(soft-fail): {_del_e}"
                    )
                raise ValidationError(
                    "El PDF generado no tiene espacio correcto para la firma. "
                    "Por favor agregue saltos de línea para generar otra página."
                )
            logger.info(f"Validación end-text OK para documento {document_id}")
        except ValidationError:
            raise
        except Exception as _e:
            logger.warning(
                f"No se pudo validar end-text del PDF (se continúa igualmente): {_e}"
            )
    else:
        logger.warning(
            f"document_url no disponible en pdf_result, se omite validación end-text. "
            f"document_id={document_id}"
        )

    # Obtener usuarios inactivos para enviar invitaciones
    inactive_signers = await fetch_all(
        get_inactive_signers_query(),
        document_id,
        schema_name=schema_name,
    )

    # Actualizar estado del documento
    await execute(
        update_document_to_sent_to_sign_query(),
        user_id,
        document_id,
        schema_name=schema_name,
    )

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
        Dict con el resultado de la operación

    Raises:
        ValidationError: Si falla descarga, firma, o actualización
        AuthorizationError: Si el usuario no es firmante o ya firmó
    """
    import hashlib

    logger.info(f"Iniciando firma de firmante común para documento {document_id[:8]}... por usuario {user_id[:8]}...")

    from services.documents.signing.r2_lock import (
        acquire_signing_lock_R2,
        release_signing_lock_R2_success,
        release_signing_lock_R2_fail,
    )
    from services.documents.signing.audit_logger import log_signature_event
    from services.r2_client import r2_get_object, R2KeyNotFound

    _lock_acquired = False
    _sign_result = "fail"
    _failure_reason: str | None = None
    _signed_pdf_bytes: bytes | None = None

    try:
        # ============================================================
        # PASO 0: VALIDAR QUE USUARIO SEA FIRMANTE Y NO HAYA FIRMADO
        # ============================================================
        signer_record = await fetch_one(
            """
            SELECT signing_order, signed_at, is_numerator
            FROM document_signers
            WHERE document_id = $1 AND user_id = $2
            """,
            document_id,
            user_id,
            schema_name=schema_name,
        )

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
        # PASO 1: ADQUIRIR LOCK R2 (mueve PDF a tosign/inprocess/)
        # ============================================================
        logger.info("Adquiriendo lock R2 para firma...")
        _lock_acquired = await acquire_signing_lock_R2(
            schema_name=schema_name,
            doc_id=document_id,
        )
        if not _lock_acquired:
            _failure_reason = "document_already_signing"
            raise ValidationError(
                f"El documento {document_id} ya está siendo firmado por otro proceso (lock R2 activo)"
            )
        logger.info("Lock R2 adquirido correctamente")

        # ============================================================
        # PASO 2: DESCARGAR PDF DESDE R2 tosign/inprocess/
        # ============================================================
        logger.info("Descargando PDF desde R2 tosign/inprocess/...")

        inprocess_key = f"inprocess/{document_id.replace('-', '')}.pdf"
        try:
            pdf_bytes = await r2_get_object(
                schema_name=schema_name,
                key=inprocess_key,
                bucket="tosign",
            )
        except R2KeyNotFound:
            raise ValidationError(f"No se encontró el PDF en R2 inprocess: {inprocess_key}")

        logger.info(f"PDF descargado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

        # ============================================================
        # PASO 2: OBTENER DATOS DEL FIRMANTE
        # ============================================================
        logger.info("Paso 2/4: Obteniendo datos del firmante...")

        signer_data = await get_signer_data(user_id, schema_name=schema_name)
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
        logger.info("Paso 3/5: Firmando con Notary API...")

        from services.shared.notary_api import call_notary_sign_pdf

        try:
            # Firmante común: official_number="" y city=""
            _signed_pdf_bytes = await call_notary_sign_pdf(
                pdf_bytes=pdf_bytes,
                signer_name=signer_name,
                signer_seal=signer_seal,
                signer_department=signer_department,
                signer_municipality=signer_municipality,
                official_number="",  # Vacío para firmante común
                city="",             # Vacío para firmante común
                tenant_id=schema_name,  # Para firma PAdES
                schema_name=schema_name  # Para resolver certificado de R2
            )
        except Exception as _notary_err:
            # Notary falló: liberar lock (rollback inprocess/ → tosign/)
            _failure_reason = f"notary_error: {str(_notary_err)[:200]}"
            logger.error(f"Notary falló, liberando lock R2: {_notary_err}")
            await release_signing_lock_R2_fail(
                schema_name=schema_name,
                doc_id=document_id,
            )
            _lock_acquired = False  # ya liberado
            raise

        logger.info(f"PDF firmado: {len(_signed_pdf_bytes)} bytes ({len(_signed_pdf_bytes)/1024:.2f} KB)")

        # ============================================================
        # PASO 4: LIBERAR LOCK R2 - ÉXITO (sube PDF firmado a tosign/)
        # ============================================================
        logger.info("Paso 4/5: Liberando lock R2 (subiendo PDF firmado a tosign/)...")

        await release_signing_lock_R2_success(
            schema_name=schema_name,
            doc_id=document_id,
            signed_pdf=_signed_pdf_bytes,
            is_numerator=False,
            number=None,
        )
        _lock_acquired = False  # liberado correctamente
        logger.info("Lock R2 liberado y PDF firmado guardado en tosign/")

        # ============================================================
        # PASO 5: ACTUALIZAR SOLO ESTADO DEL FIRMANTE
        # ============================================================
        logger.info("Paso 5/5: Actualizando estado del firmante...")

        signature_id = str(uuid.uuid4())

        status_str = await execute(
            update_signer_status_to_signed_query(),
            document_id,
            user_id,
            schema_name=schema_name,
        )

        # asyncpg execute() returns status string like "UPDATE 1"
        rows_affected = int(status_str.split()[-1]) if status_str else 0
        if rows_affected == 0:
            raise ValidationError("No se pudo actualizar el estado del firmante")

        # Obtener estado del documento (NO lo cambia, solo lo lee)
        doc_result = await fetch_one(
            get_document_draft_status_query(),
            document_id,
            schema_name=schema_name,
        )
        final_status = doc_result['status'] if doc_result else 'unknown'

        logger.info("Firmante actualizado a 'signed'")
        logger.info(f"Documento permanece en estado: {final_status}")
        logger.info("Proceso de firma común completado exitosamente")

        _sign_result = "ok"

        # Audit log (Fase 1 - firma electrónica)
        await log_signature_event(
            schema_name=schema_name,
            document_id=document_id,
            user_id=user_id,
            signature_method="electronic",
            result="ok",
            r2_object_key=inprocess_key,
        )

        return {
            "success": True,
            "message": "Documento firmado exitosamente",
            "signature_id": signature_id,
            "document_status": final_status,  # Siempre 'sent_to_sign'
            "signing_result": {
                "success": True,
                "api_mode": "direct_notary",
                "signed_pdf_size": len(_signed_pdf_bytes)
            }
        }

    except (ValidationError, AuthorizationError, DocumentAlreadySignedError) as e:
        # Errores de negocio conocidos: liberar lock si está activo
        if _lock_acquired:
            try:
                await release_signing_lock_R2_fail(
                    schema_name=schema_name,
                    doc_id=document_id,
                )
                _lock_acquired = False  # ya liberado
            except Exception as _rel_err:
                logger.warning(f"No se pudo liberar lock R2 en except: {_rel_err}")
        # Asegurar que _failure_reason quede poblado para el audit
        _failure_reason = _failure_reason or str(e)[:300]
        # Audit log de todos los fallos, incluyendo document_already_signing
        if _failure_reason:
            await log_signature_event(
                schema_name=schema_name,
                document_id=document_id,
                user_id=user_id,
                signature_method="electronic",
                result="fail",
                failure_reason=_failure_reason,
            )
        raise
    except Exception as e:
        _failure_reason = _failure_reason or str(e)[:300]
        # Liberar lock si quedó activo
        if _lock_acquired:
            try:
                await release_signing_lock_R2_fail(
                    schema_name=schema_name,
                    doc_id=document_id,
                )
                _lock_acquired = False  # ya liberado
            except Exception as _rel_err:
                logger.warning(f"No se pudo liberar lock R2 en except genérico: {_rel_err}")
        # Audit log del fallo
        await log_signature_event(
            schema_name=schema_name,
            document_id=document_id,
            user_id=user_id,
            signature_method="electronic",
            result="fail",
            failure_reason=_failure_reason,
        )
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
        creator_result = await fetch_one(
            get_user_full_name_query(),
            creator_user_id,
            schema_name=schema_name,
        )
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

                logger.info(f"Enviando invitacion a user_id={signer['user_id']}")

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
                        logger.info(f"Invitacion enviada exitosamente a user_id={signer['user_id']}")
                        invitations_sent += 1
                    else:
                        logger.error(f"Error HTTP {response.status_code} al enviar invitacion a user_id={signer['user_id']}")
                        logger.error(f"Respuesta: {response.text}")

            except Exception as e:
                logger.error(f"Error enviando invitacion a user_id={signer['user_id']}: {str(e)}")
                # Continuar con el siguiente usuario sin detener el proceso
                continue

    except Exception as e:
        logger.error(f"Error general en envío de invitaciones: {str(e)}")
        # No propagar el error - las invitaciones son "best effort"

    logger.info(f"Total invitaciones enviadas exitosamente: {invitations_sent}/{len(inactive_signers)}")
    return invitations_sent
