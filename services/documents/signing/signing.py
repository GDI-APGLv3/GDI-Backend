
from shared.logging import get_logger
import uuid
from typing import Dict, Any, Optional
from database import fetch_one, fetch_all, execute
from shared.exceptions import (
    DocumentNotFoundError, ValidationError, DocumentStateError,
    DocumentAlreadySignedError, AuthorizationError,
    ExternalServiceError
)
from shared.pdf_validation import has_end_text_marker
from fastapi.concurrency import run_in_threadpool
from services.shared.external_api import generate_final_document_pdf
from services.shared.signer_data import get_signer_data
from services.shared.resume_trigger import enqueue_resume_fire_and_forget
from services.documents.core.queries import (
    get_document_for_signing_start_query,
    get_document_signers_for_pdf_query,
    update_document_to_sent_to_sign_query,
    get_document_draft_status_query,
    update_signer_status_to_signed_query
)
from config.constants import (
    EDITABLE_DOCUMENT_STATES,
    START_SIGNING_SUCCESS_MESSAGE,
    START_SIGNING_ALREADY_DONE_MESSAGE,
    START_SIGNING_EN_CURSO_ERROR,
    START_SIGNING_ONLY_CREATOR_ERROR,
    START_SIGNING_PDF_GENERATION_ERROR
)

logger = get_logger(__name__)

async def preparar_documento_para_firma(
    document_id: str, user_id: str, *, schema_name: str
) -> Dict[str, Any]:

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
        if document['status'] == 'sent_to_sign':
            _proceso_terminado = document.get('sent_to_sign_at') is not None
            _mismo_actor = str(document.get('sent_by') or '') == str(user_id)

            if _proceso_terminado and _mismo_actor:
                logger.info(
                    f"start_document_signing_process: {document_id} ya estaba enviado a "
                    f"firma por el mismo actor; se responde idempotente sin regenerar el PDF"
                )
                return {
                    "estado": "ya_terminado",
                    "respuesta": {
                        "success": True,
                        "message": START_SIGNING_ALREADY_DONE_MESSAGE,
                        "document_generate_id": document_id,
                        "document_url": None,
                        "api_mode": "idempotent",
                    },
                }

            if not _proceso_terminado:
                logger.info(
                    f"start_document_signing_process: {document_id} tiene un proceso de "
                    f"firma EN CURSO (sent_to_sign sin sent_to_sign_at); se rechaza el "
                    f"reintento con mensaje explicito"
                )
                raise DocumentStateError(
                    START_SIGNING_EN_CURSO_ERROR,
                    current_state=document['status'],
                    required_state=" o ".join(EDITABLE_DOCUMENT_STATES),
                )

        raise DocumentStateError(
            f"Documento en estado '{document['status']}' no puede iniciarse para firma",
            current_state=document['status'],
            required_state=" o ".join(EDITABLE_DOCUMENT_STATES)
        )

    if document['created_by'] != user_id and document.get('created_by_citizen') != user_id:
        raise AuthorizationError(START_SIGNING_ONLY_CREATOR_ERROR)

    _original_status = document['status']
    _cas_row = await fetch_one(
        "UPDATE document_draft SET status = 'sent_to_sign' WHERE id = $1 AND status = $2 RETURNING id",
        document_id, _original_status,
        schema_name=schema_name,
    )
    if not _cas_row:
        raise DocumentStateError(
            "El documento cambió de estado mientras se iniciaba el proceso de firma. Reintente.",
            current_state="unknown",
            required_state=" o ".join(EDITABLE_DOCUMENT_STATES),
        )

    return {
        "estado": "listo",
        "document": document,
        "original_status": _original_status,
    }

