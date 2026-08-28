
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError, NoCredentialsError
from typing import Optional, Dict, Tuple
from threading import Lock
import time
import os
from shared.logging import get_logger

logger = get_logger(__name__)


class CloudflareR2Client:

    def __init__(self, bucket_oficial: str = None, bucket_tosign: str = None, bucket_edicion: str = None, bucket_publico: str = None, bucket_preoficial: str = None):
        self.endpoint_url = os.getenv('CF_R2_ENDPOINT')
        self.access_key_id = os.getenv('CF_R2_ACCESS_KEY_ID')
        self.secret_access_key = os.getenv('CF_R2_SECRET_ACCESS_KEY')
        self.bucket_oficial = bucket_oficial or os.getenv('CF_R2_BUCKET_OFICIAL', 'tenant-test-oficial')
        self.bucket_tosign = bucket_tosign or os.getenv('CF_R2_BUCKET_TOSIGN', 'tenant-test-tosign')
        self.bucket_edicion = bucket_edicion or os.getenv('CF_R2_BUCKET_EDICION')
        self.bucket_publico = bucket_publico
        self.bucket_preoficial = bucket_preoficial
        self.url_expiration = int(os.getenv('CF_R2_SIGN_EXPIRATION', '60'))

        if not all([self.endpoint_url, self.access_key_id, self.secret_access_key]):
            logger.warning("Credenciales de Cloudflare R2 no configuradas completamente")
            logger.warning(f"Endpoint: {'OK' if self.endpoint_url else 'FALTA'}")
            logger.warning(f"Access Key: {'OK' if self.access_key_id else 'FALTA'}")
            logger.warning(f"Secret Key: {'OK' if self.secret_access_key else 'FALTA'}")
            self._client = None
            return

        try:
            config_kwargs = dict(signature_version='s3v4', region_name='auto')
            force_path = os.getenv('S3_FORCE_PATH_STYLE', '').lower() in ('1', 'true', 'yes')
            if force_path or 'minio' in (self.endpoint_url or '').lower():
                config_kwargs['s3'] = {'addressing_style': 'path'}
            self._client = boto3.client(
                's3',
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                config=Config(**config_kwargs)
            )
            logger.info("Cliente inicializado correctamente")
            logger.info(f"Endpoint: {self.endpoint_url}")
            logger.info(f"Bucket oficial: {self.bucket_oficial}")
            logger.info(f"Bucket tosign: {self.bucket_tosign}")
            logger.info(f"Expiración URLs: {self.url_expiration}s")
        except Exception as e:
            logger.error(f"ERROR inicializando cliente: {e}")
            self._client = None


    def resolve_pdf_bucket(self, location: str = "oficial", *, for_write: bool = False) -> tuple[str, str]:
        if location == "preoficial":
            if self.bucket_preoficial:
                return self.bucket_preoficial, "preoficial"
            if for_write:
                from shared.exceptions import PreOficialNotProvisionedError
                logger.error(
                    "GDI-270: hay que escribir un PDF B-B pero el tenant no tiene "
                    "bucket preOficial provisionado. NO se degrada al bucket "
                    "oficial (%s): tiene object-lock WORM y el PDF quedaría "
                    "irreversiblemente sin poder sellarse. Correr "
                    "scripts/backfill_bucket_preoficial.py para este tenant.",
                    self.bucket_oficial,
                )
                raise PreOficialNotProvisionedError(
                    "El bucket preOficial no está provisionado para este municipio. "
                    "La firma no se completa a propósito: escribir en el bucket "
                    "oficial dejaría el documento sin poder sellarse nunca.",
                    error_code="preoficial_not_provisioned",
                )
            logger.warning(
                "GDI-270: lectura pedida contra preoficial pero el tenant no lo "
                "tiene configurado — se lee de oficial (%s). Tenant sin "
                "bucket_preoficial: correr scripts/backfill_bucket_preoficial.py.",
                self.bucket_oficial,
            )
            return self.bucket_oficial, "oficial"
        if location != "oficial":
            raise ValueError(
                f"location debe ser 'oficial' o 'preoficial', recibido: {location!r}"
            )
        return self.bucket_oficial, "oficial"

    def get_oficial_url(self, official_number: str, location: str = "oficial") -> Optional[str]:
        if not self._client:
            logger.info("Cliente no inicializado, no se puede obtener URL oficial")
            return None

        filename = official_number
        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        bucket, _loc = self.resolve_pdf_bucket(location)

        try:
            logger.info("Generando URL firmada para documento oficial")
            logger.info(f"Bucket: {bucket}")
            logger.info(f"Key: {filename}")

            url = self._client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket,
                    'Key': filename
                },
                ExpiresIn=self.url_expiration
            )

            logger.info(f"URL oficial generada exitosamente (expira en {self.url_expiration}s)")
            return url

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"Error de cliente AWS: {error_code}")
            logger.error(f"Mensaje: {error_message}")
            return None
        except NoCredentialsError:
            logger.error("Error: Credenciales no válidas o no encontradas")
            return None
        except Exception as e:
            logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
            return None

    def get_oficial_bytes(
        self, official_number: str, location: Optional[str] = None
    ) -> Optional[bytes]:
        location_explicita = location is not None
        if not self._client:
            logger.info("Cliente no inicializado, no se pueden obtener bytes oficiales")
            return None

        filename = official_number
        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        bucket, _loc = self.resolve_pdf_bucket(location or "oficial")

        try:
            logger.info("Descargando bytes de documento oficial")
            logger.info(f"Bucket: {bucket}")
            logger.info(f"Key: {filename}")

            response = self._client.get_object(
                Bucket=bucket,
                Key=filename
            )
            pdf_bytes = response['Body'].read()

            logger.info(f"Bytes oficiales descargados exitosamente ({len(pdf_bytes)} bytes)")
            return pdf_bytes

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            if error_code in ('404', 'NoSuchKey', 'NoSuchBucket'):
                _other = self.bucket_preoficial if bucket == self.bucket_oficial else self.bucket_oficial
                if _other and _other != bucket:
                    try:
                        response = self._client.get_object(Bucket=_other, Key=filename)
                        pdf_bytes = response['Body'].read()
                        if location_explicita:
                            logger.warning(
                                "GDI-270 fallback: %s no estaba en %s pero SÍ en %s "
                                "(%d bytes) — pdf_location desincronizada en BD, "
                                "revisar conciliador",
                                filename, bucket, _other, len(pdf_bytes),
                            )
                        else:
                            logger.info(
                                "GDI-270: %s encontrado en %s (%d bytes) — el caller "
                                "no indicó pdf_location",
                                filename, _other, len(pdf_bytes),
                            )
                        return pdf_bytes
                    except ClientError:
                        pass
            logger.error(f"Error de cliente AWS descargando oficial: {error_code}")
            logger.error(f"Mensaje: {error_message}")
            return None
        except NoCredentialsError:
            logger.error("Error: Credenciales no validas o no encontradas")
            return None
        except Exception as e:
            logger.error(f"Error inesperado descargando bytes oficiales: {type(e).__name__} - {str(e)}")
            return None

    def get_tosign_url(self, document_filename: str) -> Optional[str]:
        if not self._client:
            logger.info("Cliente no inicializado, no se puede obtener URL tosign")
            return None

        try:
            logger.info("Generando URL firmada para documento en firma")
            logger.info(f"Bucket: {self.bucket_tosign}")
            logger.info(f"Key: {document_filename}")

            url = self._client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_tosign,
                    'Key': document_filename
                },
                ExpiresIn=self.url_expiration
            )

            logger.info(f"URL tosign generada exitosamente (expira en {self.url_expiration}s)")
            return url

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"Error de cliente AWS: {error_code}")
            logger.error(f"Mensaje: {error_message}")
            return None
        except NoCredentialsError:
            logger.error("Error: Credenciales no válidas o no encontradas")
            return None
        except Exception as e:
            logger.error(f"Error inesperado generando URL tosign: {type(e).__name__} - {str(e)}")
            return None

    def exists_tosign(self, filename: str) -> bool:
        if not self._client:
            logger.info("Cliente no inicializado, no se puede verificar existencia")
            return False

        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        try:
            self._client.head_object(
                Bucket=self.bucket_tosign,
                Key=filename
            )
            logger.info(f"Archivo existe en tosign: {filename}")
            return True

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code in ('404', 'NoSuchKey'):
                logger.info(f"Archivo no existe en tosign: {filename}")
                return False
            logger.warning(f"Error verificando existencia: {error_code}")
            return False

        except Exception as e:
            logger.warning(f"Error inesperado verificando existencia: {type(e).__name__}")
            return False

    def upload_tosign(self, pdf_bytes: bytes, filename: str) -> dict:
        from datetime import datetime

        if not self._client:
            logger.error("Cliente no inicializado")
            raise Exception("Cliente R2 no inicializado")

        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        if not pdf_bytes or len(pdf_bytes) == 0:
            logger.error("PDF bytes no puede estar vacío")
            raise ValueError("PDF bytes no puede estar vacío")

        try:
            logger.info("Subiendo PDF a bucket tosign")
            logger.info(f"Bucket: {self.bucket_tosign}")
            logger.info(f"Filename: {filename}")
            logger.info(f"Size: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

            self._client.put_object(
                Bucket=self.bucket_tosign,
                Key=filename,
                Body=pdf_bytes,
                ContentType='application/pdf'
            )

            logger.info(f"Upload exitoso en {self.bucket_tosign}/{filename}")

            return {
                "status": "success",
                "location": "tosign",
                "filename": filename,
                "uploaded_at": datetime.utcnow().isoformat() + "Z",
                "size_bytes": len(pdf_bytes)
            }

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"ClientError: {error_code}")
            logger.error(f"Message: {error_message}")
            raise Exception(f"Error subiendo PDF a R2: {error_code} - {error_message}")
        except Exception as e:
            logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
            raise

    def delete_tosign(self, filename: str) -> dict:
        from datetime import datetime

        if not self._client:
            logger.error("Cliente no inicializado")
            raise Exception("Cliente R2 no inicializado")

        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        try:
            logger.info("Eliminando PDF de bucket tosign")
            logger.info(f"Bucket: {self.bucket_tosign}")
            logger.info(f"Filename: {filename}")

            self._client.delete_object(
                Bucket=self.bucket_tosign,
                Key=filename
            )

            logger.info("PDF eliminado exitosamente")

            return {
                "status": "success",
                "location": "tosign",
                "filename": filename,
                "deleted_at": datetime.utcnow().isoformat() + "Z"
            }

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')

            if error_code == 'NoSuchKey':
                logger.warning("Archivo no encontrado (ya eliminado previamente)")
                return {
                    "status": "success",
                    "location": "tosign",
                    "filename": filename,
                    "deleted_at": datetime.utcnow().isoformat() + "Z",
                    "note": "File did not exist (idempotent operation)"
                }

            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"ClientError: {error_code}")
            logger.error(f"Message: {error_message}")
            raise Exception(f"Error eliminando PDF de R2: {error_code} - {error_message}")

        except Exception as e:
            logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
            raise

    def upload_oficial(self, pdf_bytes: bytes, filename: str, location: str = "oficial") -> dict:
        from datetime import datetime

        if not self._client:
            logger.error("Cliente no inicializado")
            raise Exception("Cliente R2 no inicializado")

        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        if not pdf_bytes or len(pdf_bytes) == 0:
            logger.error("PDF bytes no puede estar vacío")
            raise ValueError("PDF bytes no puede estar vacío")

        bucket, effective_location = self.resolve_pdf_bucket(location, for_write=True)

        try:
            logger.info(f"Subiendo PDF a bucket {effective_location}")
            logger.info(f"Bucket: {bucket}")
            logger.info(f"Filename: {filename}")
            logger.info(f"Size: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

            self._client.put_object(
                Bucket=bucket,
                Key=filename,
                Body=pdf_bytes,
                ContentType='application/pdf'
            )

            logger.info(f"Upload exitoso en {bucket}/{filename}")

            return {
                "status": "success",
                "location": effective_location,
                "filename": filename,
                "uploaded_at": datetime.utcnow().isoformat() + "Z",
                "size_bytes": len(pdf_bytes)
            }

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"ClientError: {error_code}")
            logger.error(f"Message: {error_message}")
            if error_code == 'ObjectLockedByBucketPolicy':
                from shared.exceptions import R2ObjectLockedError
                raise R2ObjectLockedError(
                    f"Error subiendo PDF firmado a R2 oficial: {error_code} - {error_message}",
                    error_code=error_code,
                ) from e
            raise Exception(f"Error subiendo PDF firmado a R2 oficial: {error_code} - {error_message}")
        except Exception as e:
            logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
            raise

    def exists_oficial(self, filename: str, location: str = "oficial") -> bool:
        if not self._client:
            raise Exception("Cliente R2 no inicializado")

        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        if location == "any":
            if self.exists_oficial(filename, location="oficial"):
                return True
            if not self.bucket_preoficial:
                return False
            return self.exists_oficial(filename, location="preoficial")

        bucket, effective_location = self.resolve_pdf_bucket(location)

        try:
            self._client.head_object(
                Bucket=bucket,
                Key=filename
            )
            logger.info(f"Archivo existe en {effective_location}: {filename}")
            return True

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code in ('404', 'NoSuchKey'):
                logger.info(f"Archivo no existe en {effective_location}: {filename}")
                return False
            logger.warning(f"Error verificando existencia en oficial: {error_code}")
            raise

    def upload_publico(self, pdf_bytes: bytes, filename: str) -> dict:
        from datetime import datetime

        if not self.bucket_publico:
            logger.debug("Tenant sin bucket_publico configurado, upload_publico es no-op")
            return {"status": "skipped", "reason": "bucket_publico no configurado"}

        if not self._client:
            logger.error("Cliente no inicializado")
            raise Exception("Cliente R2 no inicializado")

        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        if not pdf_bytes or len(pdf_bytes) == 0:
            logger.error("PDF bytes no puede estar vacío")
            raise ValueError("PDF bytes no puede estar vacío")

        try:
            logger.info("Subiendo PDF a bucket público")
            logger.info(f"Bucket: {self.bucket_publico}")
            logger.info(f"Filename: {filename}")

            self._client.put_object(
                Bucket=self.bucket_publico,
                Key=filename,
                Body=pdf_bytes,
                ContentType='application/pdf'
            )

            logger.info(f"Upload exitoso en {self.bucket_publico}/{filename}")

            return {
                "status": "success",
                "location": "publico",
                "filename": filename,
                "uploaded_at": datetime.utcnow().isoformat() + "Z",
                "size_bytes": len(pdf_bytes)
            }

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"ClientError: {error_code}")
            logger.error(f"Message: {error_message}")
            raise Exception(f"Error subiendo PDF a R2 publico: {error_code} - {error_message}")
        except Exception as e:
            logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
            raise

    def upload_document_image(self, image_bytes: bytes, r2_key: str, content_type: str) -> dict:
        if not self._client:
            logger.error("Cliente no inicializado")
            raise Exception("Cliente R2 no inicializado")

        if not self.bucket_edicion:
            raise Exception("Bucket de edicion (bucket_edicion) no configurado para este tenant")

        if not image_bytes:
            raise ValueError("image_bytes no puede estar vacío")

        try:
            logger.info(f"Subiendo imagen de documento a bucket edicion: {r2_key}")
            self._client.put_object(
                Bucket=self.bucket_edicion,
                Key=r2_key,
                Body=image_bytes,
                ContentType=content_type,
            )
            logger.info(f"Upload exitoso en {self.bucket_edicion}/{r2_key}")
            return {"status": "success", "bucket": self.bucket_edicion, "r2_key": r2_key, "size_bytes": len(image_bytes)}
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"ClientError subiendo imagen: {error_code} - {error_message}")
            raise Exception(f"Error subiendo imagen a R2: {error_code} - {error_message}")

    def get_document_image_bytes(self, r2_key: str) -> Optional[bytes]:
        if not self._client or not self.bucket_edicion:
            return None
        try:
            response = self._client.get_object(Bucket=self.bucket_edicion, Key=r2_key)
            return response['Body'].read()
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(f"ClientError descargando imagen {r2_key}: {error_code}")
            return None
        except Exception as e:
            logger.error(f"Error inesperado descargando imagen {r2_key}: {type(e).__name__} - {e}")
            return None

    def delete_document_image(self, r2_key: str) -> None:
        if not self._client or not self.bucket_edicion:
            return
        try:
            self._client.delete_object(Bucket=self.bucket_edicion, Key=r2_key)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code in ('NoSuchKey', '404'):
                return
            logger.warning(f"ClientError eliminando imagen {r2_key}: {error_code}")
        except Exception as e:
            logger.warning(f"Error inesperado eliminando imagen {r2_key}: {type(e).__name__} - {e}")


