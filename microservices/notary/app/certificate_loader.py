"""
Módulo para cargar certificados PKCS#12 por tenant.

Este módulo maneja la carga y validación de certificados .p12
para firma digital PAdES, organizados por tenant_id.

Estructura de archivos esperada:
    certs/
    ├── {tenant_id}.p12      # Certificado del tenant
    └── passwords.json       # Mapeo tenant_id → password

Autor: GDI Latam
Versión: 1.0.0
"""

import json
import logging
import tempfile
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import Certificate
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from .config import CERTS_DIR, PASSWORDS_FILE

logger = logging.getLogger(__name__)


class CertificateError(Exception):
    """Excepción para errores relacionados con certificados."""
    pass


class CertificateNotFoundError(CertificateError):
    """Certificado no encontrado para el tenant."""
    pass


class CertificateLoadError(CertificateError):
    """Error al cargar el certificado."""
    pass


class PasswordNotFoundError(CertificateError):
    """Password no encontrado para el tenant."""
    pass


@dataclass
class LoadedCertificate:
    """Certificado cargado con su clave privada."""
    private_key: PrivateKeyTypes
    certificate: Certificate
    additional_certs: Optional[list] = None
    tenant_id: str = ""
    path: Path = None
    _password: Optional[str] = field(default=None, repr=False)
    _temp_file: Optional[str] = field(default=None, repr=False)


def load_passwords() -> dict:
    """
    Carga el archivo de passwords.

    Returns:
        dict: Mapeo tenant_id → password

    Raises:
        CertificateError: Si no se puede leer el archivo
    """
    if not PASSWORDS_FILE.exists():
        logger.warning(f"Archivo de passwords no encontrado: {PASSWORDS_FILE}")
        return {}

    try:
        with open(PASSWORDS_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise CertificateError(f"Error al parsear passwords.json: {e}")
    except IOError as e:
        raise CertificateError(f"Error al leer passwords.json: {e}")


def get_certificate_path(tenant_id: str) -> Path:
    """
    Obtiene la ruta del certificado para un tenant.
    Incluye verificación de contención de path para prevenir path traversal.

    Args:
        tenant_id: ID del tenant

    Returns:
        Path: Ruta al archivo .p12

    Raises:
        CertificateError: Si el path resultante escapa del directorio de certificados
    """
    certs_dir = Path(CERTS_DIR).resolve()
    cert_path = (certs_dir / f"{tenant_id}.p12").resolve()

    if not cert_path.is_relative_to(certs_dir):
        raise CertificateError(
            "Invalid tenant_id: path traversal detected"
        )

    return cert_path


def certificate_exists(tenant_id: str) -> bool:
    """
    Verifica si existe un certificado para el tenant.

    Args:
        tenant_id: ID del tenant

    Returns:
        bool: True si existe el certificado
    """
    cert_path = get_certificate_path(tenant_id)
    return cert_path.exists()


def load_certificate(tenant_id: str) -> LoadedCertificate:
    """
    Carga un certificado PKCS#12 para un tenant específico.

    Args:
        tenant_id: ID del tenant

    Returns:
        LoadedCertificate: Certificado cargado con clave privada

    Raises:
        CertificateNotFoundError: Si no existe el certificado
        PasswordNotFoundError: Si no hay password para el tenant
        CertificateLoadError: Si hay error al cargar el certificado
    """
    cert_path = get_certificate_path(tenant_id)

    # Verificar que existe el archivo
    if not cert_path.exists():
        raise CertificateNotFoundError(
            f"Certificado no encontrado para tenant '{tenant_id}': {cert_path}"
        )

    # Obtener password
    passwords = load_passwords()
    password = passwords.get(tenant_id)

    if password is None:
        raise PasswordNotFoundError(
            f"Password no encontrado para tenant '{tenant_id}' en passwords.json"
        )

    # Cargar el certificado PKCS#12
    try:
        with open(cert_path, 'rb') as f:
            p12_data = f.read()

        private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
            p12_data,
            password.encode('utf-8'),
            default_backend()
        )

        if private_key is None:
            raise CertificateLoadError(
                f"El archivo .p12 no contiene clave privada: {cert_path}"
            )

        if certificate is None:
            raise CertificateLoadError(
                f"El archivo .p12 no contiene certificado: {cert_path}"
            )

        logger.info(f"Certificado cargado exitosamente para tenant '{tenant_id}'")
        logger.debug(f"  - Subject: {certificate.subject}")
        logger.debug(f"  - Issuer: {certificate.issuer}")
        logger.debug(f"  - Valid from: {certificate.not_valid_before_utc}")
        logger.debug(f"  - Valid until: {certificate.not_valid_after_utc}")

        return LoadedCertificate(
            private_key=private_key,
            certificate=certificate,
            additional_certs=list(additional_certs) if additional_certs else None,
            tenant_id=tenant_id,
            path=cert_path
        )

    except ValueError as e:
        # Password incorrecto o formato inválido
        raise CertificateLoadError(
            f"Error al cargar certificado para tenant '{tenant_id}': {e}"
        )
    except IOError as e:
        raise CertificateLoadError(
            f"Error al leer archivo de certificado: {e}"
        )


