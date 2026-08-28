# Documento de Requerimientos - PDF Composer API V2.1.0

## Descripción del Proyecto
Sistema generador de PDF basado en FastAPI que permite crear tres tipos de documentos PDF a partir de plantillas HTML especializadas, utilizando WeasyPrint como motor de renderizado. Diseñado para despliegue en Fly.io.

## Arquitectura Actual
- **Framework**: FastAPI 2.1.0 con Python 3.13
- **Motor PDF**: WeasyPrint (integrado en el mismo contenedor)
- **Templates**: Jinja2 con tres plantillas especializadas
- **Autenticación**: API Key configurable
- **Deployment**: Fly.io con Docker containerización
- **Puerto**: 8002 para desarrollo, 8080 en Fly.io

## Requerimientos Funcionales

### 1. Servicio Preview PDF (Vista Previa)
- **Endpoint**: `/preview-pdf/`
- **Método**: POST  
- **Template**: `plantilla.html`
- **Característica especial**: Marca de agua "PREVISUALIZACIÓN" diagonal
- **Uso**: Documentos para revisión y aprobación

### 2. Servicio Generate PDF (Documentos Finales)
- **Endpoint**: `/generate-pdf/`
- **Método**: POST
- **Template**: `generate-pdf.html`  
- **Característica especial**: Campos fecha/número en blanco para completar
- **Uso**: Documentos oficiales finales

### 3. Servicio Create Case (Carátulas)
- **Endpoint**: `/create-case/`
- **Método**: POST
- **Template**: `caratula.html`
- **Característica especial**: Fecha automática UTC
- **Uso**: Carátulas de expedientes administrativos
## Especificaciones de Endpoints

### Todos los endpoints requieren:
- **Header**: `X-API-Key: your-api-key-here`
- **Content-Type**: `multipart/form-data`
- **Response**: PDF file with UUID filename

### Parámetros comunes (form-data):
- `urlLogo` (opcional): URL del logo a incluir
- `TypeDocument`: Tipo de documento
- `Reference`: Referencia del documento  
- `Text`: Contenido principal del documento

### Parámetros específicos de `/create-case/`:
- `case_number`: Número de expediente
- `case_type`: Tipo de expediente
- `case_motive`: Motivo del expediente
- `initiating_division`: Repartición iniciadora
- `creator`: Nombre del caratulador

## Requerimientos Técnicos

### Configuración de Entorno
```ini
API_KEY=your-api-key-here
```

### Arquitectura de Templates
1. **`plantilla.html`**: Preview con marca de agua diagonal "PREVISUALIZACIÓN"
2. **`generate-pdf.html`**: Documentos finales con campos blancos para completar
3. **`caratula.html`**: Carátulas con fecha automática UTC

### Validación de Datos
- Modelos Pydantic con tipos `str` para máxima flexibilidad
- Validación automática de todos los campos requeridos
- Respuesta con headers `X-Position-Y` para coordenadas de firma

## Requerimientos No Funcionales

### Seguridad
- Autenticación obligatoria con API Key configurable
- Validación robusta de datos de entrada
- Sin almacenamiento persistente de documentos

### Performance  
- Generación bajo demanda sin cacheo
- Nombres UUID únicos para evitar colisiones
- WeasyPrint integrado en el contenedor (sin latencia de red a servicio externo)

### Deployment
- Docker containerización completa
- Variables de entorno para configuración
- Puerto 8002 para desarrollo local
- Fly.io ready con fly.toml

## Stack Tecnológico Actual

### Backend
- **FastAPI 2.1.0**: Framework web async
- **Python 3.13**: Runtime environment
- **Pydantic**: Validación de datos con tipos flexibles
- **Jinja2**: Motor de templates HTML

### Motor PDF
- **WeasyPrint**: Conversión HTML → PDF (corre en el mismo contenedor)
- **PyMuPDF**: Manipulación de PDFs

### Infraestructura
- **Fly.io**: Platform as a Service
- **Docker**: Containerización
- **GitHub**: Control de versiones
- **GitHub Actions**: CI/CD

## Criterios de Aceptación V2.1.0

**Funcionalidad Core**
- Tres endpoints operativos con diferentes propósitos
- Marca de agua visible en documentos preview
- Campos configurables en documentos finales
- Carátulas con fecha automática

**Integración**
- WeasyPrint generando PDFs correctamente
- API Key authentication funcionando
- Templates renderizando correctamente
- UUIDs únicos para todos los archivos

**Deployment**
- Containerización Docker completa
- Variables de entorno configuradas
- Puerto 8002 para desarrollo
- Fly.io deployment ready

**Documentación**
- README actualizado y completo
- CHANGELOG con historial de versiones
- Ejemplos de uso actualizados
- Sin referencias a versiones obsoletas

---

**Repositorio**: https://github.com/GDI-AGPLv3/GDI-Backend  
**Versión Actual**: 2.1.0  
**Estado**: Producción Ready
- El PDF incluye el logo, tipo de documento, referencia y texto proporcionados.
- La autenticación con API Key funciona como se espera.
- El `Dockerfile` permite construir una imagen funcional de la aplicación.
- La documentación (`README.md`) refleja con precisión el funcionamiento y despliegue de la API.
