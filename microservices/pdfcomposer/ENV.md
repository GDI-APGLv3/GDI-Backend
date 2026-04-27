# Variables de Entorno - GDI-PDFComposer

> Ultima actualizacion: 2026-04-06

## Archivos de configuracion

| Archivo | Proposito | En Git? |
|---------|-----------|---------|
| `.env` | Desarrollo local | NO (.gitignore) |
| `.env.example` | Template referencia | SI |
| `ENV.md` | Documentacion | SI |

## Inventario completo

| Variable | Tipo | Requerida | Default | Descripcion | Donde configurar |
|----------|------|-----------|---------|-------------|-----------------|
| API_KEY | Secret | Si | miapikey | API Key autenticacion | Fly Secret |
| PORT | Config | No | 8080 | Puerto del servidor | fly.toml [env] |
| GUNICORN_WORKERS | Config | No | 4 | Numero de workers | fly.toml [env] |
| GUNICORN_TIMEOUT | Config | No | 120 | Timeout en segundos | fly.toml [env] |
| LOG_LEVEL | Config | No | info | Nivel de logging | fly.toml [env] |
| BETTERSTACK_SOURCE_TOKEN | Secret | No | - | Token Better Stack logging | Fly Secret |

## Configuracion por ambiente

### Local (desarrollo)
Copiar `.env.example` a `.env` y completar valores.
```bash
API_KEY=miapikey
```

### Fly.io (dev/staging/prod)

**Secrets:**
```bash
flyctl secrets set API_KEY=xxx -a <your-pdfcomposer-app>
```

**Config (en fly.toml [env]):**
```toml
[env]
  PORT = "8080"
```

### Notas
- WeasyPrint corre dentro del mismo contenedor, no necesita URL de servicio externo
- Stateless: no usa BD
- Puerto local: 8002 (desarrollo), puerto Fly.io: 8080 (interno)