def validate_certificate(cert: LoadedCertificate) -> Tuple[bool, str]:
    """
    Valida que el certificado sea usable para firma.

    Args:
        cert: Certificado cargado

    Returns:
        Tuple[bool, str]: (es_válido, mensaje)
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    # Verificar vigencia
    not_before = cert.certificate.not_valid_before_utc
    not_after = cert.certificate.not_valid_after_utc

    if now < not_before:
        return False, f"Certificado aún no es válido. Válido desde: {not_before}"

    if now > not_after:
        return False, f"Certificado expirado. Expiró el: {not_after}"

    # Verificar key usage si está presente
    try:
        from cryptography.x509 import ExtensionOID
        key_usage = cert.certificate.extensions.get_extension_for_oid(
            ExtensionOID.KEY_USAGE
        )
        if not key_usage.value.digital_signature:
            return False, "Certificado no permite firma digital (key_usage)"
    except Exception:
        # Si no tiene key_usage extension, asumimos que es válido
        pass

    return True, "Certificado válido"


def get_certificate_info(tenant_id: str) -> dict:
    """
    Obtiene información del certificado sin cargar la clave privada.

    Args:
        tenant_id: ID del tenant

    Returns:
        dict: Información del certificado
    """
    cert_path = get_certificate_path(tenant_id)

    if not cert_path.exists():
        return {
            "exists": False,
            "tenant_id": tenant_id,
        }

    try:
        cert = load_certificate(tenant_id)
        is_valid, message = validate_certificate(cert)

        return {
            "exists": True,
            "tenant_id": tenant_id,
            "subject": str(cert.certificate.subject),
            "issuer": str(cert.certificate.issuer),
            "not_valid_before": cert.certificate.not_valid_before_utc.isoformat(),
            "not_valid_after": cert.certificate.not_valid_after_utc.isoformat(),
            "is_valid": is_valid,
            "validation_message": message,
            "serial_number": str(cert.certificate.serial_number),
        }
    except CertificateError as e:
        return {
            "exists": True,
            "tenant_id": tenant_id,
            "error": str(e)
        }


def list_available_certificates() -> list:
    """
    Lista todos los certificados disponibles.

    Returns:
        list: Lista de tenant_ids con certificados
    """
    certs_dir = Path(CERTS_DIR)
    if not certs_dir.exists():
        return []

    return [
        p.stem for p in certs_dir.glob("*.p12")
    ]


def load_certificate_from_bytes(p12_bytes: bytes, password: str, tenant_id: str = "") -> LoadedCertificate:
    """
    Carga certificado PKCS#12 desde bytes en memoria (sin filesystem).
    Escribe a tempfile porque pyHanko requiere un path al .p12.

    Args:
        p12_bytes: Contenido del archivo .p12
        password: Password del certificado
        tenant_id: ID del tenant (para logging)

    Returns:
        LoadedCertificate con path a tempfile y _password/_temp_file seteados

    Raises:
        CertificateLoadError: Si hay error al cargar
    """
    try:
        private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
            p12_bytes, password.encode("utf-8"), default_backend()
        )

        if private_key is None:
            raise CertificateLoadError("El .p12 no contiene clave privada")
        if certificate is None:
            raise CertificateLoadError("El .p12 no contiene certificado")

        # pyHanko necesita un path al archivo .p12
        tmp = tempfile.NamedTemporaryFile(suffix=".p12", delete=False)
        tmp.write(p12_bytes)
        tmp.close()

        logger.info(f"Certificado cargado desde bytes para tenant '{tenant_id}'")
        logger.debug(f"  - Subject: {certificate.subject}")
        logger.debug(f"  - Temp file: {tmp.name}")

        return LoadedCertificate(
            private_key=private_key,
            certificate=certificate,
            additional_certs=list(additional_certs) if additional_certs else None,
            tenant_id=tenant_id,
            path=Path(tmp.name),
            _password=password,
            _temp_file=tmp.name,
        )

    except (CertificateLoadError, CertificateError):
        raise
    except ValueError as e:
        raise CertificateLoadError(f"Password incorrecto o formato invalido: {e}")
    except Exception as e:
        raise CertificateLoadError(f"Error cargando certificado desde bytes: {e}")
