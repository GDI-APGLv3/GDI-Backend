
import json
from shared.logging import get_logger
from datetime import datetime, timezone
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from config.constants import SYSTEM_TEST_USER_UUID
from database import execute, fetch_one, get_conn
from services.documents.signing.audit_logger import log_signature_event
from services.shared.notary_api import call_notary_sign_pdf
from services.shared.pdfcomposer_api import call_pdfcomposer_preview_pdf
from shared.numbering import OFFICIAL_DOCUMENTS_LOCK_ID

log = get_logger(__name__)

_SYSTEM_SEAL_FALLBACK = "SIS"


async def _resolve_tenant_data(cancelled_row: dict, *, schema_name: str) -> dict:
    tst_type = await fetch_one(
        "SELECT id, acronym, name FROM document_types "
        "WHERE acronym = 'TST' AND is_active = true",
        schema_name=schema_name,
    )
    if not tst_type:
        raise ValueError(
            f"[TST-creator] tipo TST no encontrado o inactivo en schema={schema_name}"
        )

    system_user = await fetch_one(
        """
        SELECT u.full_name, cs.name AS seal
        FROM users u
        LEFT JOIN user_seals us ON us.user_id = u.id
        LEFT JOIN city_seals cs ON cs.id = us.city_seal_id
        WHERE u.id = $1::uuid
        LIMIT 1
        """,
        SYSTEM_TEST_USER_UUID,
        schema_name=schema_name,
    )
    signer_name = (system_user and system_user.get("full_name")) or "Sistema TEST"
    signer_seal = (system_user and system_user.get("seal")) or _SYSTEM_SEAL_FALLBACK

    dept = await fetch_one(
        "SELECT acronym, name FROM departments WHERE id = $1::uuid",
        str(cancelled_row["department_id"]),
        schema_name=schema_name,
    )
    dept_acronym = dept["acronym"] if dept else "GDI"
    dept_name    = dept["name"]    if dept else "Gestión"

    muni_row = await fetch_one(
        "SELECT name FROM public.municipalities WHERE schema_name = $1",
        schema_name,
        schema_name="public",
    )
    municipality = (muni_row and muni_row.get("name")) or "Municipalidad"

    city_row = await fetch_one(
        "SELECT value FROM settings WHERE key = 'city_name'",
        schema_name=schema_name,
    )
    city = (city_row and city_row.get("value")) or "LATAM"

    return {
        "tst_type_id":   tst_type["id"],
        "tst_type_name": tst_type["name"] or "Documento de Prueba",
        "signer_name":   signer_name,
        "signer_seal":   signer_seal,
        "dept_acronym":  dept_acronym,
        "dept_name":     dept_name,
        "municipality":  municipality,
        "city":          city,
    }