async def generar_pdf_y_finalizar(
    document_id: str,
    user_id: str,
    *,
    schema_name: str,
    document: Optional[Dict[str, Any]] = None,
    original_status: str,
) -> Dict[str, Any]:
    _original_status = original_status

    if document is None:
        document = await fetch_one(
            get_document_for_signing_start_query(),
            document_id,
            schema_name=schema_name,
        )
        if not document:
            raise DocumentNotFoundError(document_id)

    try:
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
            "base_type": (document.get('source_type') or '').upper(),
            "municipality_logo_url": logo_url
        }

        _doc_base_type = (document.get('source_type') or '').upper()
        if _doc_base_type == 'NOTA':
            from services.notes.validation import validate_nota_recipients_for_signing
            await validate_nota_recipients_for_signing(document_id, schema_name=schema_name)

        elif _doc_base_type == 'MEMO':
            from services.memos.validation import validate_memo_recipients_for_signing
            await validate_memo_recipients_for_signing(document_id, schema_name=schema_name)

        if document.get('has_fields'):
            logger.info(f"Documento {document_id} tiene formulario controlado, convirtiendo content a HTML para PDF...")
            from database import fetch_one as _fetch_one
            fd_row = await _fetch_one(
                "SELECT field_definitions FROM document_type_fields WHERE document_type_id = $1",
                document['document_type_id'],
                schema_name=schema_name,
            )
            field_defs = fd_row['field_definitions'] if fd_row else []
            raw_data = document['content'] if isinstance(document['content'], dict) else {}
            from services.documents.ffcc_renderer import ffcc_to_html
            ffcc_html = ffcc_to_html(field_defs, raw_data)
            document_data['content'] = ffcc_html
            logger.info(f"Formulario controlado HTML generado: {len(ffcc_html)} chars")

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

        from services.documents.lifecycle.embedded_files import fetch_embedded_files_for_signing
        embedded_files = await fetch_embedded_files_for_signing(document_id, schema_name=schema_name)
        if embedded_files:
            logger.info(f"Documento {document_id} tiene {len(embedded_files)} adjunto(s) embebido(s) para incluir en el PDF")

        pdf_result = await generate_final_document_pdf(
            document_id, document_data, signers_for_pdf, schema_name=schema_name, embedded_files=embedded_files
        )

        logger.info(f"PDF generado para documento {document_id}")

        if not pdf_result or not pdf_result.get('document_generate_id'):
            logger.error(
                f"Error en generación de PDF. pdf_result: {pdf_result}, "
                f"document_generate_id: {pdf_result.get('document_generate_id') if pdf_result else 'No pdf_result'}"
            )
            raise ExternalServiceError(START_SIGNING_PDF_GENERATION_ERROR)

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
    except Exception:
        logger.error(
            f"start_document_signing_process: fallo tras el CAS a sent_to_sign, "
            f"revirtiendo documento {document_id} a estado '{_original_status}'"
        )
        try:
            await execute(
                "UPDATE document_draft SET status = $1 WHERE id = $2 AND status = 'sent_to_sign'",
                _original_status, document_id,
                schema_name=schema_name,
            )
        except Exception as _rollback_err:
            logger.critical(
                f"start_document_signing_process: NO SE PUDO REVERTIR el estado de "
                f"documento {document_id} tras fallo — queda trabado en 'sent_to_sign' "
                f"sin firmantes notificados. Requiere revision manual: {_rollback_err}"
            )
        raise

    await execute(
        update_document_to_sent_to_sign_query(),
        user_id,
        document_id,
        schema_name=schema_name,
    )

    enqueue_resume_fire_and_forget(document_id, schema_name)

    logger.info(f"Proceso de firma iniciado exitosamente para documento {document_id}")

    return {
        "success": True,
        "message": START_SIGNING_SUCCESS_MESSAGE,
        "document_generate_id": pdf_result.get('document_generate_id'),
        "document_url": pdf_result.get('document_url'),
        "api_mode": pdf_result.get('api_mode', 'unknown'),
    }

