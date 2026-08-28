import json
import os
import uuid as _uuid_module
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from cryptography.fernet import Fernet
from fastapi.concurrency import run_in_threadpool

from shared.logging import get_logger
from database import fetch_one, fetch_all, get_conn

from services.webhooks.tad_hmac import build_webhook_hmac_header

logger = get_logger(__name__)

CERT_MASTER_KEY = os.getenv("CERT_MASTER_KEY", "")

WEBHOOK_HTTP_TIMEOUT_SECONDS = 15.0


_ESQUEMAS_WEBHOOK_PERMITIDOS = {"http", "https"}
_SUFIJOS_HOST_BLOQUEADOS = (".internal", ".local", ".localdomain")


class DestinoWebhookNoPermitido(ValueError):
    pass


def _validar_destino_webhook(url: str) -> None:
    import ipaddress
    import socket
    from urllib.parse import urlparse

    u = urlparse(url)
    if u.scheme not in _ESQUEMAS_WEBHOOK_PERMITIDOS:
        raise DestinoWebhookNoPermitido(f"esquema no permitido: {u.scheme!r}")

    host = u.hostname
    if not host:
        raise DestinoWebhookNoPermitido("la URL no tiene host")

    host_lower = host.lower().rstrip(".")
    if host_lower.endswith(_SUFIJOS_HOST_BLOQUEADOS):
        raise DestinoWebhookNoPermitido(f"host interno: {host_lower!r}")

    try:
        resueltas = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror as e:
        raise DestinoWebhookNoPermitido(f"el host no resuelve: {host_lower!r} ({e})")

    for familia in resueltas:
        ip_txt = familia[4][0]
        try:
            ip = ipaddress.ip_address(ip_txt)
        except ValueError:
            raise DestinoWebhookNoPermitido(f"IP ilegible: {ip_txt!r}")
        if not ip.is_global or ip.is_multicast:
            raise DestinoWebhookNoPermitido(
                f"el host {host_lower!r} resuelve a una IP no publica ({ip_txt})"
            )


WEBHOOK_MAX_ATTEMPTS = int(os.getenv("TAD_WEBHOOK_MAX_ATTEMPTS", "8"))
WEBHOOK_BACKOFF_MINUTES = int(os.getenv("TAD_WEBHOOK_BACKOFF_MINUTES", "5"))


def _decrypt_webhook_secret(encrypted: str) -> str:
    if not CERT_MASTER_KEY:
        raise RuntimeError("CERT_MASTER_KEY no configurada")
    try:
        fernet = Fernet(CERT_MASTER_KEY.encode())
        return fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Error desencriptando webhook_secret: {e}")


async def get_tad_webhook_config(*, schema_name: str) -> Optional[dict]:
    row = await fetch_one(
        """
        SELECT ak.id, ak.webhook_url, ak.webhook_secret
        FROM public.api_keys ak
        JOIN public.municipalities m ON ak.municipality_id = m.id
        WHERE m.schema_name = $1
          AND ak.key_type = 'tad'
          AND ak.is_active = true
          AND ak.webhook_url IS NOT NULL
          AND ak.webhook_secret IS NOT NULL
        ORDER BY ak.created_at ASC
        LIMIT 1
        """,
        schema_name,
        schema_name="public",
    )
    if not row:
        return None

    return {
        "api_key_id": str(row["id"]),
        "webhook_url": row["webhook_url"],
        "webhook_secret": _decrypt_webhook_secret(row["webhook_secret"]),
    }


async def _get_municipality_info(*, schema_name: str) -> dict:
    row = await fetch_one(
        "SELECT name, acronym FROM public.municipalities WHERE schema_name = $1",
        schema_name,
        schema_name="public",
    )
    return {"name": row["name"] if row else None, "acronym": row["acronym"] if row else None}


