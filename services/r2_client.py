"""
Helpers de bajo nivel para operaciones R2 (copy/delete/put/get/signed_url).

Wrappea el CloudflareR2Client multi-tenant existente (services/storage/cloudflare.py)
para exponer operaciones con paths de objeto explícitos.

Todos los métodos usan schema_name como keyword-only para garantizar
el patrón multi-tenant del proyecto.
"""
import logging
from botocore.exceptions import ClientError
from fastapi.concurrency import run_in_threadpool

log = logging.getLogger(__name__)


class R2KeyNotFound(Exception):
    """Objeto no encontrado en R2 (NoSuchKey / 404)."""
    pass


async def _get_boto_client_and_bucket(schema_name: str, bucket: str):
    """
    Devuelve el cliente boto3 interno del CloudflareR2Client para el tenant dado.

    Args:
        schema_name: Schema del tenant.
        bucket: 'tosign' o 'oficial'.

    Returns:
        tuple(boto3_client, bucket_name)
    """
    from services.storage.cloudflare import get_tenant_r2_client

    r2 = await get_tenant_r2_client(schema_name=schema_name)

    if r2._client is None:
        raise RuntimeError(
            f"Cliente R2 no inicializado para tenant '{schema_name}'. "
            "Verificar CF_R2_ENDPOINT, CF_R2_ACCESS_KEY_ID, CF_R2_SECRET_ACCESS_KEY."
        )

    if bucket == "tosign":
        bucket_name = r2.bucket_tosign
    elif bucket == "oficial":
        bucket_name = r2.bucket_oficial
    else:
        raise ValueError(f"bucket debe ser 'tosign' u 'oficial', recibido: {bucket!r}")

    return r2._client, bucket_name


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

async def r2_copy(*, schema_name: str, src: str, dst: str, src_bucket: str = "tosign", dst_bucket: str = "tosign") -> None:
    """
    Copia un objeto dentro de R2 (server-side copy).

    Args:
        schema_name: Schema del tenant (keyword-only).
        src: Key origen.
        dst: Key destino.
        src_bucket: Bucket origen ('tosign' u 'oficial'). Default 'tosign'.
        dst_bucket: Bucket destino ('tosign' u 'oficial'). Default 'tosign'.

    Raises:
        R2KeyNotFound: Si src no existe.
        RuntimeError: Si el cliente no está inicializado.
    """
    client, dst_bucket_name = await _get_boto_client_and_bucket(schema_name, dst_bucket)
    _, src_bucket_name = await _get_boto_client_and_bucket(schema_name, src_bucket)

    try:
        await run_in_threadpool(
            client.copy_object,
            Bucket=dst_bucket_name,
            CopySource={"Bucket": src_bucket_name, "Key": src},
            Key=dst,
        )
        log.debug("r2_copy OK: %s/%s → %s/%s", src_bucket_name, src, dst_bucket_name, dst)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            raise R2KeyNotFound(src) from e
        raise


async def r2_delete(*, schema_name: str, key: str, bucket: str = "tosign") -> None:
    """
    Elimina un objeto de R2.

    Args:
        schema_name: Schema del tenant (keyword-only).
        key: Key del objeto a eliminar.
        bucket: 'tosign' u 'oficial'. Default 'tosign'.

    Note:
        R2 delete_object es idempotente: no lanza error si la key no existe.
    """
    client, bucket_name = await _get_boto_client_and_bucket(schema_name, bucket)
    await run_in_threadpool(client.delete_object, Bucket=bucket_name, Key=key)
    log.debug("r2_delete OK: %s/%s", bucket_name, key)


async def r2_put(*, schema_name: str, key: str, body: bytes, content_type: str = "application/pdf", bucket: str = "tosign") -> None:
    """
    Sube bytes a R2.

    Args:
        schema_name: Schema del tenant (keyword-only).
        key: Key destino.
        body: Contenido binario.
        content_type: MIME type. Default 'application/pdf'.
        bucket: 'tosign' u 'oficial'. Default 'tosign'.
    """
    client, bucket_name = await _get_boto_client_and_bucket(schema_name, bucket)
    await run_in_threadpool(
        client.put_object,
        Bucket=bucket_name,
        Key=key,
        Body=body,
        ContentType=content_type,
    )
    log.debug("r2_put OK: %s/%s (%d bytes)", bucket_name, key, len(body))


async def r2_get_object(*, schema_name: str, key: str, bucket: str = "tosign") -> bytes:
    """
    Descarga un objeto de R2 y retorna sus bytes.

    Args:
        schema_name: Schema del tenant (keyword-only).
        key: Key del objeto.
        bucket: 'tosign' u 'oficial'. Default 'tosign'.

    Returns:
        bytes del objeto.

    Raises:
        R2KeyNotFound: Si el objeto no existe.
    """
    client, bucket_name = await _get_boto_client_and_bucket(schema_name, bucket)
    try:
        r = await run_in_threadpool(client.get_object, Bucket=bucket_name, Key=key)
        data = r["Body"].read()
        log.debug("r2_get_object OK: %s/%s (%d bytes)", bucket_name, key, len(data))
        return data
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchKey", "404"):
            raise R2KeyNotFound(key) from e
        raise


async def r2_head(*, schema_name: str, key: str, bucket: str = "tosign") -> dict:
    """
    Verifica si un objeto existe en R2 (HEAD request).

    Args:
        schema_name: Schema del tenant (keyword-only).
        key: Key del objeto.
        bucket: 'tosign' u 'oficial'. Default 'tosign'.

    Returns:
        Dict con metadata del objeto (ETag, ContentLength, etc.).

    Raises:
        R2KeyNotFound: Si el objeto no existe.
    """
    client, bucket_name = await _get_boto_client_and_bucket(schema_name, bucket)
    try:
        response = await run_in_threadpool(client.head_object, Bucket=bucket_name, Key=key)
        log.debug("r2_head OK: %s/%s", bucket_name, key)
        return response
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchKey", "404", "NoSuchBucket", "NotFound"):
            raise R2KeyNotFound(key) from e
        raise


async def r2_signed_url(*, schema_name: str, key: str, ttl: int = 300, bucket: str = "tosign") -> str:
    """
    Genera una URL pre-firmada para acceso temporal a un objeto.

    Args:
        schema_name: Schema del tenant (keyword-only).
        key: Key del objeto.
        ttl: Segundos de vigencia. Default 300.
        bucket: 'tosign' u 'oficial'. Default 'tosign'.

    Returns:
        URL pre-firmada como string.
    """
    client, bucket_name = await _get_boto_client_and_bucket(schema_name, bucket)
    url = await run_in_threadpool(
        client.generate_presigned_url,
        "get_object",
        Params={"Bucket": bucket_name, "Key": key},
        ExpiresIn=ttl,
    )
    log.debug("r2_signed_url OK: %s/%s (ttl=%ds)", bucket_name, key, ttl)
    return url
