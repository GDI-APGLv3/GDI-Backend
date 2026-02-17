"""
Cliente de Cloudflare R2 para acceso directo a storage.
Reemplaza llamadas HTTP a Legal Orchestrator por acceso boto3 directo.

Buckets soportados (multi-tenant):
- Cada tenant tiene sus propios buckets configurados en {schema}.settings
- Fallback a ENV vars para desarrollo/testing

Genera URLs firmadas temporales (expiran en 600 segundos por defecto).
"""

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
    """Cliente para interactuar directamente con Cloudflare R2 (S3-compatible)"""

    def __init__(self, bucket_oficial: str = None, bucket_tosign: str = None):
        """
        Inicializa cliente R2 con credenciales desde variables de entorno.

        Args:
            bucket_oficial: Nombre del bucket oficial (opcional, default desde ENV)
            bucket_tosign: Nombre del bucket tosign (opcional, default desde ENV)

        Variables de entorno requeridas:
        - CF_R2_ENDPOINT: URL del endpoint de Cloudflare R2
        - CF_R2_ACCESS_KEY_ID: Access key de Cloudflare R2
        - CF_R2_SECRET_ACCESS_KEY: Secret key de Cloudflare R2
        - CF_R2_BUCKET_OFICIAL: Nombre del bucket de documentos oficiales (fallback)
        - CF_R2_BUCKET_TOSIGN: Nombre del bucket de documentos en firma (fallback)
        - CF_R2_SIGN_EXPIRATION: Tiempo de expiración de URLs en segundos (default 600)
        """
        self.endpoint_url = os.getenv('CF_R2_ENDPOINT')
        self.access_key_id = os.getenv('CF_R2_ACCESS_KEY_ID')
        self.secret_access_key = os.getenv('CF_R2_SECRET_ACCESS_KEY')
        # Buckets: usar parámetros explícitos o fallback a ENV vars
        self.bucket_oficial = bucket_oficial or os.getenv('CF_R2_BUCKET_OFICIAL', 'tenant-test-oficial')
        self.bucket_tosign = bucket_tosign or os.getenv('CF_R2_BUCKET_TOSIGN', 'tenant-test-tosign')
        self.url_expiration = int(os.getenv('CF_R2_SIGN_EXPIRATION', '600'))

        # Validar que tenemos todas las credenciales requeridas
        if not all([self.endpoint_url, self.access_key_id, self.secret_access_key]):
            logger.warning("Credenciales de Cloudflare R2 no configuradas completamente")
            logger.warning(f"Endpoint: {'OK' if self.endpoint_url else 'FALTA'}")
            logger.warning(f"Access Key: {'OK' if self.access_key_id else 'FALTA'}")
            logger.warning(f"Secret Key: {'OK' if self.secret_access_key else 'FALTA'}")
            self._client = None
            return

        # Crear cliente boto3 S3 apuntando a Cloudflare R2
        try:
            self._client = boto3.client(
                's3',
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                config=Config(signature_version='s3v4', region_name='auto')
            )
            logger.info("Cliente inicializado correctamente")
            logger.info(f"Endpoint: {self.endpoint_url}")
            logger.info(f"Bucket oficial: {self.bucket_oficial}")
            logger.info(f"Bucket tosign: {self.bucket_tosign}")
            logger.info(f"Expiración URLs: {self.url_expiration}s")
        except Exception as e:
            logger.error(f"ERROR inicializando cliente: {e}")
            self._client = None

    def get_oficial_url(self, official_number: str) -> Optional[str]:
        """
        Genera URL firmada para documento oficial desde bucket 'oficial'.

        Los documentos oficiales son aquellos que ya fueron firmados completamente
        y tienen un número oficial asignado (ej: ANEXO-2025-00000001-SMG-ADGEN).

        Args:
            official_number: Número oficial del documento (con o sin extensión .pdf)

        Returns:
            URL firmada temporal (expira según CF_R2_SIGN_EXPIRATION) o None si falla

        Ejemplos:
            >>> client.get_oficial_url("ANEXO-2025-00000001-SMG-ADGEN")
            'https://...cloudflare.com/...'
            >>> client.get_oficial_url("ANEXO-2025-00000001-SMG-ADGEN.pdf")
            'https://...cloudflare.com/...'
        """
        if not self._client:
            logger.info("Cliente no inicializado, no se puede obtener URL oficial")
            return None

        # Normalizar: agregar .pdf si no lo tiene (misma lógica que upload_oficial)
        filename = official_number
        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        try:
            logger.info("Generando URL firmada para documento oficial")
            logger.info(f"Bucket: {self.bucket_oficial}")
            logger.info(f"Key: {filename}")

            url = self._client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_oficial,
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

    def get_tosign_url(self, document_filename: str) -> Optional[str]:
        """
        Genera URL firmada para documento en proceso de firma desde bucket 'to-sign'.

        Los documentos en tosign son aquellos que fueron enviados a firma pero aún
        no están completamente firmados. El filename debe ser el UUID sin guiones.

        Args:
            document_filename: Nombre del archivo en R2 (UUID sin guiones, ej: 214c5d1695ea4865876de8e826ef3ece.pdf)

        Returns:
            URL firmada temporal o None si falla

        Ejemplos:
            >>> client.get_tosign_url("214c5d1695ea4865876de8e826ef3ece.pdf")
            'https://...cloudflare.com/...'
        """
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
        """
        Verifica si un archivo existe en el bucket tosign.

        Usa head_object para verificar existencia sin descargar el archivo.
        Útil para documentos importados donde el PDF puede no existir aún.

        Args:
            filename: Nombre del archivo (UUID sin guiones + .pdf)

        Returns:
            True si existe, False si no existe o hay error

        Ejemplos:
            >>> client.exists_tosign("214c5d1695ea4865876de8e826ef3ece.pdf")
            True
            >>> client.exists_tosign("nonexistent.pdf")
            False
        """
        if not self._client:
            logger.info("Cliente no inicializado, no se puede verificar existencia")
            return False

        # Normalizar: agregar .pdf si no lo tiene
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
            # 404 y NoSuchKey significan que no existe (no es error)
            if error_code in ('404', 'NoSuchKey'):
                logger.info(f"Archivo no existe en tosign: {filename}")
                return False
            # Otros errores (permisos, conexión) los logueamos y retornamos False
            logger.warning(f"Error verificando existencia: {error_code}")
            return False

        except Exception as e:
            logger.warning(f"Error inesperado verificando existencia: {type(e).__name__}")
            return False

    def upload_tosign(self, pdf_bytes: bytes, filename: str) -> dict:
        """
        Sube PDF al bucket tosign para proceso de firma.

        Args:
            pdf_bytes: Contenido binario del PDF (validado por PDFComposer)
            filename: UUID sin guiones + '.pdf' (ej: '214c5d1695ea4865876de8e826ef3ece.pdf')

        Returns:
            dict: {
                "status": "success",
                "location": "tosign",
                "filename": "214c5d1695ea4865876de8e826ef3ece.pdf",
                "uploaded_at": "2025-10-22T10:30:00Z",
                "size_bytes": 245678
            }

        Raises:
            ValueError: Si pdf_bytes está vacío
            Exception: Si falla upload a R2 (propaga ClientError)
        """
        from shared.exceptions import ValidationError
        from datetime import datetime

        if not self._client:
            logger.error("Cliente no inicializado")
            raise Exception("Cliente R2 no inicializado")

        # Normalizar filename (.pdf obligatorio)
        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        # Validar que tenemos bytes
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
        """
        Elimina PDF del bucket tosign (cuando se rechaza documento).

        SOFT-FAIL: Si el archivo no existe, retorna success (idempotente).

        Args:
            filename: UUID sin guiones + '.pdf'

        Returns:
            dict: {
                "status": "success",
                "location": "tosign",
                "filename": "214c5d1695ea4865876de8e826ef3ece.pdf",
                "deleted_at": "2025-10-22T10:35:00Z"
            }

        Raises:
            Exception: Solo si hay error de conexión R2 (no si archivo no existe)
        """
        from datetime import datetime

        if not self._client:
            logger.error("Cliente no inicializado")
            raise Exception("Cliente R2 no inicializado")

        # Normalizar filename
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

            # NoSuchKey NO es error (idempotente)
            if error_code == 'NoSuchKey':
                logger.warning("Archivo no encontrado (ya eliminado previamente)")
                return {
                    "status": "success",
                    "location": "tosign",
                    "filename": filename,
                    "deleted_at": datetime.utcnow().isoformat() + "Z",
                    "note": "File did not exist (idempotent operation)"
                }

            # Otros errores sí son críticos
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"ClientError: {error_code}")
            logger.error(f"Message: {error_message}")
            raise Exception(f"Error eliminando PDF de R2: {error_code} - {error_message}")

        except Exception as e:
            logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
            raise

    def upload_oficial(self, pdf_bytes: bytes, filename: str) -> dict:
        """
        Sube PDF firmado al bucket oficial (documentos completados).

        Args:
            pdf_bytes: Contenido binario del PDF firmado (validado por Notary)
            filename: Número oficial + '.pdf' (ej: 'IF-2025-000000157-MT-DGOBR.pdf')

        Returns:
            dict: {
                "status": "success",
                "location": "oficial",
                "filename": "IF-2025-000000157-MT-DGOBR.pdf",
                "uploaded_at": "2025-10-22T10:40:00Z",
                "size_bytes": 289456
            }

        Raises:
            ValueError: Si pdf_bytes está vacío
            Exception: Si falla upload a R2 (propaga ClientError)
        """
        from shared.exceptions import ValidationError
        from datetime import datetime

        if not self._client:
            logger.error("Cliente no inicializado")
            raise Exception("Cliente R2 no inicializado")

        # Normalizar filename (.pdf obligatorio)
        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        # Validar que tenemos bytes
        if not pdf_bytes or len(pdf_bytes) == 0:
            logger.error("PDF bytes no puede estar vacío")
            raise ValueError("PDF bytes no puede estar vacío")

        try:
            logger.info("Subiendo PDF a bucket oficial")
            logger.info(f"Bucket: {self.bucket_oficial}")
            logger.info(f"Filename: {filename}")
            logger.info(f"Size: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

            self._client.put_object(
                Bucket=self.bucket_oficial,
                Key=filename,
                Body=pdf_bytes,
                ContentType='application/pdf'
            )

            logger.info(f"Upload exitoso en {self.bucket_oficial}/{filename}")

            return {
                "status": "success",
                "location": "oficial",
                "filename": filename,
                "uploaded_at": datetime.utcnow().isoformat() + "Z",
                "size_bytes": len(pdf_bytes)
            }

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"ClientError: {error_code}")
            logger.error(f"Message: {error_message}")
            raise Exception(f"Error subiendo PDF firmado a R2 oficial: {error_code} - {error_message}")
        except Exception as e:
            logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
            raise


