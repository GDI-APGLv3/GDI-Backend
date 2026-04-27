# Variables de Entorno - GDI-Backend

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
| DB_HOST | Secret | Si | - | Host PostgreSQL | Fly Secret |
| DB_PORT | Config | Si | 5432 | Puerto PostgreSQL | fly.toml [env] |
| DB_USER | Secret | Si | - | Usuario PostgreSQL | Fly Secret |
| DB_PASSWORD | Secret | Si | - | Password PostgreSQL | Fly Secret |
| DB_NAME | Config | Si | postgres | Nombre de la BD | Fly Secret |
| PGBOUNCER_TRANSACTION_MODE | Config | No | false | Usar SET LOCAL para search_path | fly.toml [env] |
| TESTING_MODE | Config | No | false | Bypass Auth0 con X-User-ID | fly.toml [env] |
| AUTH0_DOMAIN | Secret | Si | - | Dominio Auth0 | Fly Secret |
| AUTH0_AUDIENCE | Secret | Si | - | Audience Auth0 | Fly Secret |
| AUTH0_CLIENT_ID | Secret | Si | - | Client ID Auth0 | Fly Secret |
| AUTH0_CLIENT_SECRET | Secret | Si | - | Client Secret Auth0 | Fly Secret |
| CF_R2_ENDPOINT | Secret | Si | - | Endpoint Cloudflare R2 | Fly Secret |
| CF_R2_ACCESS_KEY_ID | Secret | Si | - | Access key R2 | Fly Secret |
| CF_R2_SECRET_ACCESS_KEY | Secret | Si | - | Secret key R2 | Fly Secret |
| CF_R2_SIGN_EXPIRATION | Config | No | 600 | Expiracion URLs firmadas (seg) | fly.toml [env] |
| PDFCOMPOSER_URL | Config | Si | - | URL GDI-PDFComposer | Fly Secret |
| PDFCOMPOSER_API_KEY | Secret | Si | - | API Key PDFComposer | Fly Secret |
| NOTARY_URL | Config | Si | - | URL GDI-Notary | Fly Secret |
| NOTARY_API_KEY | Secret | Si | - | API Key Notary | Fly Secret |
| REDIS_URL | Secret | No | - | URL Redis (opcional) | Fly Secret |
| FRONTEND_URL | Config | Si | - | URL frontend (CORS) | Fly Secret |
| AGENTE_URL | Config | No | - | URL GDI-AgenteLANG | Fly Secret |
| INTERNAL_API_KEY | Secret | No | - | API Key comunicacion interna | Fly Secret |
| PORT | Config | No | 8000 | Puerto del servidor | fly.toml [env] |

## Configuracion por ambiente

### Local (desarrollo)
Copiar `.env.example` a `.env` y completar valores.

### Fly.io (dev/staging/prod)

**Secrets (valores sensibles):**
```bash
fly secrets set \
  DB_HOST=<your-postgres-app>.internal \
  DB_PORT=5432 \
  DB_USER=postgres \
  DB_PASSWORD=xxx \
  DB_NAME=postgres \
  AUTH0_DOMAIN=<your-tenant>.auth0.com \
  AUTH0_AUDIENCE=https://<your-tenant>.auth0.com/api/v2/ \
  AUTH0_CLIENT_ID=xxx \
  AUTH0_CLIENT_SECRET=xxx \
  CF_R2_ENDPOINT=https://xxx.r2.cloudflarestorage.com \
  CF_R2_ACCESS_KEY_ID=xxx \
  CF_R2_SECRET_ACCESS_KEY=xxx \
  PDFCOMPOSER_URL=http://<your-pdfcomposer-app>.internal:8080 \
  PDFCOMPOSER_API_KEY=xxx \
  NOTARY_URL=http://<your-notary-app>.internal:8080 \
  NOTARY_API_KEY=xxx \
  FRONTEND_URL=https://<your-frontend-domain> \
  AGENTE_URL=http://<your-agent-app>.internal:8080 \
  INTERNAL_API_KEY=xxx
```

**Config (en fly.toml [env]):**
```toml
[env]
  PORT = "8080"
  PYTHONUNBUFFERED = "1"
```

### Notas de conexion Fly.io
- BD interna: `<your-postgres-app>.internal` (red privada Fly.io)
- Microservicios internos: `http://{app-name}.internal:8080`
- Frontend: URL publica de Vercel
