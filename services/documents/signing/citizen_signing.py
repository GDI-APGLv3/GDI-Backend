
import base64
import binascii
from typing import Dict, Any, Optional

from shared.logging import get_logger
from shared.exceptions import ValidationError, DocumentStateError, AuthorizationError, ExternalServiceError
from database import fetch_one, execute, transaction
from fastapi.concurrency import run_in_threadpool

from services.shared.signer_data import get_citizen_signer_data
from services.shared.file_validation import validate_embedded_file
from shared.numbering import reserve_citizen_number, confirm_number, finalize_number, cancel_number
from config.constants import (
    MAX_EMBEDDED_FILES_PER_DOCUMENT,
    MAX_EMBEDDED_FILE_SIZE,
    MAX_TOTAL_EMBEDDED_SIZE,
    EMBEDDED_FILE_MAX_COUNT_ERROR,
    EMBEDDED_FILE_INDIVIDUAL_SIZE_ERROR,
    EMBEDDED_FILE_TOTAL_SIZE_ERROR,
)

logger = get_logger(__name__)


async def sign_and_number_citizen_document(document_id: str, citizen_id: str, *, schema_name: str) -> Dict[str, Any]:
    logger.info(f"Firma+numeracion citizen: doc={document_id[:8]}... citizen={citizen_id[:8]}...")

    signer_info = await fetch_one(
        """
        SELECT ds.is_numerator, ds.status as signer_status, dd.status as doc_status,
               dd.reference, dd.content, dd.document_type_id, dd.resume,
               dt.acronym as document_type_acronym, dt.type as source_type,
               dt.special_numbering
        FROM document_signers ds
        JOIN document_draft dd ON ds.document_id = dd.id
        JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE ds.document_id = $1 AND ds.citizen_id = $2
        """,
        document_id, citizen_id,
        schema_name=schema_name,
    )

    if not signer_info:
        raise AuthorizationError(f"El ciudadano {citizen_id} no es firmante del documento {document_id}")

    if not signer_info['is_numerator']:
        raise ValidationError("El ciudadano no es el numerador de este documento (no soportado en v1)")

    if signer_info['signer_status'] not in ('pending', None):
        raise ValidationError(f"El ciudadano ya firmo este documento (status: {signer_info['signer_status']})")

    if signer_info['doc_status'] != 'sent_to_sign':
        raise DocumentStateError(
            f"Documento en estado '{signer_info['doc_status']}' no puede firmarse",
            current_state=signer_info['doc_status'],
            required_state="sent_to_sign",
        )

    from datetime import datetime
    current_year = datetime.now().year

    content_for_reserve = signer_info['content'] or {}
    field_defs_row = await fetch_one(
        "SELECT field_definitions FROM document_type_fields WHERE document_type_id = $1",
        signer_info['document_type_id'],
        schema_name=schema_name,
    )
    if field_defs_row is not None and field_defs_row.get('field_definitions'):
        field_defs = field_defs_row['field_definitions']
        from services.documents.ffcc_validator import validate_ffcc_content

        raw_data = content_for_reserve if isinstance(content_for_reserve, dict) else {}
        validate_ffcc_content(raw_data, field_defs, schema_name=schema_name, enforce_required=True)
        content_for_reserve = {"schema": field_defs, "data": raw_data}
        logger.info(f"Snapshot FFCC armado (citizen): {len(field_defs)} campos en schema")

    official_number, department_id, sequence, reservation_id = await reserve_citizen_number(
        document_type_acronym=signer_info['document_type_acronym'] or "DOC",
        citizen_id=citizen_id,
        year=current_year,
        schema_name=schema_name,
        document_id=document_id,
        reference=signer_info['reference'],
        document_type_id=signer_info['document_type_id'],
        content=content_for_reserve,
        resume=signer_info.get('resume'),
        signers=None,
        signer_sector_ids=None,
    )
    logger.info(f"Numero reservado para citizen: {official_number} ticket={reservation_id[:8]}...")

    if signer_info.get('resume'):
        await execute(
            "UPDATE document_draft SET resume = NULL WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )

    from services.storage.cloudflare import get_tenant_r2_client
    import httpx

    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    filename = document_id.replace('-', '') + '.pdf'

    try:
        pdf_url = await run_in_threadpool(r2_client.get_tosign_url, filename)
        if not pdf_url:
            raise ValidationError("No se pudo obtener URL del PDF desde R2 tosign")

        async with httpx.AsyncClient(timeout=30.0) as client:
            pdf_response = await client.get(pdf_url)
            pdf_response.raise_for_status()
            pdf_bytes = pdf_response.content

        signer_data = await get_citizen_signer_data(citizen_id, schema_name=schema_name)

        from services.shared.notary_api import call_notary_sign_pdf
        from services.shared.settings_utils import get_city_from_settings

        city = await get_city_from_settings(schema_name=schema_name)
        source_type = signer_info.get('source_type') or ""
        stamp_position = "last" if source_type == 'Importado' else ""

        signed_pdf_bytes = await call_notary_sign_pdf(
            pdf_bytes=pdf_bytes,
            signer_name=signer_data['full_name'],
            signer_seal=signer_data['seal'],
            signer_department=signer_data['department_name'],
            signer_municipality=signer_data['municipality_name'],
            official_number=official_number,
            city=city,
            stamp_position=stamp_position,
            tenant_id=schema_name,
            schema_name=schema_name,
            defer_timestamp=True,
        )

        await confirm_number(document_id, reservation_id, schema_name=schema_name)

        from services.storage.pdf_location import (
            target_pdf_location, persist_pdf_location, effective_pdf_location,
        )
        _target_loc = target_pdf_location()
        oficial_filename = f"{official_number}.pdf"
        _upload_res = await run_in_threadpool(
            r2_client.upload_oficial, signed_pdf_bytes, oficial_filename, _target_loc
        )
        _effective_loc = effective_pdf_location(_upload_res, _target_loc)
        await persist_pdf_location(document_id, _effective_loc, schema_name=schema_name)

        await finalize_number(document_id, reservation_id, schema_name=schema_name)

        try:
            from services.documents.lifecycle.embedded_files import promote_embedded_files_to_official
            await promote_embedded_files_to_official(document_id, document_id, schema_name=schema_name)
        except Exception as e:
            logger.warning(f"promote_embedded_files_to_official fallo (soft-fail) doc={document_id[:8]}...: {e}")

        from services.storage.publish_public import maybe_publish_official_pdf
        await maybe_publish_official_pdf(
            schema_name=schema_name,
            official_number=official_number,
            document_id=document_id,
            signed_pdf_bytes=signed_pdf_bytes,
        )

        async with transaction(schema_name=schema_name, user_id=citizen_id, auth_source="tad") as conn:
            await conn.execute(
                "UPDATE official_documents SET signed_at = CURRENT_TIMESTAMP WHERE id = $1 AND signed_at IS NULL",
                document_id,
            )
            await conn.execute(
                """
                UPDATE document_signers
                SET status = 'signed', signed_at = CURRENT_TIMESTAMP
                WHERE document_id = $1 AND citizen_id = $2
                """,
                document_id, citizen_id,
            )
            _draft_rows = await conn.fetch(
                """
                UPDATE document_draft
                SET status = 'signed',
                    document_number = $1,
                    numbered_at = CURRENT_TIMESTAMP,
                    numbered_by = $2,
                    last_modified_at = CURRENT_TIMESTAMP
                WHERE id = $3 AND status = 'sent_to_sign'
                RETURNING id
                """,
                official_number, citizen_id, document_id,
            )
            if not _draft_rows:
                raise ValidationError(
                    "El documento cambio de estado mientras se confirmaba la firma (ya rechazado/firmado)"
                )

        try:
            await run_in_threadpool(r2_client.delete_tosign, filename)
        except Exception as e:
            logger.warning(f"No se pudo eliminar PDF de R2 tosign (soft-fail): {e}")

        try:
            from services.documents.signing.audit_logger import log_signature_event
            await log_signature_event(
                schema_name=schema_name,
                document_id=document_id,
                user_id=citizen_id,
                signature_method="electronic",
                result="ok",
                official_number=official_number,
                r2_object_key=oficial_filename,
                actor_type="citizen",
            )
        except Exception as _audit_err:
            logger.warning(f"audit_log fallo (soft-fail): {_audit_err}")

        logger.info(f"Documento citizen firmado y numerado: {official_number}")

        return {
            "success": True,
            "message": "Documento firmado y numerado exitosamente",
            "document_id": document_id,
            "official_number": official_number,
            "document_status": "signed",
            "pdf_location": _effective_loc,
        }

    except Exception as e:
        logger.error(f"Firma citizen fallida doc={document_id[:8]}...: {e}")
        try:
            await cancel_number(
                document_id,
                schema_name=schema_name,
                reason=f"firma_citizen_fallida: {str(e)[:400]}",
            )
        except Exception as cancel_err:
            logger.error(f"cancel_number fallo (soft-fail): {cancel_err}")

        try:
            from services.documents.signing.audit_logger import log_signature_event
            await log_signature_event(
                schema_name=schema_name,
                document_id=document_id,
                user_id=citizen_id,
                signature_method="electronic",
                result="fail",
                failure_reason=str(e)[:300],
                official_number=official_number,
                actor_type="citizen",
            )
        except Exception as _audit_err:
            logger.warning(f"audit_log fallo en path de error (soft-fail): {_audit_err}")

        if isinstance(e, (ValidationError, DocumentStateError, AuthorizationError)):
            raise
        raise ValidationError(f"Error al firmar documento: {str(e)}")


TAD_IMPORTED_PDF_MAX_SIZE = 20 * 1024 * 1024


def _decode_and_validate_imported_pdf(pdf_base64: str) -> bytes:
    try:
        pdf_bytes = base64.b64decode(pdf_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError("'pdf_base64' no es base64 válido")

    if not pdf_bytes:
        raise ValidationError("'pdf_base64' no puede estar vacío")

    if len(pdf_bytes) > TAD_IMPORTED_PDF_MAX_SIZE:
        raise ValidationError(
            f"El PDF excede el tamaño máximo ({TAD_IMPORTED_PDF_MAX_SIZE // (1024 * 1024)}MB). "
            f"Tamaño recibido: {len(pdf_bytes) / 1024 / 1024:.2f}MB"
        )

    if not pdf_bytes.startswith(b'%PDF'):
        raise ValidationError("'pdf_base64' no decodifica a un PDF válido")

    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if len(reader.pages) < 1:
            raise ValidationError("'pdf_base64' no contiene páginas: no es un PDF válido")
    except ValidationError:
        raise
    except Exception:
        raise ValidationError(
            "'pdf_base64' no decodifica a un PDF válido (archivo corrupto o mal formado)"
        )

    return pdf_bytes


def _decode_and_validate_embedded_files(
    embedded_files: Optional[list],
    *,
    doc_type: Dict[str, Any],
    is_imported: bool,
    document_type_acronym: str,
) -> list:
    if not embedded_files:
        return []

    if is_imported:
        raise ValidationError(
            f"El tipo de documento '{document_type_acronym}' es Importado: el PDF importado no se "
            "regenera, por lo que 'embedded_files' no está soportado para este tipo."
        )

    if not doc_type.get("accepts_embedded_files"):
        raise ValidationError(
            f"El tipo de documento '{document_type_acronym}' no admite archivos adjuntos embebidos "
            "('accepts_embedded_files' = false)."
        )

    if not isinstance(embedded_files, list):
        raise ValidationError("'embedded_files' debe ser una lista de objetos {file_name, content_base64}")

    if len(embedded_files) > MAX_EMBEDDED_FILES_PER_DOCUMENT:
        raise ValidationError(
            EMBEDDED_FILE_MAX_COUNT_ERROR.format(max_count=MAX_EMBEDDED_FILES_PER_DOCUMENT)
        )

    decoded: list = []
    total_size = 0
    for i, item in enumerate(embedded_files):
        if not isinstance(item, dict):
            raise ValidationError(f"'embedded_files[{i}]' debe ser un objeto {{file_name, content_base64}}")

        file_name = item.get("file_name")
        content_base64 = item.get("content_base64")
        if not file_name or not isinstance(file_name, str):
            raise ValidationError(f"'embedded_files[{i}].file_name' es requerido y debe ser texto")
        if not content_base64 or not isinstance(content_base64, str):
            raise ValidationError(f"'embedded_files[{i}].content_base64' es requerido y debe ser texto")

        try:
            content_bytes = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValidationError(f"'embedded_files[{i}].content_base64' no es base64 válido")

        if not content_bytes:
            raise ValidationError(f"'embedded_files[{i}].content_base64' no puede decodificar a un archivo vacío")

        if len(content_bytes) > MAX_EMBEDDED_FILE_SIZE:
            raise ValidationError(
                EMBEDDED_FILE_INDIVIDUAL_SIZE_ERROR.format(max_mb=MAX_EMBEDDED_FILE_SIZE // (1024 * 1024))
            )

        total_size += len(content_bytes)
        if total_size > MAX_TOTAL_EMBEDDED_SIZE:
            raise ValidationError(
                EMBEDDED_FILE_TOTAL_SIZE_ERROR.format(max_mb=MAX_TOTAL_EMBEDDED_SIZE // (1024 * 1024))
            )

        validate_embedded_file(content_bytes, file_name)

        decoded.append((file_name, content_bytes))

    return decoded


async def create_and_sign_citizen_document(
    document_type_acronym: str,
    citizen_id: str,
    reference: str,
    *,
    schema_name: str,
    content_html: Optional[str] = None,
    pdf_base64: Optional[str] = None,
    embedded_files: Optional[list] = None,
    form_data: Optional[dict] = None,
) -> Dict[str, Any]:
    from database import fetch_one
    from services.documents.lifecycle.creation import create_document
    from services.documents.lifecycle.embedded_files import upload_embedded_files_for_citizen_document
    from services.documents.signing.signing import preparar_documento_para_firma
    from services.storage.cloudflare import get_tenant_r2_client

    doc_type = await fetch_one(
        """
        SELECT dt.id, dt.type, dt.name, dt.acronym, dt.accepts_embedded_files,
               EXISTS(SELECT 1 FROM document_type_fields dtf WHERE dtf.document_type_id = dt.id) AS has_fields
        FROM document_types dt
        WHERE dt.acronym = $1 AND dt.external_signable = true AND dt.is_active = true
        """,
        document_type_acronym,
        schema_name=schema_name,
    )
    if not doc_type:
        raise ValidationError(
            f"Tipo de documento '{document_type_acronym}' no habilitado para firma ciudadana (TAD)"
        )

    if doc_type["type"] not in ("HTML", "Importado"):
        raise ValidationError(
            f"Tipo de documento '{document_type_acronym}' (type='{doc_type['type']}') "
            "no soporta firma ciudadana (TAD) -- solo tipos Importado, HTML o FFCC."
        )

    field_defs = None
    if doc_type["has_fields"]:
        fd_row = await fetch_one(
            "SELECT field_definitions FROM document_type_fields WHERE document_type_id = $1",
            doc_type["id"],
            schema_name=schema_name,
        )
        field_defs = fd_row["field_definitions"] if fd_row else []

        for f in field_defs:
            if isinstance(f, dict) and f.get("type") == "file" and f.get("required"):
                raise ValidationError(
                    "El formulario tiene un campo de archivo obligatorio: no disponible vía TAD"
                )

        if not form_data:
            raise ValidationError(
                f"Tipo de documento '{document_type_acronym}' es un formulario controlado (FFCC): "
                "'form_data' es requerido"
            )
        if content_html:
            raise ValidationError("'form_data' y 'content_html' son mutuamente excluyentes")
        if pdf_base64:
            raise ValidationError("'form_data' y 'pdf_base64' son mutuamente excluyentes")
        if not isinstance(form_data, dict):
            raise ValidationError("'form_data' debe ser un objeto JSON {campo: valor}")

        defined_names = {f.get("name") for f in field_defs if isinstance(f, dict)}
        extra_fields = sorted(set(form_data.keys()) - defined_names)
        if extra_fields:
            raise ValidationError(
                f"Campos no definidos en el formulario: {extra_fields}. "
                "Consultar los campos validos en GET /tad/document-types/{id}/fields"
            )

        for f in field_defs:
            if not isinstance(f, dict) or f.get("type") != "file":
                continue
            value = form_data.get(f.get("name", ""))
            has_value = value is not None and value != "" and value != []
            if has_value:
                label = f.get("label", f.get("name", ""))
                raise ValidationError(
                    f"El campo '{label}' es de tipo archivo: no disponible vía TAD "
                    "(enviar null u omitir)"
                )

        from services.documents.ffcc_validator import validate_ffcc_content
        validate_ffcc_content(form_data, field_defs, schema_name=schema_name, enforce_required=True)
    elif form_data:
        raise ValidationError(
            f"Tipo de documento '{document_type_acronym}' no es un formulario controlado (FFCC): "
            "'form_data' no está permitido"
        )

    is_imported = doc_type["type"] == "Importado"
    if is_imported and not pdf_base64:
        raise ValidationError(
            f"El tipo de documento '{document_type_acronym}' es Importado: 'pdf_base64' es requerido"
        )
    if not is_imported and pdf_base64:
        raise ValidationError(
            f"El tipo de documento '{document_type_acronym}' no es Importado: 'pdf_base64' no está permitido"
        )

    pdf_bytes: Optional[bytes] = None
    if is_imported:
        pdf_bytes = _decode_and_validate_imported_pdf(pdf_base64)

    decoded_embedded_files = _decode_and_validate_embedded_files(
        embedded_files, doc_type=doc_type, is_imported=is_imported,
        document_type_acronym=document_type_acronym,
    )

    draft = await create_document(
        document_type_acronym,
        reference,
        schema_name=schema_name,
        auth_source="tad",
        citizen_id=citizen_id,
    )
    document_id = draft["document_id"]

    if decoded_embedded_files:
        await upload_embedded_files_for_citizen_document(
            document_id, citizen_id, decoded_embedded_files, schema_name=schema_name,
        )

    r2_client = await get_tenant_r2_client(schema_name=schema_name)

    import_pendiente: Optional[Dict[str, Any]] = None

    if is_imported:
        from config.constants import DEFAULT_LOGO_URL

        settings_row = await fetch_one("SELECT logo_url FROM settings LIMIT 1", schema_name=schema_name)
        logo_url = (settings_row or {}).get("logo_url") or DEFAULT_LOGO_URL

        raw_filename = document_id.replace('-', '') + '_raw.pdf'
        try:
            await run_in_threadpool(r2_client.upload_tosign, pdf_bytes, raw_filename)
        except Exception as e:
            logger.error(f"Error subiendo PDF crudo TAD a R2 tosign: {e}")
            raise ValidationError(f"Error al subir el PDF a almacenamiento: {str(e)}")

        import_pendiente = {
            "raw_filename": raw_filename,
            "url_logo": logo_url,
            "name_acrony_type": doc_type["acronym"],
            "document_type": doc_type["name"],
            "reference": reference,
        }
    else:
        if doc_type["has_fields"]:
            content_to_persist = form_data
        else:
            from shared.validation import sanitize_html
            safe_html = (
                sanitize_html(content_html, permitir_formato_inline=True)
                if content_html else f"<p>{reference}</p>"
            )
            content_to_persist = {"html": safe_html}
        from services.documents.core.queries import update_document_content_query

        await execute(
            update_document_content_query(),
            content_to_persist,
            document_id,
            schema_name=schema_name,
            user_id=citizen_id,
            auth_source="tad",
        )

    preparado = await preparar_documento_para_firma(
        document_id, citizen_id, schema_name=schema_name
    )

    if preparado["estado"] == "ya_terminado":
        raise ValidationError(
            "El documento ya había sido enviado a firma: el alta TAD no puede reusarlo"
        )

    payload: Dict[str, Any] = {
        "pdf_pendiente": True,
        "original_status": preparado["original_status"],
    }
    if import_pendiente:
        payload["import_pendiente"] = import_pendiente

    try:
        return await enqueue_citizen_signing(
            document_id, citizen_id, schema_name=schema_name, payload=payload,
        )
    except Exception:
        logger.error(
            f"create_and_sign_citizen_document: falló el encolado de {document_id} "
            f"tras marcar el documento; revirtiendo a '{preparado['original_status']}'"
        )
        try:
            await execute(
                "UPDATE document_draft SET status = $1 WHERE id = $2 AND status = 'sent_to_sign'",
                preparado["original_status"], document_id,
                schema_name=schema_name,
            )
        except Exception as rollback_err:
            logger.critical(
                f"create_and_sign_citizen_document: NO SE PUDO REVERTIR {document_id} "
                f"tras fallar el encolado — queda trabado en 'sent_to_sign'. "
                f"Requiere revisión manual: {rollback_err}"
            )
        raise


async def enqueue_citizen_signing(
    document_id: str,
    citizen_id: str,
    *,
    schema_name: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone
    from database import get_conn

    session_id = str(_uuid.uuid4())
    ttl_minutos = int(__import__("os").getenv("ESCRI_PENDING_TTL_MINUTES", "30"))
    expires_at = datetime.now(tz=timezone.utc) + timedelta(minutes=ttl_minutos)

    async with get_conn(schema_name="public") as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO public.signing_sessions
                (session_id, schema_name, document_id, reservation_id, user_id,
                 citizen_id, job_type, status, expires_at, payload)
                VALUES ($1::uuid, $2, $3::uuid, NULL, NULL,
                        $4::uuid, 'sign_citizen', 'pending', $5, $6::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING session_id::text
                """,
                session_id, schema_name, document_id, citizen_id, expires_at,
                payload or {},
            )

            if row is None:
                existente = await conn.fetchrow(
                    """
                    SELECT session_id::text AS sid, expires_at
                    FROM public.signing_sessions
                    WHERE schema_name = $1
                      AND document_id = $2::uuid
                      AND citizen_id  = $3::uuid
                      AND job_type    = 'sign_citizen'
                      AND status IN ('pending', 'processing')
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    schema_name, document_id, citizen_id,
                )
                if existente is None:
                    raise ExternalServiceError(
                        "No se pudo encolar la firma del ciudadano ni recuperar "
                        "la sesión existente"
                    )
                logger.info(
                    "GDI-205: ya había una sesión sign_citizen viva para doc=%s "
                    "— se devuelve la existente (%s), no se crea una segunda",
                    document_id[:8], existente["sid"][:8],
                )
                return {
                    "success": True,
                    "message": "La firma ya estaba encolada",
                    "document_id": document_id,
                    "session_id": existente["sid"],
                    "status": "queued",
                    "expires_at": _iso(existente["expires_at"]),
                }

            await conn.execute("SELECT pg_notify('escri', $1)", schema_name)

    logger.info(
        "GDI-205: firma de ciudadano encolada session=%s doc=%s schema=%s",
        session_id[:8], document_id[:8], schema_name,
    )
    return {
        "success": True,
        "message": "Documento recibido — la firma se está procesando",
        "document_id": document_id,
        "session_id": session_id,
        "status": "queued",
        "expires_at": _iso(expires_at),
    }


def _iso(dt) -> Optional[str]:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None
