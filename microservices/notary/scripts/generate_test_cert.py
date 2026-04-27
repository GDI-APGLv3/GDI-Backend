#!/usr/bin/env python3
"""
Script para generar certificados auto-firmados de prueba para firma PAdES.

Este script genera certificados PKCS#12 (.p12) auto-firmados para pruebas
de firma digital. NO USAR EN PRODUCCION - los certificados de producción
deben ser emitidos por una Autoridad Certificadora (CA) reconocida.

Uso:
    python generate_test_cert.py --tenant 100_test
    python generate_test_cert.py --tenant municipio_demo --password mi_clave
    python generate_test_cert.py --tenant 100_test --output ../certs

Autor: GDI Latam
Versión: 1.0.0
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
except ImportError:
    print("Error: Se requiere el paquete 'cryptography'")
    print("Instalar con: pip install cryptography")
    sys.exit(1)


def generate_test_certificate(
    tenant_id: str,
    password: str,
    output_dir: Path,
    validity_days: int = 365,
    common_name: str = None,
    organization: str = None,
) -> Path:
    """
    Genera un certificado auto-firmado en formato PKCS#12.

    Args:
        tenant_id: Identificador del tenant (nombre del archivo)
        password: Contraseña para proteger el archivo .p12
        output_dir: Directorio de salida
        validity_days: Días de validez del certificado
        common_name: Nombre común (CN) del certificado
        organization: Organización (O) del certificado

    Returns:
        Path: Ruta al archivo .p12 generado
    """
    # Valores por defecto basados en tenant_id
    if common_name is None:
        common_name = f"GDI Test Certificate - {tenant_id}"
    if organization is None:
        organization = f"Municipio {tenant_id} (TEST)"

    # Generar clave privada RSA 2048 bits
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )

    # Datos del sujeto/emisor (auto-firmado)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Buenos Aires"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Ciudad Autonoma"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, organization),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    # Fechas de validez (usar timezone-aware datetime)
    not_valid_before = datetime.now(timezone.utc)
    not_valid_after = not_valid_before + timedelta(days=validity_days)

    # Construir el certificado
    cert_builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_valid_before)
        .not_valid_after(not_valid_after)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                content_commitment=True,  # Non-repudiation
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.CODE_SIGNING,
                # Agregar OID para firma de documentos si es necesario
            ]),
            critical=False,
        )
    )

    # Firmar el certificado (auto-firmado)
    certificate = cert_builder.sign(private_key, hashes.SHA256(), default_backend())

    # Crear directorio de salida si no existe
    output_dir.mkdir(parents=True, exist_ok=True)

    # Guardar como PKCS#12 (.p12)
    from cryptography.hazmat.primitives.serialization import pkcs12
    p12_path = output_dir / f"{tenant_id}.p12"
    p12_data = pkcs12.serialize_key_and_certificates(
        name=tenant_id.encode('utf-8'),
        key=private_key,
        cert=certificate,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode('utf-8')),
    )

    with open(p12_path, 'wb') as f:
        f.write(p12_data)

    print(f"Certificado generado: {p12_path}")
    print(f"  - Tenant ID: {tenant_id}")
    print(f"  - Common Name: {common_name}")
    print(f"  - Organization: {organization}")
    print(f"  - Válido desde: {not_valid_before.isoformat()}")
    print(f"  - Válido hasta: {not_valid_after.isoformat()}")
    print(f"  - Password: {password}")

    return p12_path


def update_passwords_file(output_dir: Path, tenant_id: str, password: str):
    """
    Actualiza el archivo passwords.json con la nueva contraseña.

    Args:
        output_dir: Directorio donde está passwords.json
        tenant_id: ID del tenant
        password: Contraseña del certificado
    """
    passwords_file = output_dir / "passwords.json"

    # Cargar passwords existentes o crear nuevo dict
    if passwords_file.exists():
        with open(passwords_file, 'r') as f:
            passwords = json.load(f)
    else:
        passwords = {}

    # Agregar o actualizar password
    passwords[tenant_id] = password

    # Guardar
    with open(passwords_file, 'w') as f:
        json.dump(passwords, f, indent=2)

    print(f"Password agregado a: {passwords_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Genera certificados auto-firmados de prueba para firma PAdES",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  %(prog)s --tenant 100_test
  %(prog)s --tenant municipio_demo --password clave_segura
  %(prog)s --tenant 100_test --output ./certs --days 730

ADVERTENCIA: Los certificados generados son SOLO para pruebas.
Para producción, use certificados de una CA reconocida.
        """
    )

    parser.add_argument(
        "--tenant", "-t",
        required=True,
        help="ID del tenant (será el nombre del archivo .p12)"
    )

    parser.add_argument(
        "--password", "-p",
        default="test123",
        help="Contraseña para el archivo .p12 (default: test123)"
    )

    parser.add_argument(
        "--output", "-o",
        default="../certs",
        help="Directorio de salida (default: ../certs)"
    )

    parser.add_argument(
        "--days", "-d",
        type=int,
        default=365,
        help="Días de validez del certificado (default: 365)"
    )

    parser.add_argument(
        "--cn",
        help="Common Name personalizado"
    )

    parser.add_argument(
        "--org",
        help="Organization personalizada"
    )

    parser.add_argument(
        "--no-passwords-file",
        action="store_true",
        help="No actualizar passwords.json"
    )

    args = parser.parse_args()

    # Resolver directorio de salida relativo al script
    script_dir = Path(__file__).parent
    output_dir = (script_dir / args.output).resolve()

    print("=" * 60)
    print("  Generador de Certificados de Prueba - GDI Notary")
    print("=" * 60)
    print()
    print("ADVERTENCIA: Estos certificados son SOLO para desarrollo/pruebas.")
    print("NO usar en producción. Use certificados de una CA reconocida.")
    print()

    # Generar certificado
    p12_path = generate_test_certificate(
        tenant_id=args.tenant,
        password=args.password,
        output_dir=output_dir,
        validity_days=args.days,
        common_name=args.cn,
        organization=args.org,
    )

    # Actualizar passwords.json
    if not args.no_passwords_file:
        print()
        update_passwords_file(output_dir, args.tenant, args.password)

    print()
    print("=" * 60)
    print("Certificado generado exitosamente!")
    print()
    print("Próximos pasos:")
    print(f"  1. Verificar certificado: openssl pkcs12 -info -in {p12_path}")
    print(f"  2. Probar firma: curl -X POST http://localhost:8001/sign-pdf ...")
    print("=" * 60)


if __name__ == "__main__":
    main()
