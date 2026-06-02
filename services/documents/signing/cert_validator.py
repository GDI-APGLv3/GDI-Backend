"""
Validacion de certificados X.509 de AC ONTI (firma digital con token ePass2003).

Estado actual (V2.2):
  Solo se valida la vigencia temporal del certificado.

Pipeline completo pendiente (TODO V3 — PRD):
  1. [ACTIVO] Validez temporal (not_before / not_after)
  2. [TODO V3] Chain check: descargar cert emisor desde AIA (caIssuers) y verificar
               firma criptografica (RSA PKCS1v15 / ECDSA). Requiere que el endpoint
               AIA de ONTI sea accesible desde Fly.io.
  3. [TODO V3] OCSP: consultar firmar.gob.ar con OCSPRequestBuilder (SHA256).
               Politica: revoked=fail, timeout/UNAUTHORIZED=soft-fail.
               Nota: ONTI retorna OCSPResponseStatus.UNAUTHORIZED con el request actual
               — hay que investigar si requiere GET en vez de POST, o hash SHA1.
  4. [TODO V3] Key Usage: digital_signature + non_repudiation (content_commitment).
  5. [TODO V3] CUIT match: cert.subject.SerialNumber == users.CountryID.
               El cert real de ONTI guarda el CUIT en un atributo aun no identificado.
               Ver cert_subject_dump en logs de la sesion 856044f para el OID exacto.
               El campo es OID 2.5.4.5 (serialNumber) pero el formato puede variar:
               "CUIT 20-12345678-9", "CUIT 20123456789", o solo digitos.
  6. [TODO V3] Notary /sign-pdf/verify: pyHanko valida la cadena de confianza del cert.
               ONTI usa AC raiz "AC Raiz 2016" y AC subordinada "AC ONTI 2016".
               Hay que agregar esos certs al trust store de GDI-Notary (pyHanko).
               Ver: https://pki.jefatura.gob.ar/politica_de_certificacion.html

Referencias para V3:
  - AC ONTI certs: http://pki.jefatura.gob.ar/  (CRL / certs descargables)
  - OCSP endpoint: http://ocsp.jefatura.gob.ar/
  - pyHanko trust store: settings.yml en GDI-Notary, campo trusted_cert_sources
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class CertValidationResult:
    ok: bool
    failure_reason: Optional[str] = None
    cert_subject_dn: Optional[str] = None
    cert_issuer_dn: Optional[str] = None
    cert_serial: Optional[str] = None
    cert_subject_cuit: Optional[str] = None
    cert_not_after: Optional[datetime] = None
    cert_policy_oids: list = field(default_factory=list)
    signature_algorithm: Optional[str] = None
    revocation_status: Optional[str] = None


def validate_cert_full(cert_der: bytes, *, expected_cuit: str | None) -> CertValidationResult:
    """
    Valida certificado X.509 DER.

    V2.2: solo valida vigencia temporal.
    V3: activar chain check, OCSP, Key Usage y CUIT match (ver docstring del modulo).
    """
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend

    try:
        cert = x509.load_der_x509_certificate(cert_der, default_backend())
    except Exception as e:
        return CertValidationResult(ok=False, failure_reason=f"cert_parse_error: {e}")

    try:
        subject_dn = cert.subject.rfc4514_string()
        issuer_dn = cert.issuer.rfc4514_string()
        serial_str = format(cert.serial_number, 'x')
        not_after = (
            cert.not_valid_after_utc
            if hasattr(cert, 'not_valid_after_utc')
            else cert.not_valid_after.replace(tzinfo=timezone.utc)
        )
        not_before = (
            cert.not_valid_before_utc
            if hasattr(cert, 'not_valid_before_utc')
            else cert.not_valid_before.replace(tzinfo=timezone.utc)
        )
    except Exception as e:
        return CertValidationResult(ok=False, failure_reason=f"cert_field_error: {e}")

    base = dict(cert_subject_dn=subject_dn, cert_issuer_dn=issuer_dn, cert_serial=serial_str)

    # Paso 1: Validez temporal (unico check activo en V2.2)
    now = datetime.now(timezone.utc)
    if now < not_before:
        return CertValidationResult(ok=False, failure_reason="cert_not_yet_valid", **base)
    if now > not_after:
        return CertValidationResult(ok=False, failure_reason="cert_expired", **base)

    log.info("cert_temporal_ok serial=%s issuer=%s", serial_str, issuer_dn)

    # TODO V3: chain check (paso 2)
    # TODO V3: OCSP (paso 3)
    # TODO V3: Key Usage (paso 4)
    # TODO V3: CUIT match (paso 5)

    return CertValidationResult(
        ok=True,
        cert_not_after=not_after,
        revocation_status="skipped_v2",
        **base,
    )

