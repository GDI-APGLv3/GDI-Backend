
import os
import time
import threading
from typing import Tuple, Optional

import boto3
from botocore.client import Config
from cryptography.fernet import Fernet

from shared.logging import get_logger

logger = get_logger(__name__)


CERT_MASTER_KEY = os.getenv("CERT_MASTER_KEY", "")
CERT_R2_BUCKET = os.getenv("CERT_R2_BUCKET", "gdi-certificates")

CACHE_TTL_SECONDS = 300


_cache: dict = {}
_cache_lock = threading.Lock()


def _get_r2_client():
    endpoint = os.getenv("CF_R2_ENDPOINT")
    access_key = os.getenv("CF_R2_ACCESS_KEY_ID")
    secret_key = os.getenv("CF_R2_SECRET_ACCESS_KEY")

    if not all([endpoint, access_key, secret_key]):
        raise RuntimeError("Credenciales R2 no configuradas para certificados")

    config_kwargs = dict(signature_version="s3v4", region_name="auto")
    force_path = os.getenv("S3_FORCE_PATH_STYLE", "").lower() in ("1", "true", "yes")
    if force_path or "minio" in (endpoint or "").lower():
        config_kwargs["s3"] = {"addressing_style": "path"}

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(**config_kwargs),
    )


def _get_from_cache(tenant_id: str) -> Optional[Tuple[bytes, str]]:
    with _cache_lock:
        entry = _cache.get(tenant_id)
        if entry is None:
            return None
        p12_bytes, password, ts = entry
        if time.time() - ts > CACHE_TTL_SECONDS:
            del _cache[tenant_id]
            return None
        return (p12_bytes, password)


def _set_cache(tenant_id: str, p12_bytes: bytes, password: str):
    with _cache_lock:
        _cache[tenant_id] = (p12_bytes, password, time.time())


def invalidate_cache(tenant_id: str = None):
    with _cache_lock:
        if tenant_id:
            _cache.pop(tenant_id, None)
        else:
            _cache.clear()


async def resolve_certificate(tenant_id: str, *, schema_name: str) -> Tuple[bytes, str]:
    cached = _get_from_cache(tenant_id)
    if cached:
        logger.info(f"Certificate cache HIT for {tenant_id}")
        return cached

    logger.info(f"Certificate cache MISS for {tenant_id}, fetching from R2...")

    from database import fetch_one

    query = """
        SELECT r2_bucket, r2_key, encrypted_password
        FROM public.tenant_certificates
        WHERE tenant_id = $1 AND is_active = true
    """
    result = await fetch_one(query, tenant_id, schema_name=schema_name)

    if not result:
        raise RuntimeError(f"No hay certificado activo en BD para tenant '{tenant_id}'")

    r2_bucket = result["r2_bucket"]
    r2_key = result["r2_key"]
    encrypted_password = result["encrypted_password"]

    if not CERT_MASTER_KEY:
        raise RuntimeError("CERT_MASTER_KEY no configurada")

    try:
        fernet = Fernet(CERT_MASTER_KEY.encode())
        password = fernet.decrypt(encrypted_password.encode("utf-8")).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Error desencriptando password para {tenant_id}: {e}")

    try:
        client = _get_r2_client()
        response = client.get_object(Bucket=r2_bucket, Key=r2_key)
        p12_bytes = response["Body"].read()
        logger.info(f"Certificado descargado de R2: {r2_bucket}/{r2_key} ({len(p12_bytes)} bytes)")
    except Exception as e:
        raise RuntimeError(f"Error descargando certificado de R2 para {tenant_id}: {e}")

    _set_cache(tenant_id, p12_bytes, password)

    return (p12_bytes, password)
