# Notario - Servicio de Firma Digital de PDFs

## Descripción

**Notario** es un microservicio desarrollado en FastAPI para la firma digital automática de documentos PDF. El sistema implementa un layout inteligente de 2 columnas que permite múltiples firmas visuales sin superposición, con estampado opcional de número de documento y nomenclatura inteligente de archivos.

## Características Principales

- ✅ **Firma Digital Profesional**: Sistema de firma digital sin certificados PEM
- ✅ **Layout de 2 Columnas**: Posicionamiento automático de firmas en formato de 2 columnas
- ✅ **Detección de Firmas Existentes**: Evita superposición detectando firmas previas
- ✅ **Estampado de Documentos**: Sello con número de documento, ciudad y fecha en la primera página
- ✅ **Nomenclatura Inteligente**: Nombres de archivo basados en número de documento o nombre original
- ✅ **Posicionamiento Basado en "end-text"**: Detección automática del final del contenido
- ✅ **Validación de Espacio**: Verifica que haya espacio suficiente antes de firmar
- ✅ **API RESTful**: Endpoints simples y bien documentados
- ✅ **Formato Letter**: Optimizado para documentos PDF en formato Letter (612x792 puntos)

## Tecnologías Utilizadas

- **FastAPI**: Framework web moderno y rápido
- **Python 3.11**: Lenguaje de programación
- **Gunicorn**: Servidor WSGI con 3 workers para producción
- **Uvicorn**: Servidor ASGI para desarrollo y como worker class
- **PyPDF2**: Biblioteca para manipulación de documentos PDF
- **PyMuPDF (fitz)**: Análisis y búsqueda de texto en PDFs
- **ReportLab**: Generación de contenido PDF y overlays

## Estructura del Proyecto

```
Notario/
├── app/
│   ├── __init__.py
│   ├── main.py              # Aplicación principal FastAPI
│   ├── config.py            # Configuraciones del sistema
│   ├── layout.py            # Lógica de posicionamiento de firmas
│   ├── signature_inserter.py # Lógica de firma digital
│   ├── document_stamper.py  # Estampado de documentos
│   ├── validators.py        # Validaciones de entrada
│   └── auth.py              # Autenticación API Key
├── certs/
│   └── README.md            # Documentación de certificados (legacy)
├── requirements.txt
├── Dockerfile
├── railway.json
└── README.md
```

## Layout de Firmas

El sistema implementa un layout de 2 columnas para las firmas visuales:

```
Página PDF (612x792 puntos)
┌─────────────────────────────────────┐
│                                     │
│           Contenido del             │
│           documento...              │
│                                     │
│           "end-text"                │ ← Punto de referencia
│                                     │
│  [Firma 1]      [Firma 2]          │ ← Fila 1
│  (x=50)         (x=270)             │
│                                     │
│  [Firma 3]      [Firma 4]          │ ← Fila 2
│  (x=50)         (x=270)             │
│                                     │
│  [Firma 5]      [Firma 6]          │ ← Fila 3
│  (x=50)         (x=270)             │
│                                     │
└─────────────────────────────────────┘
```

### Especificaciones del Layout

- **Columna Izquierda**: x = 50 puntos (firmas impares: 1, 3, 5, 7...)
- **Columna Derecha**: x = 270 puntos (firmas pares: 2, 4, 6, 8...)
- **Ancho de Firma**: 200 puntos
- **Alto de Firma**: 80 puntos
- **Espaciado entre Filas**: 20 puntos
- **Límite Inferior**: y = 100 puntos (margen de seguridad)
- **Offset desde "end-text"**: 100 puntos (configurable)

## Configuración

### Variables de Entorno

```bash
# API Key para autenticación
API_KEY=miapikey

# Configuración de archivos
MAX_PDF_SIZE_MB=10
REQUEST_TIMEOUT=30
```

### Configuración de Layout (config.py)

```python
# Dimensiones de página Letter
LETTER_WIDTH = 612
LETTER_HEIGHT = 792

# Posiciones de firmas
FIRST_SIGNATURE_X = 50      # Columna izquierda
SECOND_SIGNATURE_X = 270    # Columna derecha
SIGNATURE_WIDTH = 200
SIGNATURE_HEIGHT = 80
ROW_SPACING = 20

# Detección de contenido
END_TEXT_SEARCH = "end-text"
SIGNATURE_OFFSET_BELOW = 100
MIN_SIGNATURE_Y = 100
```

## Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/tu-usuario/Notario.git
cd Notario
```

### 2. Crear entorno virtual

```bash
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Configurar variables de entorno

```bash
export API_KEY="tu-api-key-aqui"
```

### 5. Ejecutar el servidor

```bash
# Opción A: Desarrollo con hot-reload (uvicorn)
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# Opción B: Simular producción (gunicorn)
gunicorn app.main:app -c gunicorn_conf.py
```

## ⚙️ Configuración de Gunicorn (Producción)

La aplicación usa **Gunicorn** con las siguientes características:

- **3 workers**: Para procesamiento concurrente de firmas
- **Bind 0.0.0.0**: Compatible con Railway Private Networking (URLs internas)
- **UvicornWorker**: Worker class para compatibilidad con FastAPI async
- **Timeout 90s**: Para operaciones de firma que pueden tomar tiempo
- **Max requests 1000**: Auto-restart de workers para prevenir memory leaks
- **Graceful shutdown**: Timeout de 30s para finalizar requests en progreso

