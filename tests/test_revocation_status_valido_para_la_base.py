
from datetime import datetime, timedelta, timezone

import pytest


REVOCATION_STATUS_PERMITIDOS = {None, "good", "revoked", "unknown"}


def _certificado_real_vigente() -> bytes:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.oid import NameOID

    clave = ec.generate_private_key(ec.SECP256R1())
    nombre = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "PEREZ Juan"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
    ])
    ahora = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=1))
        .not_valid_after(ahora + timedelta(days=365))
        .sign(clave, hashes.SHA256())
    )
    return cert.public_bytes(Encoding.DER)


def test_el_estado_de_revocacion_que_produce_el_validador_entra_en_la_base():
    from services.documents.signing.cert_validator import validate_cert_full

    resultado = validate_cert_full(_certificado_real_vigente(), expected_cuit=None)

    assert resultado.ok, f"el certificado de prueba debería validar: {resultado}"
    assert resultado.revocation_status in REVOCATION_STATUS_PERMITIDOS, (
        f"el validador devuelve revocation_status={resultado.revocation_status!r}, "
        f"que el CHECK de firma_audit_log rechaza. El INSERT de auditoría entero "
        f"rebota y se pierde el registro de la firma. Permitidos: "
        f"{sorted(x for x in REVOCATION_STATUS_PERMITIDOS if x)}"
    )


def test_ningun_camino_del_validador_inventa_un_estado_de_revocacion():
    import ast
    import inspect

    from services.documents.signing import cert_validator as cv

    arbol = ast.parse(inspect.getsource(cv))
    valores = [
        nodo.value.value
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.keyword)
        and nodo.arg == "revocation_status"
        and isinstance(nodo.value, ast.Constant)
    ]

    assert valores, "no se encontró ninguna asignación de revocation_status"
    invalidos = [v for v in valores if v not in REVOCATION_STATUS_PERMITIDOS]
    assert not invalidos, (
        f"el validador asigna {invalidos}, que el CHECK de firma_audit_log rechaza"
    )
