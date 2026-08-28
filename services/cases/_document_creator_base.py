
from typing import Dict, Any, Callable
from datetime import datetime
from fastapi.concurrency import run_in_threadpool

from database import fetch_one, execute
from shared.exceptions import (
    ValidationError, ExternalServiceError, DocumentNotFoundError
)
from shared.logging import get_logger
from shared.numbering import generate_official_number
from services.documents.lifecycle.creation import create_document

logger = get_logger(__name__)


async def create_and_sign_case_document(
    document_type_acronym: str,
    reference: str,
    html_builder: Callable[[], str],
    payload_builder: Callable[[str, str, str, str], Dict[str, Any]],
    orchestrator_endpoint: str,
    case_id: str,
    user_id: str,
    *,
    schema_name: str,
    connection=None
) -> Dict[str, Any]:
    logger.info(f"Iniciando creación de documento {document_type_acronym}")
    logger.info(f"Case ID: {case_id[:8]}...")
    logger.info(f"User ID: {user_id[:8]}...")

    logger.info(f"PASO 1: Creando documento {document_type_acronym}...")
    document = await create_document(
        document_type_acronym=document_type_acronym,
        reference=reference,
        creator_id=user_id,
        schema_name=schema_name
    )

    document_id = document['document_id']
    document_type_name = document['document_type_name']
    logger.info(f"Documento creado: {document_id[:8]}...")

    try:
        logger.info("PASO 2: Construyendo HTML...")

        html_content = html_builder()

        logger.info("Guardando contenido...")

        content_structure = {
            "html": html_content,
            "format_version": "2.0",
            "updated_at": datetime.now().isoformat()
        }

        await execute(
            """
            UPDATE document_draft
            SET content = $1::jsonb,
                last_modified_at = CURRENT_TIMESTAMP
            WHERE id = $2
            """,
            content_structure,
            document_id,
            schema_name=schema_name,
        )

        logger.info("Contenido guardado")

        logger.info("PASO 3: Recolectando datos para generate_official_number...")
        current_year = datetime.now().year

        type_result = await fetch_one(
            "SELECT id as document_type_id FROM document_types WHERE acronym = $1",
            document_type_acronym,
            schema_name=schema_name,
        )
        if not type_result:
            raise ValidationError(f"Tipo de documento {document_type_acronym} no encontrado")
        document_type_id = type_result['document_type_id']

        doc_data = await fetch_one(
            "SELECT reference FROM document_draft WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )
        if not doc_data:
            raise DocumentNotFoundError(document_id)
        reference_text = doc_data['reference']

        signers_result = await fetch_one(
            """
            SELECT json_agg(
                json_build_object(
                    'user_id', ds.user_id,
                    'full_name', u.full_name,
                    'status', ds.status,
                    'is_numerator', ds.is_numerator,
                    'signing_order', ds.signing_order,
                    'signed_at', ds.signed_at
                )
            ) as signers
            FROM document_signers ds
            JOIN users u ON ds.user_id = u.id
            WHERE ds.document_id = $1
            """,
            document_id,
            schema_name=schema_name,
        )
        signers_data = signers_result['signers'] if signers_result else []

        signer_sectors_result = await fetch_one(
            """
            SELECT ARRAY_AGG(DISTINCT u.sector_id) FILTER (WHERE u.sector_id IS NOT NULL) as sector_ids
            FROM document_signers ds
            JOIN users u ON ds.user_id = u.id
            WHERE ds.document_id = $1
            """,
            document_id,
            schema_name=schema_name,
        )
        signer_sector_ids = signer_sectors_result['sector_ids'] if signer_sectors_result else None

        logger.info("Datos recolectados")

        logger.info("PASO 4: Construyendo payload y generando número oficial (lock ultra corto ~5ms)...")

        payload = payload_builder(document_id, document_type_name, None, user_id)
        payload.pop("official_document_number", None)
        logger.info(f"Payload construido con {len(payload)} campos")

        official_number, department_id, global_sequence = await generate_official_number(
            document_type_acronym=document_type_acronym,
            user_id=user_id,
            year=current_year,
            schema_name=schema_name,
            document_id=document_id,
            reference=reference_text,
            document_type_id=document_type_id,
            content=payload,
            signers=signers_data,
            signer_sector_ids=signer_sector_ids,
        )

        logger.info(f"Número oficial: {official_number}")
        logger.info(f"Global sequence: {global_sequence}")

        payload["official_document_number"] = official_number

        logger.info(f"PASO 6: Ejecutando flujo directo {orchestrator_endpoint}...")

        try:
            api_result = await _route_document_creation(
                orchestrator_endpoint, payload, schema_name=schema_name,
                document_id=document_id, user_id=user_id,
            )
            logger.info("Flujo directo exitoso")

        except Exception as e:
            logger.error(f"Flujo directo falló: {str(e)}")
            logger.error(
                f"official_documents queda con signed_at=NULL para doc={document_id}. "
                f"Número {official_number} reservado como hueco aceptable."
            )
            raise ExternalServiceError(f"Error en flujo directo: {str(e)}")

        logger.info("PASO 7: Confirmando firma en official_documents (UPDATE)...")

        await execute(
            """
            UPDATE official_documents
            SET signed_at = CURRENT_TIMESTAMP
            WHERE id = $1 AND signed_at IS NULL
            """,
            document_id,
            schema_name=schema_name,
        )

        await execute(
            """
            UPDATE official_documents
            SET signers = (
                SELECT jsonb_agg(
                    CASE WHEN s->>'user_id' = $1
                        THEN jsonb_set(
                            jsonb_set(s, '{status}', '"signed"'),
                            '{signed_at}', to_jsonb(to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'))
                        )
                        ELSE s
                    END
                )
                FROM jsonb_array_elements(signers) s
            )
            WHERE id = $2
            """,
            user_id,
            document_id,
            schema_name=schema_name,
        )

        logger.info("official_documents actualizado con signed_at y signers")

        logger.info("PASO 8: Actualizando estados a 'signed'...")

        await execute(
            "UPDATE document_draft SET status = 'signed' WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )
        await execute(
            """
            UPDATE document_signers
            SET status = 'signed', signed_at = CURRENT_TIMESTAMP
            WHERE document_id = $1 AND user_id = $2
            """,
            document_id,
            user_id,
            schema_name=schema_name,
        )

        logger.info("Estados actualizados")
        logger.info("Proceso completado exitosamente")
        logger.info("NOTA: El documento NO está vinculado al case aún")
        logger.info("El llamador debe usar CaseService.link_official_document()")

        return {
            "success": True,
            "document_id": document_id,
            "official_number": official_number,
            "message": f"Documento {document_type_acronym} creado exitosamente: {official_number}"
        }

    except Exception as e:
        logger.error(
            f"ERROR GENERAL en create_and_sign_case_document ({document_type_acronym}): {str(e)} | "
            f"doc={document_id} queda como draft con signed_at=NULL en official_documents "
            f"(si generate_official_number ya ejecutó). Hueco aceptable por diseño."
        )
        raise


async def _route_document_creation(
    endpoint: str, payload: Dict[str, Any], *, schema_name: str,
    document_id: str = None, user_id: str = None,
) -> Dict[str, Any]:
    logger.info(f"Detectando endpoint: {endpoint}")

    if endpoint == "/create-case-cover":
        logger.info("Ejecutando flujo directo para CAEX...")
        from services.shared.pdfcomposer_api import call_pdfcomposer_create_case
        return await _direct_pdf_flow(
            call_pdfcomposer_create_case, payload,
            schema_name=schema_name, label="caratula", document_id=document_id,
            user_id=user_id,
        )

    elif endpoint == "/create-transfer-document":
        logger.info("Ejecutando flujo directo para PV...")
        from services.shared.pdfcomposer_api import call_pdfcomposer_create_transfer
        return await _direct_pdf_flow(
            call_pdfcomposer_create_transfer, payload,
            schema_name=schema_name, label="pase", document_id=document_id,
            user_id=user_id,
        )

    elif endpoint == "/create-ifrlm":
        logger.info("Ejecutando flujo directo para IFRLM...")
        from services.shared.pdfcomposer_api import call_pdfcomposer_create_ifrlm
        return await _direct_pdf_flow(
            call_pdfcomposer_create_ifrlm, payload,
            schema_name=schema_name, label="informe de legajo", document_id=document_id,
            user_id=user_id,
        )

    else:
        raise NotImplementedError(
            f"Endpoint {endpoint} no soportado. "
            f"Endpoints soportados: /create-case-cover, /create-transfer-document, /create-ifrlm"
        )


async def _direct_pdf_flow(
    pdfcomposer_call,
    payload: Dict[str, Any],
    *,
    schema_name: str,
    label: str = "documento",
    document_id: str = None,
    user_id: str = None,
) -> Dict[str, Any]:
    logger.info(f"Iniciando flujo directo para {label}...")

    try:
        logger.info(f"1/3: Generando PDF para {label}...")
        pdf_bytes = await pdfcomposer_call(payload, schema_name=schema_name)
        logger.info(f"PDF generado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

        logger.info("2/3: Firmando PDF con Notary...")
        from services.shared.notary_api import call_notary_sign_pdf

        _seal_inline = False

        signed_pdf_bytes = await call_notary_sign_pdf(
            pdf_bytes=pdf_bytes,
            signer_name=payload["signer_full_name"],
            signer_seal=payload["signer_seal"],
            signer_department=payload["signer_department"],
            signer_municipality=payload["signer_municipality"],
            official_number=payload["official_document_number"],
            city=payload["city_name"],
            tenant_id=schema_name,
            schema_name=schema_name,
            defer_timestamp=not _seal_inline,
        )
        logger.info(f"PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

        logger.info("3/3: Subiendo a R2 oficial...")
        from services.storage.cloudflare import get_tenant_r2_client
        r2_client = await get_tenant_r2_client(schema_name=schema_name)

        from services.storage.pdf_location import (
            target_pdf_location, persist_pdf_location, effective_pdf_location,
        )
        _target_loc = target_pdf_location()
        filename_oficial = f"{payload['official_document_number']}.pdf"
        _upload_res = await run_in_threadpool(
            r2_client.upload_oficial, signed_pdf_bytes, filename_oficial, _target_loc
        )
        _effective_loc = effective_pdf_location(_upload_res, _target_loc)
        await persist_pdf_location(
            document_id, _effective_loc, schema_name=schema_name,
            official_number=payload["official_document_number"],
        )
        logger.info(f"Subido a R2 {_effective_loc}: {filename_oficial}")

        official_number_signed = payload['official_document_number']
        if True:
            if document_id:
                try:
                    import asyncio as _asyncio
                    from services.storage.publish_public import maybe_publish_official_pdf
                    from config.constants import PUBLISH_PUBLIC_MAX_RETRIES

                    for _attempt in range(1, PUBLISH_PUBLIC_MAX_RETRIES + 1):
                        _published = await maybe_publish_official_pdf(
                            schema_name=schema_name,
                            official_number=official_number_signed,
                            document_id=document_id,
                            signed_pdf_bytes=signed_pdf_bytes,
                        )
                        if _published:
                            break
                        logger.warning(
                            f"_document_creator_base.publish_public_retry label={label} "
                            f"num={official_number_signed} attempt={_attempt}/{PUBLISH_PUBLIC_MAX_RETRIES}"
                        )
                        if _attempt < PUBLISH_PUBLIC_MAX_RETRIES:
                            await _asyncio.sleep(1.0)
                    else:
                        logger.error(
                            f"publish_public_failed document_id={document_id} label={label} "
                            f"schema={schema_name} num={official_number_signed} "
                            f"attempts={PUBLISH_PUBLIC_MAX_RETRIES}"
                        )
                except Exception as _pub_err:
                    logger.warning(
                        f"_document_creator_base.publish_public_failed label={label} "
                        f"num={official_number_signed}: {_pub_err} (soft-fail)"
                    )
        else:
            try:
                from shared.alerts import send_alert_mail
                await send_alert_mail(
                    subject=f"[GDI {label.upper()}] Documento B-B definitivo — {official_number_signed}",
                    body=(
                        f"El documento {label} {document_id} ({official_number_signed}, "
                        f"schema {schema_name}) se firmó y numeró correctamente, pero no "
                        f"había cupo del rate limiter TSA en el momento de crearlo. Queda "
                        f"B-B definitivo (firma electrónica válida, sin sello de tiempo de "
                        f"tercero) — bajo GDI-253 no hay ningún carril asíncrono que lo "
                        f"vaya a sellar después. NO se publicó en el bucket público "
                        f"(invariante GDI-223)."
                    ),
                    schema_name=schema_name,
                )
            except Exception as _alert_err:
                logger.error(f"_document_creator_base.b_b_definitivo_alert_err: {_alert_err}")

        logger.info(f"Flujo completado exitosamente para {label}")

        return {
            "status": "success",
            "message": f"{label.capitalize()} creado exitosamente: {payload['official_document_number']}",
            "data": {
                "official_document_number": payload['official_document_number'],
                "signed_url": f"https://cloudflare.r2/oficial/{filename_oficial}"
            }
        }

    except Exception as e:
        logger.error(f"Error en flujo directo: {str(e)}")
        raise ExternalServiceError(f"Error creando {label}: {str(e)}")