async def create_tst_document_signed_by_system(
    cancelled_row: dict,
    *,
    schema_name: str,
) -> str:
    cancelled_id = str(cancelled_row["id"])
    global_seq   = int(cancelled_row["global_sequence"])
    year         = int(cancelled_row["year"])
    dept_id      = str(cancelled_row["department_id"])

    log.info(
        "tst_creator.start",
        extra={
            "schema_name":  schema_name,
            "global_seq":   global_seq,
            "year":         year,
            "cancelled_id": cancelled_id[:8],
        },
    )

    tenant = await _resolve_tenant_data(cancelled_row, schema_name=schema_name)

    official_number = (
        f"{tenant['dept_acronym']}-{year}-{global_seq:04d}"
        f"-TST-{tenant['signer_seal']}"
    )
    reference  = "Testing Operativo"
    new_id     = str(uuid4())
    now        = datetime.now(timezone.utc)
    content_js = json.dumps({"html": "<p>Documento de Prueba</p>"})

    log.info(
        "tst_creator.number_assigned",
        extra={"official_number": official_number, "schema_name": schema_name},
    )

    async with get_conn(schema_name=schema_name) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout = '10s'")
            await conn.execute(
                f"SELECT pg_advisory_xact_lock({OFFICIAL_DOCUMENTS_LOCK_ID}, hashtext($1))",
                schema_name,
            )

            deleted = await conn.execute(
                """
                DELETE FROM official_documents
                 WHERE id = $1::uuid
                   AND reservation_status = 'CANCELLED'
                """,
                cancelled_id,
            )
            if deleted == "DELETE 0":
                raise RuntimeError(
                    f"[TST-creator] fila CANCELLED id={cancelled_id[:8]} "
                    "ya no existe (race: reciclaje ganó primero)"
                )

            await conn.execute(
                """
                INSERT INTO official_documents (
                    id, document_type_id, reference, content,
                    official_number, year, department_id, numerator_id,
                    signed_at, signers, signer_sector_ids,
                    global_sequence, numbering_regime, reservation_status,
                    reserved_at, reservation_id
                ) VALUES (
                    $1::uuid, $2, $3, $4::jsonb,
                    $5, $6, $7::uuid, $8::uuid,
                    NULL, NULL, ARRAY[]::UUID[],
                    $9, 'GLOBAL', 'RESERVED',
                    $10, $11::uuid
                )
                """,
                new_id,
                tenant["tst_type_id"],
                reference,
                content_js,
                official_number,
                year,
                dept_id,
                SYSTEM_TEST_USER_UUID,
                global_seq,
                now,
                str(uuid4()),
            )

    log.info(
        "tst_creator.claimed",
        extra={
            "official_number": official_number,
            "new_id":          new_id[:8],
            "schema_name":     schema_name,
        },
    )

    try:
        pdf_bytes = await call_pdfcomposer_preview_pdf(
            {
                "document_type_acronym": "TST",
                "document_type_name":    tenant["tst_type_name"],
                "reference":             reference,
                "content":               {"html": "<p>Documento de Prueba</p>"},
            },
            schema_name=schema_name,
        )
        log.info("tst_creator.pdf_generated", extra={"size_bytes": len(pdf_bytes)})


        signed_pdf = await call_notary_sign_pdf(
            pdf_bytes=pdf_bytes,
            signer_name=tenant["signer_name"],
            signer_seal=tenant["signer_seal"],
            signer_department=tenant["dept_acronym"],
            signer_municipality=tenant["municipality"],
            official_number=official_number,
            city=tenant["city"],
            defer_timestamp=True,
        )
        log.info("tst_creator.pdf_signed", extra={"official_number": official_number})

        from services.storage.cloudflare import get_tenant_r2_client
        r2  = await get_tenant_r2_client(schema_name=schema_name)
        from services.storage.pdf_location import (
            target_pdf_location, persist_pdf_location, effective_pdf_location,
        )
        _target_loc = target_pdf_location()
        r2k = f"{official_number}.pdf"
        _upload_res = await run_in_threadpool(r2.upload_oficial, signed_pdf, r2k, _target_loc)
        _effective_loc = effective_pdf_location(_upload_res, _target_loc)
        await persist_pdf_location(
            new_id, _effective_loc, schema_name=schema_name, official_number=official_number
        )
        log.info("tst_creator.r2_uploaded", extra={"r2_key": r2k, "location": _effective_loc})

        try:
            from services.storage.publish_public import maybe_publish_official_pdf
            await maybe_publish_official_pdf(
                schema_name=schema_name,
                official_number=official_number,
                document_id=new_id,
                document_type_id=tenant["tst_type_id"],
                signed_pdf_bytes=signed_pdf,
            )
        except Exception as _pub_err:
            log.warning(
                "tst_creator.publish_public_failed official_number=%s error=%s (soft-fail)",
                official_number, _pub_err,
            )

    except Exception as signing_err:
        log.warning(
            "tst_creator.signing_failed — revirtiendo a CANCELLED official_number=%s: %s",
            official_number, signing_err,
        )
        try:
            await execute(
                """
                UPDATE official_documents
                   SET reservation_status = 'CANCELLED'
                 WHERE id = $1::uuid
                   AND reservation_status = 'RESERVED'
                """,
                new_id,
                schema_name=schema_name,
            )
            log.info("tst_creator.reverted_to_cancelled new_id=%s", new_id[:8])
        except Exception as revert_err:
            log.error(
                "tst_creator.revert_failed (hueco puede quedar RESERVED hasta "
                "que el sweeper_escri lo limpie en %s min): %s",
                15,
                revert_err,
            )
        raise signing_err

    now_signed = datetime.now(timezone.utc)
    signers_js = json.dumps([
        {
            "user_id":        SYSTEM_TEST_USER_UUID,
            "full_name":      tenant["signer_name"],
            "seal":           tenant["signer_seal"],
            "department":     tenant["dept_name"],
            "signed_at":      now_signed.isoformat(),
            "signature_type": "electronic",
        }
    ])
    await execute(
        """
        UPDATE official_documents
           SET reservation_status = 'CONFIRMED',
               signed_at          = $2,
               signers            = $3::jsonb
         WHERE id = $1::uuid
           AND reservation_status = 'RESERVED'
        """,
        new_id,
        now_signed,
        signers_js,
        schema_name=schema_name,
    )

    log.info(
        "tst_creator.confirmed",
        extra={
            "official_number": official_number,
            "new_id":          new_id[:8],
            "schema_name":     schema_name,
            "global_seq":      global_seq,
        },
    )

    try:
        await log_signature_event(
            schema_name=schema_name,
            document_id=new_id,
            user_id=SYSTEM_TEST_USER_UUID,
            signature_method="electronic",
            result="ok",
            official_number=official_number,
            failure_reason=None,
            session_id=f"tst_fill_{global_seq}_{year}",
        )
    except Exception as audit_err:
        log.warning("tst_creator.audit_log_failed (soft-fail): %s", audit_err)

    return official_number
