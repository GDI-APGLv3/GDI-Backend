# Variables de Entorno - GDI-Notary

> Ultima actualizacion: 2026-02-20

## Archivos de configuracion

| Archivo | Proposito | En Git? |
|---------|-----------|---------|
| `.env` | Desarrollo local | NO (.gitignore) |
| `.env.example` | Template referencia | SI |
| `ENV.md` | Documentacion | SI |

## Inventario completo

| Variable | Tipo | Requerida | Default | Descripcion | Donde configurar |
|----------|------|-----------|---------|-------------|-----------------|
| API_KEY | Secret | Si | your-api-key-here | API Key autenticacion | Fly Secret |
| ENVIRONMENT | Config | Si | test | Ambiente: test o prd | fly.toml [env] |
| CERTS_DIR | Config | No | ./certs | Directorio certificados .p12 | fly.toml [env] |
| TSA_URL | Config | No | http://timestamp.digicert.com | Timestamp Authority | fly.toml [env] |
| FALLBACK_TO_VISUAL | Config | No | true (test) / false (prd) | Firma visual si no hay cert | fly.toml [env] |
| GUNICORN_WORKERS | Config | No | 3 | Numero de workers | fly.toml [env] |
| GUNICORN_TIMEOUT | Config | No | 90 | Timeout en segundos | fly.toml [env] |
| PORT | Config | No | 8000 | Puerto del servidor | fly.toml [env] |

## Configuracion por ambiente

### Local (desarrollo)
Copiar `.env.example` a `.env` y completar valores.

### Fly.io (dev/staging/prod)

**Secrets:**
```bash
fly secrets set API_KEY=xxx
```

**Config (en fly.toml [env]):**
```toml
[env]
  ENVIRONMENT = "test"
  PORT = "8080"
```

### Notas
- Certificados .p12 van dentro de la imagen Docker (en `certs/`)
- Futuro: migrar certificados a Cloudflare R2 o Fly Volumes
- Stateless: no usa BD
