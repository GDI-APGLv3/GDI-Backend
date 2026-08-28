# GDI Public PDF Router

Cloudflare Worker que sirve los PDFs publicos de TODOS los municipios bajo una
unica URL estable (GDI-098 D10). Es la pieza que permite que `pdf_url` funcione
con multiples tenants por ambiente: el backend arma
`{PUBLIC_PDF_BASE_URL}/{numero_oficial}.pdf` y este Worker resuelve a que
bucket ir leyendo el acronimo del municipio del propio numero
(4to segmento: `NORPU-2026-00002312-TXST-INNO.pdf` -> `gdi-txst-publico`).

Acceso a R2 via S3 API firmada (aws4fetch) con un token **read-only a nivel
cuenta**: los municipios nuevos funcionan sin tocar el Worker (no hay bindings
estaticos por bucket).

## Cuentas Cloudflare (OJO: son 2)

| Cuenta | Ambientes | Estado |
|--------|-----------|--------|
| "DEV, DEMO y HML" (<your-cf-account>, `<YOUR_CF_ACCOUNT_ID>`) | DEV + HML + DEMO | ✅ Deployado 2026-07-17: `https://gdi-public-pdf.<your-cf-account>.workers.dev` (config: `wrangler.toml`) |
| `<your-cf-account-prd>` (`<YOUR_CF_ACCOUNT_ID>`) | PRD | ✅ Deployado 2026-07-17 con custom domain `https://public.your-domain.com` (config: copia de `wrangler.toml` con su account_id) |

DEMO vive en la cuenta de no-produccion, asi que `<your-gateway-app>` usa el Worker
`gdi-public-pdf.<your-cf-account>.workers.dev` (via `PUBLIC_PDF_BASE_URL`).
Solo PRD usa el dominio `public.your-domain.com` (que ademas es el default
hardcodeado de `build_public_pdf_url`, asi que `<your-gateway-app>` no necesita
setear `PUBLIC_PDF_BASE_URL`).

## Deploy

```bash
cd workers/public-pdf
npm install
npx wrangler login          # cuenta correspondiente al ambiente

# Cuenta de no-produccion (DEV/HML/DEMO):
npx wrangler deploy                        # usa wrangler.toml
# Cuenta PRD:
npx wrangler deploy                        # copia wrangler.toml y ajusta account_id/dominio

# Secrets (una vez por cuenta; token R2 "Object Read only" sobre todos los buckets,
# se crea en dash.cloudflare.com -> R2 -> Manage API Tokens -> Account API token).
# Para otro ambiente, apunta wrangler a la cuenta correspondiente:
echo "<ACCESS_KEY_ID>"     | npx wrangler secret put R2_ACCESS_KEY_ID
echo "<SECRET_ACCESS_KEY>" | npx wrangler secret put R2_SECRET_ACCESS_KEY
```

## Conexion con el backend

Cada Gateway apunta su secret al Worker de su cuenta:

```bash
flyctl secrets set PUBLIC_PDF_BASE_URL=https://gdi-public-pdf.<your-cf-account>.workers.dev -a <your-gateway-app>
```

(`build_public_pdf_url` en `api_gateway/public_info/sanitize.py` es el unico
punto del backend que arma estas URLs.)

## Verificacion rapida

```bash
# PDF publico real -> 200 application/pdf
curl -sI https://gdi-public-pdf.<your-cf-account>.workers.dev/NORPU-2026-00002312-TXST-INNO.pdf
# Inexistente o ruta invalida -> 404
```