async def start_document_signing_process(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    preparado = await preparar_documento_para_firma(
        document_id, user_id, schema_name=schema_name
    )
    if preparado["estado"] == "ya_terminado":
        return preparado["respuesta"]

    return await generar_pdf_y_finalizar(
        document_id, user_id,
        schema_name=schema_name,
        document=preparado["document"],
        original_status=preparado["original_status"],
    )

async def sign_document(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:

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

        if signer_record['signed_at'] is not None:
            raise DocumentAlreadySignedError(
                f"Usuario {user_id} ya firmó este documento"
            )

        logger.info(f"Validación OK: usuario es firmante y no ha firmado aún")
        logger.info(f"  signing_order: {signer_record['signing_order']}, is_numerator: {signer_record['is_numerator']}")

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

        logger.info("Paso 2/4: Obteniendo datos del firmante...")

        signer_data = await get_signer_data(user_id, schema_name=schema_name)
        signer_name = signer_data['full_name']
        signer_seal = signer_data['seal']
        signer_department = signer_data['department_name']
        signer_municipality = signer_data['municipality_name']

        logger.info(f"Firmante: {signer_name}")
        logger.info(f"Sello: {signer_seal}")
        logger.info(f"Departamento: {signer_department}")

        logger.info("Paso 3/5: Firmando con Notary API...")

        from services.shared.notary_api import call_notary_sign_pdf

        try:
            _signed_pdf_bytes = await call_notary_sign_pdf(
                pdf_bytes=pdf_bytes,
                signer_name=signer_name,
                signer_seal=signer_seal,
                signer_department=signer_department,
                signer_municipality=signer_municipality,
                official_number="",
                city="",
                tenant_id=schema_name,
                schema_name=schema_name,
                defer_timestamp=True,
            )
        except Exception as _notary_err:
            _failure_reason = f"notary_error: {str(_notary_err)[:200]}"
            logger.error(f"Notary falló, liberando lock R2: {_notary_err}")
            await release_signing_lock_R2_fail(
                schema_name=schema_name,
                doc_id=document_id,
            )
            _lock_acquired = False
            raise

        logger.info(f"PDF firmado: {len(_signed_pdf_bytes)} bytes ({len(_signed_pdf_bytes)/1024:.2f} KB)")

        logger.info("Paso 4/5: Liberando lock R2 (subiendo PDF firmado a tosign/)...")

        await release_signing_lock_R2_success(
            schema_name=schema_name,
            doc_id=document_id,
            signed_pdf=_signed_pdf_bytes,
            is_numerator=False,
            number=None,
        )
        _lock_acquired = False
        logger.info("Lock R2 liberado y PDF firmado guardado en tosign/")

        logger.info("Paso 5/5: Actualizando estado del firmante...")

        signature_id = str(uuid.uuid4())

        status_str = await execute(
            update_signer_status_to_signed_query(),
            document_id,
            user_id,
            schema_name=schema_name,
        )

        rows_affected = int(status_str.split()[-1]) if status_str else 0
        if rows_affected == 0:
            raise ValidationError("No se pudo actualizar el estado del firmante")

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
            "document_status": final_status,
            "signing_result": {
                "success": True,
                "api_mode": "direct_notary",
                "signed_pdf_size": len(_signed_pdf_bytes)
            }
        }

    except (ValidationError, AuthorizationError, DocumentAlreadySignedError) as e:
        if _lock_acquired:
            try:
                await release_signing_lock_R2_fail(
                    schema_name=schema_name,
                    doc_id=document_id,
                )
                _lock_acquired = False
            except Exception as _rel_err:
                logger.warning(f"No se pudo liberar lock R2 en except: {_rel_err}")
        _failure_reason = _failure_reason or str(e)[:300]
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
        if _lock_acquired:
            try:
                await release_signing_lock_R2_fail(
                    schema_name=schema_name,
                    doc_id=document_id,
                )
                _lock_acquired = False
            except Exception as _rel_err:
                logger.warning(f"No se pudo liberar lock R2 en except genérico: {_rel_err}")
        await log_signature_event(
            schema_name=schema_name,
            document_id=document_id,
            user_id=user_id,
            signature_method="electronic",
            result="fail",
            failure_reason=_failure_reason,
        )
        logger.error(
            f"Error al firmar documento {document_id[:8]}...: {type(e).__name__}: {e}"
        )
        raise ValidationError(
            "No se pudo completar la firma del documento. Reintentá en unos "
            "segundos; si el problema persiste, avisá a soporte."
        )
