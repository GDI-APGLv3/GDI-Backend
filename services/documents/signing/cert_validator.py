from shared.logging import get_logger
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = get_logger(__name__)


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

    now = datetime.now(timezone.utc)
    if now < not_before:
        return CertValidationResult(ok=False, failure_reason="cert_not_yet_valid", **base)
    if now > not_after:
        return CertValidationResult(ok=False, failure_reason="cert_expired", **base)

    log.info("cert_temporal_ok serial=%s issuer=%s", serial_str, issuer_dn)


    return CertValidationResult(
        ok=True,
        cert_not_after=not_after,
        revocation_status="unknown",
        **base,
    )