# Instancia singleton del cliente R2
_r2_client: Optional[CloudflareR2Client] = None


def get_r2_client() -> CloudflareR2Client:
    """
    Obtiene instancia singleton del cliente R2 (legacy, usa ENV vars).

    Se crea una sola vez y se reutiliza en toda la aplicación para evitar
    múltiples inicializaciones del cliente boto3.

    Returns:
        CloudflareR2Client: Instancia única del cliente

    Ejemplos:
        >>> from services.storage.cloudflare import get_r2_client
        >>> client = get_r2_client()
        >>> url = client.get_oficial_url("DOC-123.pdf")
    """
    global _r2_client
    if _r2_client is None:
        logger.info("Creando nueva instancia singleton del cliente R2")
        _r2_client = CloudflareR2Client()
    return _r2_client


# ============================================================================
# MULTI-TENANT: Cliente R2 por tenant con buckets desde BD
# ============================================================================

# Cache de clientes R2 por tenant: {schema_name: (client, timestamp)}
_tenant_clients: Dict[str, Tuple[CloudflareR2Client, float]] = {}
_cache_lock = Lock()
CACHE_TTL_SECONDS = 3600  # 1 hora


def get_tenant_settings(schema_name: str) -> Dict[str, str]:
    """
    Obtiene configuración de buckets R2 desde la tabla settings del tenant.

    Args:
        schema_name: Nombre del schema del tenant (ej: '100_test', 'municipio_xyz')

    Returns:
        Dict con 'bucket_oficial' y 'bucket_tosign'

    Raises:
        ValueError: Si el tenant no tiene settings configurados
    """
    from database import execute_query

    query = "SELECT bucket_oficial, bucket_tosign FROM settings LIMIT 1"
    result = execute_query(query, schema_name=schema_name)

    if not result or not result[0].get('bucket_oficial'):
        raise ValueError(f"Tenant {schema_name} no tiene buckets configurados en settings")

    return {
        'bucket_oficial': result[0]['bucket_oficial'],
        'bucket_tosign': result[0]['bucket_tosign']
    }


