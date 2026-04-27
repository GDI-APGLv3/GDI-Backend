# PDF Composer API - V2.2.0

Servicio de generación de PDF basado en FastAPI que usa [WeasyPrint](https://weasyprint.org/) para crear documentos PDF a partir de plantillas HTML. Diseñado para ser desplegado en Fly.io.

## Características Principales

- **Tres tipos de documentos**: Vista previa (con marca de agua), Documentos finales y Carátulas de expedientes
- **Autenticación API Key**: Protección mediante clave de API configurable
- **Plantillas personalizables**: Sistema de templates HTML con Jinja2
- **Logos dinámicos**: Soporte para imágenes desde URL
- **Archivos únicos**: Nombres UUID para evitar colisiones
- **WeasyPrint integrado**: Motor PDF corre en el mismo contenedor, sin servicios externos
- **Documentación automática**: FastAPI docs en `/docs`

## Endpoints Disponibles

### 1. `/preview-pdf/` - Vista Previa con Marca de Agua
Genera PDF de vista previa con marca de agua "PREVISUALIZACIÓN" diagonal.

**Template**: `plantilla.html`  
**Marca de agua**: Texto diagonal semitransparente  
**Uso**: Documentos para revisión y aprobación

### 2. `/generate-pdf/` - Documentos Finales  
Genera PDF final para producción sin marca de agua.

**Template**: `generate-pdf.html`  
**Características**: Campos de fecha/número en blanco para completar manualmente  
**Uso**: Documentos oficiales listos para firma

### 3. `/create-case/` - Carátulas de Expedientes
Genera carátulas estructuradas para expedientes administrativos.

**Template**: `caratula.html`  
**Fecha automática**: Genera timestamp UTC  
**Uso**: Caratulación de expedientes administrativos

## Configuración

### Variables de Entorno (.env)
```ini
API_KEY=miapikey
```

### Puerto de Desarrollo
- **Local**: `8002` (configurado para testing)
- **Fly.io**: `8080` (puerto interno)

## Uso de la API

### Headers Requeridos
```
X-API-Key: miapikey
```

### Estructura de Request (form-data)
```
urlLogo: https://www.grupogdi.com/assets/img/logo-gdi-2.png (opcional)
TypeDocument: TIPO DE DOCUMENTO
Reference: REF-001-2025
Text: Contenido del documento...
```

### Ejemplo con curl
```bash
curl -X POST "http://localhost:8002/generate-pdf/" \
     -H "X-API-Key: miapikey" \
     -F "urlLogo=https://www.grupogdi.com/assets/img/logo-gdi-2.png" \
     -F "TypeDocument=DOCUMENTO TÉCNICO" \
     -F "Reference=REF-GEN-001-2025" \
     -F "Text=Contenido del documento." \
     --output documento.pdf
```

### Response Headers
```
X-Position-Y: 123 (coordenada Y del ancla de firma)
Content-Type: application/pdf
```

## Estructura del Proyecto

```
PDFCOMPOSER/
├── app/
│   ├── models/
│   │   └── pdf_models.py      # Modelos Pydantic
│   ├── services/
│   │   └── pdf_service.py     # Lógica de negocio
│   └── templates/
│       ├── plantilla.html     # Preview con marca de agua
│       ├── generate-pdf.html  # Documentos finales
│       └── caratula.html      # Carátulas de expedientes
├── main.py                    # FastAPI app
├── requirements.txt           # Dependencias Python
├── Dockerfile                 # Containerización
├── fly.toml                   # Configuración Fly.io (DEV)
├── fly.prd.toml               # Configuración Fly.io (PRD)
├── .env                       # Variables de entorno
└── README.md                  # Este archivo
```

## Deployment en Fly.io

1. **Apps**:
   - DEV: `<your-pdfcomposer-app>`
   - PRD: `<your-pdfcomposer-prd-app>` (compartida entre clientes)
2. **Secrets**:
   ```bash
   flyctl secrets set API_KEY=your-secure-api-key -a <your-pdfcomposer-app>
   ```
3. **Deploy**: GitHub Actions desde rama `dev` (DEV) y `prd` (PRD)
4. **WeasyPrint**: Corre dentro del mismo contenedor, no necesita servicio externo

## Testing Local

```bash
# Instalar dependencias
pip install -r requirements.txt

# Ejecutar API (desarrollo con hot-reload)
python main.py

# O ejecutar con Gunicorn (simular producción)
gunicorn main:app -c gunicorn_conf.py

# API disponible en:
http://localhost:8002
http://localhost:8002/docs  # Documentación interactiva
```

## Configuración de Gunicorn (Producción)

La aplicación usa **Gunicorn** con las siguientes características:

- **4 workers**: Para procesamiento concurrente de requests
- **UvicornWorker**: Worker class para compatibilidad con FastAPI async
- **Timeout 120s**: Para operaciones de generación de PDF que pueden tomar tiempo
- **Max requests 1000**: Auto-restart de workers para prevenir memory leaks
- **Graceful shutdown**: Timeout de 30s para finalizar requests en progreso

### Variables de entorno opcionales:
```ini
GUNICORN_WORKERS=4           # Número de workers (default: 4)
GUNICORN_TIMEOUT=120         # Timeout por request (default: 120s)
GUNICORN_MAX_REQUESTS=1000   # Max requests por worker antes de restart
LOG_LEVEL=info               # Nivel de logging (debug, info, warning, error)
```

## Tecnologías

- **FastAPI 2.1.0**: Framework web async
- **Python 3.13**: Runtime environment
- **Gunicorn**: Servidor WSGI con 4 workers para producción
- **Uvicorn**: Servidor ASGI para desarrollo
- **WeasyPrint**: Motor de conversión HTML→PDF (integrado en el contenedor)
- **PyMuPDF**: Manipulación de PDFs
- **Pydantic**: Validación de datos
- **Jinja2**: Motor de templates
- **Docker**: Containerización
- **Fly.io**: Platform as a Service

## Versionado

- **V2.2.0 (Actual)**: Gunicorn con 4 workers, optimizaciones de concurrencia
- **V2.1.0**: Nuevo endpoint generate-pdf, optimizaciones
- **V2.0.0**: Endpoint create-case, refactorización Pydantic
- **V1.x**: Versión inicial con preview-pdf

---

**Repositorio**: https://github.com/GDI-APGLv3/GDI-Backend  
**Documentación**: `/docs` endpoint para API interactiva