async def _resolve_documents_for_payload(document_ids: list[str], *, schema_name: str) -> list[dict]:
    from services.storage.cloudflare import get_tenant_r2_client

    if not document_ids:
        return []

    rows = await fetch_all(
        """
        SELECT id, official_number, reference, pdf_location
        FROM official_documents
        WHERE id = ANY($1::uuid[]) AND signed_at IS NOT NULL
        """,
        document_ids,
        schema_name=schema_name,
    )
    if not rows:
        return []

    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    documents = []
    for row in rows:
        official_number = row["official_number"]
        url = await run_in_threadpool(
            r2_client.get_oficial_url, official_number, row.get("pdf_location") or "oficial",
        ) if official_number else None
        documents.append({
            "id": str(row["id"]),
            "official_number": official_number,
            "name": row["reference"],
            "url": url,
        })
    return documents


async def build_documents_notified_payload(
    citizen: dict, case: dict, document_ids: list[str], *, schema_name: str
) -> dict:
    municipality = await _get_municipality_info(schema_name=schema_name)
    documents = await _resolve_documents_for_payload(document_ids, schema_name=schema_name)
    return {
        "event": "documents.notified",
        "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "municipality": municipality,
        "citizen": {
            "id": citizen["id"],
            "country_id": citizen["country_id"],
            "full_name": citizen["full_name"],
        },
        "case": {
            "id": case["id"],
            "number": case["number"],
            "reference": case["reference"],
        },
        "documents": documents,
    }


def build_webhook_test_payload(municipality: dict) -> dict:
    return {
        "event": "webhook.test",
        "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "municipality": municipality,
        "citizen": {
            "id": "00000000-0000-0000-0000-000000000000",
            "country_id": "20000000001",
            "full_name": "Ciudadano de Prueba",
        },
        "case": {
            "id": "00000000-0000-0000-0000-000000000000",
            "number": "EE-2026-000000-TEST-XXXX",
            "reference": "Expediente de prueba (webhook.test)",
        },
        "documents": [
            {
                "id": "00000000-0000-0000-0000-000000000000",
                "official_number": "TEST-2026-00000000-XXXX-TAD",
                "name": "Documento de prueba",
                "url": "https://ejemplo.invalido/documento-de-prueba.pdf",
            }
        ],
        "note": "Webhook de PRUEBA disparado desde POST /api/v1/tad/webhook/test. No corresponde a un tramite real.",
    }


async def send_test_webhook(*, schema_name: str) -> dict:
    config = await get_tad_webhook_config(schema_name=schema_name)
    if not config:
        raise ValueError(
            "No hay una API Key TAD con webhook configurado para este municipio. "
            "Configura la URL de callback y el secret en BackOffice antes de probar."
        )

    municipality = await _get_municipality_info(schema_name=schema_name)
    payload = build_webhook_test_payload(municipality)
    body_bytes = json.dumps(payload).encode("utf-8")

    from urllib.parse import urlparse
    path = urlparse(config["webhook_url"]).path or "/"
    signature = build_webhook_hmac_header(
        config["webhook_secret"], method="POST", path=path, body_bytes=body_bytes,
    )

    result = {
        "delivered": False,
        "webhook_url": config["webhook_url"],
        "status_code": None,
        "signature": signature,
        "event": "webhook.test",
        "error": None,
    }
    try:
        await run_in_threadpool(_validar_destino_webhook, config["webhook_url"])
        async with httpx.AsyncClient(timeout=WEBHOOK_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                config["webhook_url"],
                content=body_bytes,
                headers={"Content-Type": "application/json", "X-GDI-Signature": signature},
            )
        result["status_code"] = response.status_code
        result["delivered"] = 200 <= response.status_code < 300
        if not result["delivered"]:
            result["error"] = f"El servidor respondio {response.status_code}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    logger.info(
        f"[TadWebhook] webhook.test schema={schema_name} delivered={result['delivered']} "
        f"status={result['status_code']} err={result['error']}"
    )
    return result


