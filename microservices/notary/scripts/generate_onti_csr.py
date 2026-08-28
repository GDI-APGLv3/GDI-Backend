#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
except ImportError:
    print("Error: Se requiere el paquete 'cryptography'")
    print("Instalar con: pip install cryptography")
    sys.exit(1)


def generar_key_y_csr(cn: str, cuit: str, org: str, ou: str, output_dir: Path) -> tuple[Path, Path]:
    _validar_campo("CN", cn)
    _validar_campo("O (organismo)", org)
    _validar_campo("OU (área)", ou)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
    ])

    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(key, hashes.SHA256())
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    key_path = output_dir / "municipio.key"
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    csr_path = output_dir / "municipio.csr"
    csr_path.write_bytes(csr.public_bytes(serialization.Encoding.PEM))

    return key_path, csr_path


def finalizar_p12(key_path: Path, cer_path: Path, password: str, output_dir: Path) -> Path:
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    cert = x509.load_pem_x509_certificate(cer_path.read_bytes())

    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=b"certificado",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode()),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    p12_path = output_dir / "municipio.p12"
    p12_path.write_bytes(p12_bytes)
    return p12_path


def _validar_campo(nombre: str, valor: str):
    if len(valor) > 64:
        print(f"Error: {nombre} supera los 64 caracteres (ONTI lo rechazará)")
        sys.exit(1)


def _imprimir_separador():
    print("=" * 60)


def cmd_generar(args):
    output_dir = Path(args.output)

    _imprimir_separador()
    print("  GDI Latam — Generador de CSR para AC ONTI")
    _imprimir_separador()
    print()

    key_path, csr_path = generar_key_y_csr(
        cn=args.cn,
        cuit=args.cuit,
        org=args.org,
        ou=args.ou,
        output_dir=output_dir,
    )

    print(f"[OK] Clave privada:  {key_path}")
    print(f"[OK] CSR generado:   {csr_path}")
    print()
    _imprimir_separador()
    print()
    print("PASO 1 — GUARDÁ LA CLAVE PRIVADA")
    print(f"  Archivo: {key_path}")
    print("  IMPORTANTE: Guardala en un lugar seguro.")
    print("  NO la compartas con nadie, ni con ONTI, ni con GDI.")
    print("  La vas a necesitar cuando llegue el certificado de ONTI.")
    print()
    print("PASO 2 — MANDÁ EL CSR A ONTI")
    print(f"  Archivo: {csr_path}")
    print("  - Ingresa a: https://incidencias.innovacion.gob.ar/servicedesk/customer/portal/16")
    print('  - Asunto: "Solicitud de certificado de aplicacion - [tu organismo]"')
    print("  - Adjunta el archivo .csr y completa la nota de solicitud.")
    print()
    print("PASO 3 — CUANDO LLEGUE EL .cer DE ONTI")
    print("  Volvé al BackOffice y finalizá el certificado,")
    print("  o corré este script con --finalizar.")
    print()
    _imprimir_separador()


def cmd_finalizar(args):
    key_path = Path(args.key)
    cer_path = Path(args.cer)
    output_dir = Path(args.output)

    if not key_path.exists():
        print(f"Error: no se encuentra la clave privada: {key_path}")
        sys.exit(1)
    if not cer_path.exists():
        print(f"Error: no se encuentra el certificado .cer: {cer_path}")
        sys.exit(1)

    _imprimir_separador()
    print("  GDI Latam — Finalizar certificado ONTI → .p12")
    _imprimir_separador()
    print()

    p12_path = finalizar_p12(key_path, cer_path, args.password, output_dir)

    print(f"[OK] .p12 generado: {p12_path}")
    print()
    print("LISTO. Ahora podés:")
    print(f"  1. Subir {p12_path} al BackOffice de GDI")
    print(f"  2. Ingresar la contraseña: {args.password}")
    print()
    print("  El certificado quedará activo para firma digital.")
    _imprimir_separador()


def main():
    parser = argparse.ArgumentParser(
        description="Generador de CSR para AC ONTI / Finalizador de .p12 para GDI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--finalizar", action="store_true",
                        help="Modo finalizar: combina .key + .cer en .p12")
    parser.add_argument("--output", default="./salida",
                        help="Directorio de salida (default: ./salida)")

    parser.add_argument("--cn", help="Nombre de la aplicación (CN, max 64 chars)")
    parser.add_argument("--cuit", help="CUIT del organismo (sin guiones)")
    parser.add_argument("--org", help="Nombre del organismo (O, max 64 chars)")
    parser.add_argument("--ou", help="Área responsable (OU, max 64 chars)")

    parser.add_argument("--key", help="Path al archivo .key generado en el paso 1")
    parser.add_argument("--cer", help="Path al archivo .cer emitido por ONTI")
    parser.add_argument("--password", help="Contraseña para proteger el .p12")

    args = parser.parse_args()

    if args.finalizar:
        for campo in ["key", "cer", "password"]:
            if not getattr(args, campo):
                print(f"Error: --{campo} es requerido para --finalizar")
                sys.exit(1)
        cmd_finalizar(args)
    else:
        for campo in ["cn", "cuit", "org", "ou"]:
            if not getattr(args, campo):
                print(f"Error: --{campo} es requerido")
                print("Ejemplo:")
                print('  python generate_onti_csr.py --cn "GDI Latam" --cuit "30123456789" '
                      '--org "Municipalidad del Futuro" --ou "Secretaria de Gobierno"')
                sys.exit(1)
        cmd_generar(args)


if __name__ == "__main__":
    main()