def get_tenant_r2_client(*, schema_name: str) -> CloudflareR2Client:
    """
    Obtiene cliente R2 específico para el tenant.

    Lee los nombres de buckets desde {schema}.settings y crea un cliente
    configurado para ese tenant. Usa cache thread-safe con TTL de 1 hora.

    Args:
        schema_name: Nombre del schema del tenant (requerido)

    Returns:
        CloudflareR2Client configurado con los buckets del tenant

    Ejemplos:
        >>> from services.storage.cloudflare import get_tenant_r2_client
        >>> client = get_tenant_r2_client(schema_name='100_test')
        >>> url = client.get_oficial_url("DOC-123.pdf")  # Usa bucket del tenant
    """
    now = time.time()

    # Verificar cache (thread-safe)
    with _cache_lock:
        if schema_name in _tenant_clients:
            client, created_at = _tenant_clients[schema_name]
            if now - created_at < CACHE_TTL_SECONDS:
                logger.info(f"Usando cliente cacheado para tenant {schema_name}")
                return client

    # Crear nuevo cliente (fuera del lock para no bloquear)
    logger.info(f"Creando nuevo cliente R2 para tenant {schema_name}")
    settings = get_tenant_settings(schema_name)

    new_client = CloudflareR2Client(
        bucket_oficial=settings['bucket_oficial'],
        bucket_tosign=settings['bucket_tosign']
    )

    # Guardar en cache (thread-safe)
    with _cache_lock:
        _tenant_clients[schema_name] = (new_client, now)

    logger.info(f"Cliente creado - Buckets: {settings['bucket_oficial']}, {settings['bucket_tosign']}")
    return new_client


def invalidate_tenant_r2_cache(*, schema_name: str):
    """
    Invalida cache de clientes R2 manualmente.

    Útil si se actualizan los settings de buckets de un tenant.

    Args:
        schema_name: Schema específico a invalidar (requerido)
    """
    with _cache_lock:
        if schema_name in _tenant_clients:
            del _tenant_clients[schema_name]
            logger.info(f"Cache invalidado para tenant {schema_name}")
