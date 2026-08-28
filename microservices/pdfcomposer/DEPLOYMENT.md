# Guía de Deployment - PDF Composer API

## Fly.io Deployment

### Apps

| Ambiente | App Name | Config |
|----------|----------|--------|
| DEV | `<your-pdfcomposer-app>` | `fly.toml` |
| PRD | `<your-pdfcomposer-prd-app>` | `fly.prd.toml` |

WeasyPrint corre dentro del mismo contenedor. No se necesita un servicio externo de PDF.

### Paso 1: Configurar Secrets
```bash
# DEV
flyctl secrets set API_KEY=your-api-key -a <your-pdfcomposer-app>

# PRD
flyctl secrets set API_KEY=your-api-key -a <your-pdfcomposer-prd-app>
```

### Paso 2: Deploy
```bash
# DEV (usa fly.toml por defecto)
flyctl deploy -a <your-pdfcomposer-app>

# PRD (usa fly.prd.toml)
flyctl deploy -a <your-pdfcomposer-prd-app> --config fly.prd.toml
```

En la práctica, el deploy se hace via GitHub Actions:
- Push a `dev` → deploy automático a `<your-pdfcomposer-app>`
- Push a `prd` → deploy automático a `<your-pdfcomposer-prd-app>`

### Paso 3: Verificar Deployment
```bash
# Ver status
flyctl status -a <your-pdfcomposer-app>

# Ver logs
flyctl logs -a <your-pdfcomposer-app>

# Health check (la app es interna, usar flyctl proxy o llamar desde otro servicio Fly.io)
flyctl proxy 8002:8080 -a <your-pdfcomposer-app>
curl http://localhost:8002/health
```

### Variables de Entorno

**Secrets (flyctl secrets set):**
```bash
API_KEY=your-secure-api-key
```

**Config (en fly.toml [env]):**
```toml
[env]
  PORT = "8080"
```

**Opcionales (en fly.toml [env]):**
```toml
GUNICORN_WORKERS = "4"
GUNICORN_TIMEOUT = "120"
GUNICORN_MAX_REQUESTS = "1000"
LOG_LEVEL = "info"
```

## Local Development

### Requisitos
- Python 3.13+

### Setup Local
```bash
# 1. Clonar repositorio
git clone https://github.com/GDI-AGPLv3/GDI-Backend
cd PDFCOMPOSER

# 2. Instalar dependencias (incluye WeasyPrint)
pip install -r requirements.txt

# 3. Configurar entorno local
echo "API_KEY=your-api-key-here" > .env

# 4. Ejecutar API
# Opción A: Desarrollo con hot-reload
python main.py

# Opción B: Simular producción con Gunicorn
gunicorn main:app -c gunicorn_conf.py
```

### Verificar Local
```bash
# API disponible en:
http://localhost:8002
http://localhost:8002/docs
```

## Docker Deployment

### Build y Run
```bash
# Build imagen
docker build -t pdfcomposer .

# Run container
docker run -p 8002:8080 \
  -e API_KEY=your-api-key-here \
  -e PORT=8080 \
  pdfcomposer
```

## Health Checks

### Endpoint de Salud
```bash
# Verificar que la API responde
curl http://localhost:8002/health

# Test endpoint con autenticación
curl -X POST "http://localhost:8002/preview-pdf/" \
  -H "X-API-Key: your-api-key-here" \
  -F "TypeDocument=TEST" \
  -F "Reference=TEST-001" \
  -F "Text=Test content"
```

### Logs Fly.io
```bash
flyctl logs -a <your-pdfcomposer-app>
flyctl logs -a <your-pdfcomposer-prd-app>
```

## Seguridad

### Variables Sensibles
- **API_KEY**: Configurar como Fly.io secret, cambiar valor por defecto en producción
- **Logs**: No exponer datos sensibles en logs

### Configuración Recomendada
```ini
# Producción (Fly.io secrets)
API_KEY=your-secure-api-key-here

# Desarrollo (.env)
API_KEY=your-api-key-here
```

---

**Documentación**: `/docs` endpoint  
**Repositorio**: https://github.com/GDI-AGPLv3/GDI-Backend