_r2_client: Optional[CloudflareR2Client] = None


def get_r2_client() -> CloudflareR2Client:
    global _r2_client
    if _r2_client is None:
        logger.info("Creando nueva instancia singleton del cliente R2")
        _r2_client = CloudflareR2Client()
    return _r2_client


_tenant_clients: Dict[str, Tuple[CloudflareR2Client, float]] = {}
_cache_lock = Lock()
CACHE_TTL_SECONDS = 3600


async def get_tenant_settings(schema_name: str) -> Dict[str, str]:
    from database import fetch_one

    query = "SELECT bucket_oficial, bucket_tosign, bucket_edicion, bucket_publico, bucket_preoficial FROM settings LIMIT 1"
    result = await fetch_one(query, schema_name=schema_name)

    if not result or not result.get('bucket_oficial'):
        raise ValueError(f"Tenant {schema_name} no tiene buckets configurados en settings")

    return {
        'bucket_oficial': result['bucket_oficial'],
        'bucket_tosign': result['bucket_tosign'],
        'bucket_edicion': result.get('bucket_edicion'),
        'bucket_publico': result.get('bucket_publico'),
        'bucket_preoficial': result.get('bucket_preoficial'),
    }


async def get_tenant_r2_client(*, schema_name: str) -> CloudflareR2Client:
    now = time.time()

    with _cache_lock:
        if schema_name in _tenant_clients:
            client, created_at = _tenant_clients[schema_name]
            if now - created_at < CACHE_TTL_SECONDS:
                logger.info(f"Usando cliente cacheado para tenant {schema_name}")
                return client

    logger.info(f"Creando nuevo cliente R2 para tenant {schema_name}")
    settings = await get_tenant_settings(schema_name)

    new_client = CloudflareR2Client(
        bucket_oficial=settings['bucket_oficial'],
        bucket_tosign=settings['bucket_tosign'],
        bucket_edicion=settings.get('bucket_edicion'),
        bucket_publico=settings.get('bucket_publico'),
        bucket_preoficial=settings.get('bucket_preoficial')
    )

    with _cache_lock:
        _tenant_clients[schema_name] = (new_client, now)

    logger.info(f"Cliente creado - Buckets: {settings['bucket_oficial']}, {settings['bucket_tosign']}")
    return new_client


