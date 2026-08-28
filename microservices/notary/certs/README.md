# Certificados PAdES

Este directorio contiene los certificados PKCS#12 (.p12) para firma digital PAdES por tenant.

## Estructura

```
certs/
├── {tenant_id}.p12    # Certificado PKCS#12 del tenant
├── passwords.json     # Mapeo tenant_id → password
└── README.md          # Este archivo
```

## Archivos

### Certificados por tenant
- Formato: PKCS#12 (.p12)
- Nombre: `{tenant_id}.p12` (ej: `100_test.p12`, `municipio_demo.p12`)
- Contenido: Clave privada + certificado + cadena de certificados (opcional)

### passwords.json
Mapeo de tenant_id a password del certificado:
```json
{
  "100_test": "test123",
  "municipio_demo": "clave_segura"
}
```

## Generar certificado de prueba

```bash
# Desde el directorio GDI-Notary
python scripts/generate_test_cert.py --tenant 100_test

# Con password personalizado
python scripts/generate_test_cert.py --tenant municipio_demo --password mi_clave

# Ver opciones
python scripts/generate_test_cert.py --help
```

## Configuración

Variables de entorno en GDI-Notary:

| Variable | Default | Descripción |
|----------|---------|-------------|
| CERTS_DIR | ./certs | Directorio de certificados |
| TSA_URL | http://timestamp.digicert.com | Servidor de timestamp |
| FALLBACK_TO_VISUAL | true | Si no hay certificado, usar firma visual |

## Seguridad

- **NUNCA** commits certificados de producción al repositorio
- Los archivos `.p12` y `passwords.json` deben estar en `.gitignore`
- En producción, usar:
  - Railway Secrets para passwords
  - Volúmenes o secrets para certificados
  - Cloudflare R2 encriptado (fase futura)

## Verificar certificado

```bash
# Ver info del certificado
openssl pkcs12 -info -in 100_test.p12 -nokeys

# Extraer certificado público
openssl pkcs12 -in 100_test.p12 -clcerts -nokeys -out cert.pem

# Verificar fechas
openssl x509 -in cert.pem -text -noout | grep -A2 Validity
```

## Certificados de producción

Para producción, obtener certificados de una CA reconocida:
- DigiCert
- GlobalSign
- Sectigo
- CAs locales autorizadas por el gobierno