
from shared.logging import get_logger
from typing import Dict, Any, Optional
from datetime import datetime
from fastapi.concurrency import run_in_threadpool

from database import get_conn
from shared.exceptions import (
    ValidationError, ExternalServiceError, DocumentNotFoundError
)
from shared.numbering import generate_official_number, generate_citizen_official_number
from services.shared.signer_data import get_signer_data, get_citizen_signer_data
from services.documents.lifecycle.creation import create_document
from config.constants import (
    DOCUMENT_TYPE_CAEX,
    CASE_COVER_REFERENCE_TEMPLATE,
    CASE_COVER_CREATED_SUCCESS,
    CASE_COVER_CREATION_ERROR,
    DEFAULT_LOGO_URL,
)
from services.shared.settings_utils import get_city_from_settings

logger = get_logger(__name__)


async def create_case_cover(
    case_id: str,
    case_number: str,
    case_reference: str,
    case_template_acronym: str,
    case_template_name: str,
    filing_department_id: str,
    user_id: Optional[str] = None,
    citizen_id: Optional[str] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    if bool(user_id) == bool(citizen_id):
        raise ValidationError("Se requiere exactamente uno de user_id o citizen_id")
    is_citizen_actor = citizen_id is not None
    actor_id = citizen_id if is_citizen_actor else user_id

    logger.info(f"Iniciando creación de carátula para expediente {case_number}")
    logger.info(f"Case ID: {case_id[:8]}")
    logger.info(f"{'Citizen' if is_citizen_actor else 'User'} ID: {actor_id[:8]}")

    logger.info("PASO 1: Creando documento CAEX...")
    if is_citizen_actor:
        caex_document = await create_document(
            document_type_acronym=DOCUMENT_TYPE_CAEX,
            reference=CASE_COVER_REFERENCE_TEMPLATE.format(case_number=case_number),
            schema_name=schema_name,
            auth_source="tad",
            citizen_id=citizen_id,
        )
    else:
        caex_document = await create_document(
            document_type_acronym=DOCUMENT_TYPE_CAEX,
            reference=CASE_COVER_REFERENCE_TEMPLATE.format(case_number=case_number),
            creator_id=user_id,
            schema_name=schema_name
        )

    document_id = caex_document['document_id']
    document_type_name = caex_document['document_type_name']
    logger.info(f"Documento CAEX creado: {document_id[:8]}...")

    try:
        logger.info("PASO 2: Construyendo HTML de carátula...")

        caratula_html = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h1 style="text-align: center; color: #333;">CARÁTULA DE EXPEDIENTE</h1>
            <hr style="border: 2px solid #333; margin: 20px 0;">

            <div style="margin: 20px 0;">
                <p><strong>Número de Expediente:</strong> {case_number}</p>
                <p><strong>Tipo de Expediente:</strong> {case_template_name} ({case_template_acronym})</p>
                <p><strong>Motivo/Referencia:</strong> {case_reference}</p>
            </div>

            <div style="margin: 20px 0;">
                <p><strong>Creado automáticamente el:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
            </div>
        </div>
        """

        async with get_conn(schema_name=schema_name) as conn:
            content_structure = {
                "html": caratula_html,
                "format_version": "2.0",
                "updated_at": datetime.now().isoformat()
            }
            await conn.execute(
                """
                UPDATE document_draft
                SET content = $1::jsonb,
                    last_modified_at = CURRENT_TIMESTAMP
                WHERE id = $2
                """,
                content_structure,
                document_id
            )

        logger.info("Contenido HTML guardado exitosamente")

        logger.info("PASO 3: Recolectando datos para generate_official_number...")
        current_year = datetime.now().year

        async with get_conn(schema_name=schema_name) as conn:
            type_result = await conn.fetchrow(
                "SELECT id as document_type_id FROM document_types WHERE acronym = $1",
                DOCUMENT_TYPE_CAEX
            )
            if not type_result:
                raise ValidationError(f"Tipo de documento {DOCUMENT_TYPE_CAEX} no encontrado")
            document_type_id = type_result['document_type_id']

            doc_ref_row = await conn.fetchrow(
                "SELECT reference FROM document_draft WHERE id = $1",
                document_id
            )
            if not doc_ref_row:
                raise DocumentNotFoundError(document_id)
            reference_text = doc_ref_row['reference']

            signers_result = await conn.fetchrow(
                """
                SELECT json_agg(
                    json_build_object(
                        'user_id', ds.user_id,
                        'citizen_id', ds.citizen_id,
                        'full_name', COALESCE(u.full_name, c.full_name),
                        'status', ds.status,
                        'is_numerator', ds.is_numerator,
                        'signing_order', ds.signing_order,
                        'signed_at', ds.signed_at
                    )
                ) as signers
                FROM document_signers ds
                LEFT JOIN users u ON ds.user_id = u.id
                LEFT JOIN citizens c ON ds.citizen_id = c.id
                WHERE ds.document_id = $1
                """,
                document_id
            )
            signers_data = signers_result['signers'] if signers_result else []

            signer_sectors_result = await conn.fetchrow(
                """
                SELECT ARRAY_AGG(DISTINCT u.sector_id) FILTER (WHERE u.sector_id IS NOT NULL) as sector_ids
                FROM document_signers ds
                LEFT JOIN users u ON ds.user_id = u.id
                WHERE ds.document_id = $1
                """,
                document_id
            )
            signer_sector_ids = signer_sectors_result['sector_ids'] if signer_sectors_result else None

            settings_result = await conn.fetchrow("SELECT logo_url, city FROM settings LIMIT 1")
            logo_url = (
                settings_result['logo_url']
                if settings_result and settings_result.get('logo_url')
                else DEFAULT_LOGO_URL
            )
            city = await get_city_from_settings(conn=conn, schema_name=schema_name)

        if is_citizen_actor:
            signer_data_for_payload = await get_citizen_signer_data(citizen_id, schema_name=schema_name)
        else:
            signer_data_for_payload = await get_signer_data(user_id, schema_name=schema_name)

        logger.info("PASO 3: Generando numero oficial (lock ultra corto ~5ms)...")

        cover_data = {
            "municipality_logo_url": logo_url,
            "document_type_acronym": "CAEX",
            "document_type_name": document_type_name,
            "document_reference": f"Creación {case_number}",
            "case_number": case_number,
            "case_type_acronym": case_template_acronym,
            "case_type_name": case_template_name,
            "case_motive": case_reference,
            "initiating_department": signer_data_for_payload['department_name'],
            "case_creator": signer_data_for_payload['full_name'],
            "signer_full_name": signer_data_for_payload['full_name'],
            "signer_seal": signer_data_for_payload['seal'],
            "signer_department": signer_data_for_payload['department_name'],
            "signer_municipality": signer_data_for_payload['municipality_name'],
            "city_name": city
        }

        if is_citizen_actor:
            official_number, department_id, global_sequence = await generate_citizen_official_number(
                document_type_acronym="CAEX",
                citizen_id=citizen_id,
                year=current_year,
                schema_name=schema_name,
                document_id=document_id,
                reference=reference_text,
                document_type_id=document_type_id,
                content=cover_data,
                signers=signers_data,
                signer_sector_ids=signer_sector_ids,
            )
        else:
            official_number, department_id, global_sequence = await generate_official_number(
                document_type_acronym="CAEX",
                user_id=user_id,
                year=current_year,
                schema_name=schema_name,
                document_id=document_id,
                reference=reference_text,
                document_type_id=document_type_id,
                content=cover_data,
                signers=signers_data,
                signer_sector_ids=signer_sector_ids,
            )
        logger.info(f"Número oficial generado: {official_number}")
        logger.info(f"Global sequence: {global_sequence}")

        logger.info("PASO 5: Generando, firmando y publicando carátula...")
        try:
            logger.info("5.1: Generando PDF con PDFComposer /create-case/...")
            from services.shared.pdfcomposer_api import call_pdfcomposer_create_case

            pdf_bytes = await call_pdfcomposer_create_case(cover_data, schema_name=schema_name)
            logger.info(f"[OK] PDF generado: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

            logger.info("5.2: Firmando PDF con Notary...")
            from services.shared.notary_api import call_notary_sign_pdf

            _seal_inline = False

            signed_pdf_bytes = await call_notary_sign_pdf(
                pdf_bytes=pdf_bytes,
                signer_name=cover_data["signer_full_name"],
                signer_seal=cover_data["signer_seal"],
                signer_department=cover_data["signer_department"],
                signer_municipality=cover_data["signer_municipality"],
                official_number=official_number,
                city=cover_data["city_name"],
                tenant_id=schema_name,
                schema_name=schema_name,
                defer_timestamp=not _seal_inline,
            )
            logger.info(f"[OK] PDF firmado: {len(signed_pdf_bytes)} bytes ({len(signed_pdf_bytes)/1024:.2f} KB)")

            logger.info("5.3: Subiendo a R2 oficial...")
            from services.storage.cloudflare import get_tenant_r2_client
            r2_client = await get_tenant_r2_client(schema_name=schema_name)

            from services.storage.pdf_location import (
                target_pdf_location, persist_pdf_location, effective_pdf_location,
            )
            _target_loc = target_pdf_location()
            filename_oficial = f"{official_number}.pdf"
            _upload_res = await run_in_threadpool(
                r2_client.upload_oficial, signed_pdf_bytes, filename_oficial, _target_loc
            )
            _effective_loc = effective_pdf_location(_upload_res, _target_loc)
            await persist_pdf_location(document_id, _effective_loc, schema_name=schema_name)
            logger.info(f"[OK] Subido a R2 {_effective_loc}: {filename_oficial}")

            if True:
                try:
                    import asyncio as _asyncio
                    from services.storage.publish_public import maybe_publish_official_pdf
                    from config.constants import PUBLISH_PUBLIC_MAX_RETRIES

                    for _attempt in range(1, PUBLISH_PUBLIC_MAX_RETRIES + 1):
                        _published = await maybe_publish_official_pdf(
                            schema_name=schema_name,
                            official_number=official_number,
                            document_id=document_id,
                            document_type_id=DOCUMENT_TYPE_CAEX,
                            signed_pdf_bytes=signed_pdf_bytes,
                        )
                        if _published:
                            break
                        logger.warning(
                            f"cover_creator.publish_public_retry num={official_number} "
                            f"attempt={_attempt}/{PUBLISH_PUBLIC_MAX_RETRIES}"
                        )
                        if _attempt < PUBLISH_PUBLIC_MAX_RETRIES:
                            await _asyncio.sleep(1.0)
                    else:
                        logger.error(
                            f"publish_public_failed document_id={document_id} "
                            f"schema={schema_name} num={official_number} "
                            f"attempts={PUBLISH_PUBLIC_MAX_RETRIES}"
                        )
                except Exception as _pub_err:
                    logger.warning(
                        f"cover_creator.publish_public_failed num={official_number}: "
                        f"{_pub_err} (soft-fail, no bloquea la creación del expediente)"
                    )
            else:
                try:
                    from shared.alerts import send_alert_mail
                    await send_alert_mail(
                        subject=f"[GDI CAEX] Carátula B-B definitiva — {official_number}",
                        body=(
                            f"La carátula {document_id} ({official_number}, "
                            f"schema {schema_name}) se firmó y numeró correctamente, "
                            f"pero no había cupo del rate limiter TSA en el momento de "
                            f"crear el expediente. Queda B-B definitivo (firma "
                            f"electrónica válida, sin sello de tiempo de tercero) — "
                            f"bajo GDI-253 no hay ningún carril asíncrono que la vaya a "
                            f"sellar después. NO se publicó en el bucket público "
                            f"(invariante GDI-223)."
                        ),
                        schema_name=schema_name,
                    )
                except Exception as _alert_err:
                    logger.error(f"cover_creator.b_b_definitivo_alert_err: {_alert_err}")

        except Exception as e:
            logger.error(f"[ERR] Fallo en generacion/firma/publicacion: {str(e)}")
            logger.error(
                f"official_documents queda con signed_at=NULL para doc={document_id}. "
                f"Número {official_number} reservado como hueco aceptable."
            )
            raise ExternalServiceError(CASE_COVER_CREATION_ERROR.format(error=str(e)))

        logger.info("PASO 6: Confirmando firma en official_documents (UPDATE)...")
        async with get_conn(schema_name=schema_name) as conn:
            await conn.execute(
                """
                UPDATE official_documents
                SET signed_at = CURRENT_TIMESTAMP
                WHERE id = $1 AND signed_at IS NULL
                """,
                document_id
            )

            match_key = 'citizen_id' if is_citizen_actor else 'user_id'
            await conn.execute(
                f"""
                UPDATE official_documents
                SET signers = (
                    SELECT jsonb_agg(
                        CASE WHEN s->>'{match_key}' = $1
                            THEN jsonb_set(
                                jsonb_set(s, '{{status}}', '"signed"'),
                                '{{signed_at}}', to_jsonb(to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'))
                            )
                            ELSE s
                        END
                    )
                    FROM jsonb_array_elements(signers) s
                )
                WHERE id = $2
                """,
                actor_id, document_id
            )
        logger.info("official_documents actualizado con signed_at y signers")

        logger.info("PASO 7: Actualizando estados a 'signed'...")
        async with get_conn(schema_name=schema_name) as conn:
            await conn.execute(
                "UPDATE document_draft SET status = 'signed' WHERE id = $1",
                document_id
            )
            if is_citizen_actor:
                await conn.execute(
                    """
                    UPDATE document_signers
                    SET status = 'signed', signed_at = CURRENT_TIMESTAMP
                    WHERE document_id = $1 AND citizen_id = $2
                    """,
                    document_id, citizen_id
                )
            else:
                await conn.execute(
                    """
                    UPDATE document_signers
                    SET status = 'signed', signed_at = CURRENT_TIMESTAMP
                    WHERE document_id = $1 AND user_id = $2
                    """,
                    document_id, user_id
                )
        logger.info("Estados actualizados a 'signed'")

        logger.info("PASO 8: Vinculando carátula al expediente...")
        async with get_conn(schema_name=schema_name) as conn:
            if is_citizen_actor:
                await conn.execute(
                    """
                    INSERT INTO case_official_documents (
                        case_id, official_document_id, linking_citizen, order_number,
                        linking_date, is_active
                    ) VALUES ($1, $2, $3, 1, CURRENT_TIMESTAMP, true)
                    """,
                    case_id, document_id, citizen_id
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO case_official_documents (
                        case_id, official_document_id, linking_user_id, order_number,
                        linking_date, is_active
                    ) VALUES ($1, $2, $3, 1, CURRENT_TIMESTAMP, true)
                    """,
                    case_id, document_id, user_id
                )
        logger.info(f"Carátula vinculada al expediente")
        logger.info(f"Link: case_id={case_id[:8]} -> document_id={document_id[:8]}")
        logger.info("Proceso completado exitosamente")

        return {
            "success": True,
            "document_id": document_id,
            "official_number": official_number,
            "message": CASE_COVER_CREATED_SUCCESS.format(official_number=official_number)
        }

    except Exception as e:
        logger.error(
            f"ERROR GENERAL en create_case_cover: {str(e)} | "
            f"doc={document_id} queda como draft con signed_at=NULL en official_documents "
            f"(si generate_official_number ya ejecutó). Hueco aceptable por diseño."
        )
        raise
