"""
Certificate Resolver - Descarga .p12 de R2 y desencripta password.
Usado por notary_api.py para enviar certificados a Notary via multipart.

Cache en memoria con TTL 5 minutos para evitar descargas repetidas.
"""

import os
import time
import threading
from typing import Tuple, Optional

import boto3
from botocore.client import Config
from cryptography.fernet import Fernet

from shared.logging import get_logger

logger = get_logger(__name__)

# --- Configuration ---

CERT_MASTER_KEY = os.getenv("CERT_MASTER_KEY", "")
CERT_R2_BUCKET = os.getenv("CERT_R2_BUCKET", "gdi-certificates")

CACHE_TTL_SECONDS = 300  # 5 minutos

# --- Cache ---

_cache: dict = {}  # {tenant_id: (p12_bytes, password, timestamp)}
_cache_lock = threading.Lock()


def _get_r2_client():
    """Crea cliente boto3 para R2."""
    endpoint = os.getenv("CF_R2_ENDPOINT")
    access_key = os.getenv("CF_R2_ACCESS_KEY_ID")
    secret_key = os.getenv("CF_R2_SECRET_ACCESS_KEY")

    if not all([endpoint, access_key, secret_key]):
        raise RuntimeError("Credenciales R2 no configuradas para certificados")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", region_name="auto"),
    )


def _get_from_cache(tenant_id: str) -> Optional[Tuple[bytes, str]]:
    """Intenta obtener del cache si no expiró."""
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
    """Guarda en cache."""
    with _cache_lock:
        _cache[tenant_id] = (p12_bytes, password, time.time())


def invalidate_cache(tenant_id: str = None):
    """Invalida cache para un tenant o todos."""
    with _cache_lock:
        if tenant_id:
            _cache.pop(tenant_id, None)
        else:
            _cache.clear()


async def resolve_certificate(tenant_id: str, *, schema_name: str) -> Tuple[bytes, str]:
    """
    Resuelve certificado para un tenant: descarga .p12 de R2 + desencripta password.

    1. Revisa cache (TTL 5 min)
    2. Consulta public.tenant_certificates en BD
    3. Desencripta password con Fernet (CERT_MASTER_KEY)
    4. Descarga .p12 de R2
    5. Guarda en cache

    Args:
        tenant_id: ID del tenant
        schema_name: Schema para la query (keyword-only)

    Returns:
        Tuple[bytes, str]: (p12_bytes, password_plaintext)

    Raises:
        RuntimeError: Si no se encuentra certificado o hay error
    """
    # 1. Cache hit?
    cached = _get_from_cache(tenant_id)
    if cached:
        logger.info(f"Certificate cache HIT for {tenant_id}")
        return cached

    logger.info(f"Certificate cache MISS for {tenant_id}, fetching from R2...")

    # 2. Query BD
    from database import execute_query

    query = """
        SELECT r2_bucket, r2_key, encrypted_password
        FROM public.tenant_certificates
        WHERE tenant_id = %s AND is_active = true
    """
    result = execute_query(query, (tenant_id,), fetch_one=True, schema_name=schema_name)

    if not result:
        raise RuntimeError(f"No hay certificado activo en BD para tenant '{tenant_id}'")

    r2_bucket = result["r2_bucket"]
    r2_key = result["r2_key"]
    encrypted_password = result["encrypted_password"]

    # 3. Desencriptar password
    if not CERT_MASTER_KEY:
        raise RuntimeError("CERT_MASTER_KEY no configurada")

    try:
        fernet = Fernet(CERT_MASTER_KEY.encode())
        password = fernet.decrypt(encrypted_password.encode("utf-8")).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Error desencriptando password para {tenant_id}: {e}")

    # 4. Descargar .p12 de R2
    try:
        client = _get_r2_client()
        response = client.get_object(Bucket=r2_bucket, Key=r2_key)
        p12_bytes = response["Body"].read()
        logger.info(f"Certificado descargado de R2: {r2_bucket}/{r2_key} ({len(p12_bytes)} bytes)")
    except Exception as e:
        raise RuntimeError(f"Error descargando certificado de R2 para {tenant_id}: {e}")

    # 5. Guardar en cache
    _set_cache(tenant_id, p12_bytes, password)

    return (p12_bytes, password)
