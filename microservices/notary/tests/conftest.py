import os

os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("FALLBACK_TO_VISUAL", "true")

import io
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def make_test_pdf(n_pages: int = 1) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for _ in range(n_pages):
        c.drawString(50, 50, "end-text")
        c.showPage()
    c.save()
    return buf.getvalue()


def make_ephemeral_certificate():
    from app.certificate_loader import LoadedCertificate

    key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Test Signer")]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256(), default_backend())
    )
    password = "test123"
    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=b"test",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(
            password.encode("utf-8")
        ),
    )
    fd, tmp_path = tempfile.mkstemp(suffix=".p12")
    with os.fdopen(fd, "wb") as fh:
        fh.write(p12_bytes)

    return LoadedCertificate(
        private_key=key,
        certificate=cert,
        additional_certs=None,
        tenant_id="test",
        path=tmp_path,
        _password=password,
    )


@pytest.fixture
def test_pdf_factory():
    return make_test_pdf


@pytest.fixture
def ephemeral_cert_factory():
    return make_ephemeral_certificate