### Variables de entorno opcionales:
```ini
GUNICORN_WORKERS=3           # Número de workers (default: 3)
GUNICORN_TIMEOUT=90          # Timeout por request (default: 90s)
GUNICORN_MAX_REQUESTS=1000   # Max requests por worker antes de restart
LOG_LEVEL=info               # Nivel de logging (debug, info, warning, error)
```

## Uso de la API

### Endpoint de Salud

```bash
GET /health
```

**Respuesta:**
```json
{
  "status": "healthy",
  "service": "Notary",
  "version": "2.1.0",
  "signature_system": {
    "type": "digital_signature",
    "version": "2.1",
    "description": "Sistema de firma digital sin certificados PEM",
    "features": [
      "Firma digital profesional",
      "Layout automático 2 columnas",
      "Posicionamiento inteligente",
      "Información completa del firmante"
    ]
  }
}
```

### Endpoint de Firma

```bash
POST /sign-pdf
```

**Parámetros:**
- `pdf_file` (file): Archivo PDF a firmar
- `name` (string): Nombre del firmante (1-100 caracteres)
- `seal` (string): Cargo o sello del firmante (1-50 caracteres)
- `department` (string): Departamento del firmante (1-100 caracteres)
- `entity` (string): Entidad del firmante (1-100 caracteres)
- `document_number` (string, opcional): Número de documento para estampar
- `city` (string, opcional): Ciudad para el estampado
- `api_key` (header): API Key para autenticación

**Ejemplo con curl:**

```bash
curl -X POST "http://localhost:8000/sign-pdf" \
  -H "X-API-Key: tu-api-key" \
  -F "pdf_file=@documento.pdf" \
  -F "name=Juan Pérez" \
  -F "seal=Director" \
  -F "department=Administración" \
  -F "entity=Mi Empresa S.A." \
  -F "document_number=12345678" \
  -F "city=Bogotá" \
  --output documento_firmado.pdf
```

## Formato de Firma Visual

Cada firma digital incluye:

```
┌─────────────────────────────────────┐
│ Digitally Signed by TEST SERVER      │
│                                     │
│ Juan Pérez                          │
│ Director                            │
│ Administración                      │
│ Mi Empresa S.A.                     │
│                                     │
│ 2024-01-15 14:30:25 UTC            │
└─────────────────────────────────────┘
```

## Validaciones

### Formato PDF
- Solo se aceptan archivos PDF
- Formato Letter (612x792 puntos)
- Tamaño máximo: 10MB

### Parámetros
- `name`: 1-100 caracteres
- `seal`: 1-50 caracteres
- `department`: 1-100 caracteres
- `entity`: 1-100 caracteres
- `document_number`: máximo 40 caracteres
- `city`: requerido si se proporciona document_number

### Espacio Disponible
- Verifica que haya espacio suficiente para la firma
- Mínimo y = 100 puntos desde el borde inferior
- Detecta firmas existentes para evitar superposición

## Códigos de Error

- **400 Bad Request**: Parámetros inválidos o formato PDF incorrecto
- **401 Unauthorized**: API Key inválida o faltante
- **500 Internal Server Error**: Error interno del servidor

## Desarrollo

### Estructura de Módulos

- `main.py`: Aplicación FastAPI principal y endpoints
- `config.py`: Configuraciones y constantes del sistema
- `layout.py`: Algoritmos de posicionamiento de firmas
- `signature_inserter.py`: Lógica de inserción de firmas visuales
- `document_stamper.py`: Funcionalidad de estampado
- `validators.py`: Validaciones de entrada
- `auth.py`: Autenticación por API Key

### Ejecutar en modo desarrollo

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

### Documentación de la API

Una vez ejecutado el servidor, la documentación interactiva está disponible en:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Despliegue

### Docker

```bash
# Construir imagen
docker build -t notario .

# Ejecutar contenedor (Gunicorn con 3 workers)
docker run -p 8001:8000 -e API_KEY=tu-api-key notario

# Ejecutar con configuración personalizada
docker run -p 8001:8000 \
  -e API_KEY=tu-api-key \
  -e GUNICORN_WORKERS=3 \
  -e GUNICORN_TIMEOUT=90 \
  notario
```

### Railway

El proyecto incluye configuración para Railway en `railway.json`.

## Contribuir

1. Fork el proyecto
2. Crea una rama para tu feature (`git checkout -b feature/nueva-funcionalidad`)
3. Commit tus cambios (`git commit -am 'Agregar nueva funcionalidad'`)
4. Push a la rama (`git push origin feature/nueva-funcionalidad`)
5. Crea un Pull Request

## Licencia

Este proyecto está bajo la Licencia MIT. Ver el archivo `LICENSE` para más detalles.

## Changelog

### v2.1.0 (2025-10-27)
- ✅ Gunicorn con 3 workers para producción
- ✅ Bind a 0.0.0.0 para Railway Private Networking
- ✅ Configuración optimizada para Railway
- ✅ Variables de entorno para personalizar Gunicorn

### v2.0.0 (2024-01-15)
- ✅ Migración de firma digital a firma digital
- ✅ Eliminación de dependencias de certificados digitales
- ✅ Actualización de documentación y API
- ✅ Mejoras en la estabilidad del sistema

### v1.0.0 (2024-01-01)
- ✅ Implementación inicial del sistema de firma digital
- ✅ Layout automático de 2 columnas
- ✅ Estampado de documentos
- ✅ API RESTful completa