from shared.logging import get_logger
from botocore.exceptions import ClientError
from fastapi.concurrency import run_in_threadpool

log = get_logger(__name__)


class R2KeyNotFound(Exception):
    pass


async def _get_boto_client_and_bucket(schema_name: str, bucket: str):
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
    elif bucket == "preoficial":
        bucket_name = r2.bucket_preoficial
        if not bucket_name:
            raise ValueError(f"Tenant '{schema_name}' no tiene bucket_preoficial configurado")
    elif bucket == "publico":
        bucket_name = r2.bucket_publico
        if not bucket_name:
            raise ValueError(f"Tenant '{schema_name}' no tiene bucket_publico configurado")
    else:
        raise ValueError(
            f"bucket debe ser 'tosign', 'oficial', 'preoficial' o 'publico', recibido: {bucket!r}"
        )

    return r2._client, bucket_name


async def r2_copy(*, schema_name: str, src: str, dst: str, src_bucket: str = "tosign", dst_bucket: str = "tosign") -> None:
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
    client, bucket_name = await _get_boto_client_and_bucket(schema_name, bucket)
    await run_in_threadpool(client.delete_object, Bucket=bucket_name, Key=key)
    log.debug("r2_delete OK: %s/%s", bucket_name, key)


async def r2_put(*, schema_name: str, key: str, body: bytes, content_type: str = "application/pdf", bucket: str = "tosign") -> None:
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


async def r2_list(
    *,
    schema_name: str,
    bucket: str = "oficial",
    prefix: str = "",
    max_keys: int | None = None,
) -> tuple[list[dict], bool]:
    client, bucket_name = await _get_boto_client_and_bucket(schema_name, bucket)

    objetos: list[dict] = []
    token: str | None = None
    truncado = False

    while True:
        kwargs = {"Bucket": bucket_name}
        if prefix:
            kwargs["Prefix"] = prefix
        if token:
            kwargs["ContinuationToken"] = token

        page = await run_in_threadpool(client.list_objects_v2, **kwargs)

        for obj in page.get("Contents", []):
            objetos.append({
                "key": obj["Key"],
                "size": obj.get("Size", 0),
                "last_modified": obj.get("LastModified"),
            })
            if max_keys is not None and len(objetos) >= max_keys:
                truncado = page.get("IsTruncated", False) or True
                log.warning(
                    "r2_list truncado en %d objetos: %s/%s", max_keys, bucket_name, prefix,
                )
                return objetos, truncado

        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
        if not token:
            break

    log.debug("r2_list %s/%s → %d objetos", bucket_name, prefix, len(objetos))
    return objetos, truncado


async def r2_signed_url(*, schema_name: str, key: str, ttl: int = 60, bucket: str = "tosign") -> str:
    client, bucket_name = await _get_boto_client_and_bucket(schema_name, bucket)
    url = await run_in_threadpool(
        client.generate_presigned_url,
        "get_object",
        Params={"Bucket": bucket_name, "Key": key},
        ExpiresIn=ttl,
    )
    log.debug("r2_signed_url OK: %s/%s (ttl=%ds)", bucket_name, key, ttl)
    return url
