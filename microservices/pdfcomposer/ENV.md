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
| API_KEY | Secret | Si | your-api-key-here | API Key autenticacion | Fly Secret |
| PORT | Config | No | 8080 | Puerto del servidor | fly.toml [env] |
| GUNICORN_WORKERS | Config | No | 4 | Numero de workers | fly.toml [env] |
| GUNICORN_TIMEOUT | Config | No | 120 | Timeout en segundos | fly.toml [env] |
| PDF_MAX_CONCURRENCY | Config | No | 2 | `write_pdf` concurrentes por worker (semaforo) | fly.toml [env] |
| PDF_MAX_QUEUE | Config | No | 30 | Requests en vuelo (esperando+corriendo) antes de responder 503 | fly.toml [env] |
| LOG_LEVEL | Config | No | info | Nivel de logging | fly.toml [env] |
| BETTERSTACK_SOURCE_TOKEN | Secret | No | - | Token Better Stack logging | Fly Secret |

## Dimensionamiento de paralelismo (round 3, escalado horizontal a 100 firmas/min)

WeasyPrint es CPU-bound y satura ~1 core por PDF (medido 19/08: sin limite, N
`write_pdf` simultaneos en la misma CPU compiten y degradan a TODOS por
thrashing). Los "slots" totales de composicion por maquina son
`GUNICORN_WORKERS x PDF_MAX_CONCURRENCY` y deben aproximarse al numero de
vCPUs de la VM, no superarlo — pasarse no suma throughput, solo mas
context-switching.

| VM (Fly size) | vCPUs | GUNICORN_WORKERS | PDF_MAX_CONCURRENCY | slots totales |
|---|---|---|---|---|
| shared-cpu-1x | 1 | 1 | 1 | 1 |
| performance-2x | 2 | 2 | 1 | 2 |
| performance-4x | 4 | 2 | 2 | 4 |
| performance-8x | 8 | 4 | 2 | 8 |

`PDF_MAX_QUEUE` es independiente: es backpressure explicito (round 3), no
dimensionamiento de CPU. Si el total de requests en vuelo (esperando cupo de
`PDF_MAX_CONCURRENCY` + corriendo) supera `PDF_MAX_QUEUE`, el servicio
responde 503 de inmediato en vez de dejar que el caller (Backend/escri) espere
hasta su propio timeout (~60s). Default generoso (30) para no interferir con
carga normal; bajarlo en maquinas chicas si se prioriza fail-fast sobre
absorber picos cortos.

## Configuracion por ambiente

### Local (desarrollo)
Copiar `.env.example` a `.env` y completar valores.
```bash
API_KEY=your-api-key-here
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