_avatar_client = None


def _get_avatar_boto_client():
    global _avatar_client
    if _avatar_client is not None:
        return _avatar_client

    endpoint_url = os.getenv('CF_R2_ENDPOINT')
    access_key_id = os.getenv('CF_R2_ACCESS_KEY_ID')
    secret_access_key = os.getenv('CF_R2_SECRET_ACCESS_KEY')

    if not all([endpoint_url, access_key_id, secret_access_key]):
        logger.warning("Credenciales de Cloudflare R2 no configuradas — avatares deshabilitados")
        return None

    _avatar_client = boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(signature_version='s3v4', region_name='auto')
    )
    return _avatar_client


def upload_avatar(image_bytes: bytes, r2_key: str) -> str:
    bucket = os.getenv('CF_R2_BUCKET_AVATARS')
    public_base = os.getenv('CF_R2_AVATARS_PUBLIC_URL')
    client = _get_avatar_boto_client()

    if not client or not bucket or not public_base:
        raise Exception("Bucket de avatares no configurado (CF_R2_BUCKET_AVATARS / CF_R2_AVATARS_PUBLIC_URL)")

    if not image_bytes:
        raise ValueError("image_bytes no puede estar vacío")

    try:
        client.put_object(
            Bucket=bucket,
            Key=r2_key,
            Body=image_bytes,
            ContentType='image/webp',
        )
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_message = e.response.get('Error', {}).get('Message', str(e))
        logger.error(f"ClientError subiendo avatar: {error_code} - {error_message}")
        raise Exception(f"Error subiendo avatar a R2: {error_code} - {error_message}")

    return f"{public_base.rstrip('/')}/{r2_key}"


def delete_avatar(r2_key: str) -> None:
    bucket = os.getenv('CF_R2_BUCKET_AVATARS')
    client = _get_avatar_boto_client()
    if not client or not bucket or not r2_key:
        return
    try:
        client.delete_object(Bucket=bucket, Key=r2_key)
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        if error_code in ('NoSuchKey', '404'):
            return
        logger.warning(f"ClientError eliminando avatar {r2_key}: {error_code}")
    except Exception as e:
        logger.warning(f"Error inesperado eliminando avatar {r2_key}: {type(e).__name__} - {e}")


def invalidate_tenant_r2_cache(*, schema_name: str):
    with _cache_lock:
        if schema_name in _tenant_clients:
            del _tenant_clients[schema_name]
            logger.info(f"Cache invalidado para tenant {schema_name}")