async def enqueue_tad_webhook(
    *, schema_name: str, api_key_id: str, event_type: str, payload: dict
) -> str:
    job_id = str(_uuid_module.uuid4())
    async with get_conn(schema_name="public") as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO public.tad_webhook_jobs
                    (id, schema_name, api_key_id, event_type, payload, status)
                VALUES ($1::uuid, $2, $3::uuid, $4, $5::jsonb, 'pending')
                """,
                job_id, schema_name, api_key_id, event_type, json.dumps(payload),
            )
            await conn.execute("SELECT pg_notify('tad_webhook', $1)", schema_name)
    logger.info(f"[TadWebhook] job encolado: id={job_id[:8]}... schema={schema_name} event={event_type}")
    return job_id


async def send_tad_webhook_job(job: dict) -> None:
    job_id = str(job["id"])
    schema_name = job["schema_name"]
    api_key_id = str(job["api_key_id"])
    payload = job["payload"] if isinstance(job["payload"], dict) else json.loads(job["payload"])
    attempts = int(job.get("attempts", 0)) + 1

    try:
        config = await get_tad_webhook_config(schema_name=schema_name)
        if not config or config["api_key_id"] != api_key_id:
            raise RuntimeError(
                f"No hay webhook_url/secret configurado para api_key_id={api_key_id} en schema={schema_name}"
            )

        payload = {
            **payload,
            "event": job["event_type"],
            "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        }
        if payload.get("documents"):
            document_ids = [d["id"] for d in payload["documents"]]
            payload["documents"] = await _resolve_documents_for_payload(
                document_ids, schema_name=schema_name
            )

        body_bytes = json.dumps(payload).encode("utf-8")
        from urllib.parse import urlparse
        path = urlparse(config["webhook_url"]).path or "/"
        signature = build_webhook_hmac_header(
            config["webhook_secret"], method="POST", path=path, body_bytes=body_bytes,
        )

        await run_in_threadpool(_validar_destino_webhook, config["webhook_url"])
        async with httpx.AsyncClient(timeout=WEBHOOK_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                config["webhook_url"],
                content=body_bytes,
                headers={"Content-Type": "application/json", "X-GDI-Signature": signature},
            )
            response.raise_for_status()

        await _mark_job_sent(job_id)
        logger.info(f"[TadWebhook] job enviado OK: id={job_id[:8]}... status={response.status_code}")

    except Exception as e:
        logger.warning(f"[TadWebhook] job {job_id[:8]}... fallo (intento {attempts}): {e}")
        await _requeue_or_fail(job_id, attempts, str(e)[:500])


async def _mark_job_sent(job_id: str) -> None:
    from database import execute
    await execute(
        "UPDATE public.tad_webhook_jobs SET status = 'sent', updated_at = NOW() WHERE id = $1",
        job_id,
        schema_name="public",
    )


async def _requeue_or_fail(job_id: str, attempts: int, error: str) -> None:
    from database import execute

    if attempts >= WEBHOOK_MAX_ATTEMPTS:
        await execute(
            """
            UPDATE public.tad_webhook_jobs
            SET status = 'failed', attempts = $1, last_error = $2, updated_at = NOW()
            WHERE id = $3
            """,
            attempts, error, job_id,
            schema_name="public",
        )
        logger.error(f"[TadWebhook] job {job_id[:8]}... agoto los {WEBHOOK_MAX_ATTEMPTS} intentos, marcado 'failed'")
        return

    backoff_minutes = attempts * WEBHOOK_BACKOFF_MINUTES
    await execute(
        f"""
        UPDATE public.tad_webhook_jobs
        SET status = 'pending', attempts = $1, last_error = $2,
            available_at = NOW() + '{backoff_minutes} minutes'::interval,
            updated_at = NOW()
        WHERE id = $3
        """,
        attempts, error, job_id,
        schema_name="public",
    )
    logger.info(f"[TadWebhook] job {job_id[:8]}... reencolado, intento {attempts}, backoff={backoff_minutes}min")
