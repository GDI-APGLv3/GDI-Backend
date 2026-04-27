# Changelog

Todas las correcciones y mejoras notables de este proyecto serán documentadas en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto adhiere al [Versionado Semántico](https://semver.org/lang/es/).

## [2.1.0] - 2025-10-27

### ⭐ Añadido
- **Gunicorn como servidor de producción**: Migración de Uvicorn standalone a Gunicorn con 3 workers
- **Archivo `gunicorn_conf.py`**: Configuración centralizada y extensible para Gunicorn
- **Railway Private Networking**: Bind a `0.0.0.0` para compatibilidad con networking interno de Railway
- **Variables de entorno para Gunicorn**: `GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`, `GUNICORN_MAX_REQUESTS`, `LOG_LEVEL`
- **Hooks de lifecycle**: Logging detallado de eventos de workers (start, stop, reload)

### 🔧 Cambiado
- **Dockerfile**: Actualizado para usar `gunicorn` con archivo de configuración
- **Comando de inicio**: De `uvicorn` a `gunicorn app.main:app -c gunicorn_conf.py`
- **requirements.txt**: Agregado `gunicorn==21.2.0`
- **Timeout**: Configurado a 90s para operaciones de firma de PDF

### 🎨 Mejoras
- **Concurrencia mejorada**: 3 workers para procesamiento paralelo de firmas
- **Auto-restart de workers**: Max 1000 requests por worker para prevenir memory leaks
- **Graceful shutdown**: Timeout de 30s para finalizar requests en progreso
- **Logs mejorados**: Access logs y error logs a stdout/stderr para Railway
- **Compatibilidad interna**: Soporte completo para URLs internas de Railway (`*.railway.internal`)

### 📚 Documentación
- **README.md**: Sección nueva sobre configuración de Gunicorn
- **README.md**: Actualizada sección de despliegue con Docker
- **CHANGELOG.md**: Actualizado con nueva versión 2.1.0
- **Ejemplos de ejecución**: Agregadas opciones para desarrollo y producción local

## [2.0.0] - 2025-01-15

### 🎉 Agregado

- **Sistema de Firma Visual Profesional**: Implementación completa de firma visual sin certificados digitales
- **Layout de 2 Columnas Mejorado**: Algoritmo optimizado para posicionamiento automático de firmas
- **Detección Avanzada de Firmas**: Sistema mejorado para detectar firmas existentes y evitar superposición
- **Información Completa del Firmante**: Campos expandidos para `name`, `seal`, `department`, y `entity`
- **Validaciones Mejoradas**: Sistema robusto de validación de parámetros y espacio disponible
- **Documentación Interactiva**: Swagger UI y ReDoc disponibles en `/docs` y `/redoc`

### 📝 Cambiado

- **BREAKING CHANGE**: Migración completa de firma digital a firma visual
- **Puerto por Defecto**: Cambiado de `8000` a `8001`
- **Parámetros de API**: 
  - `signer_name` → `name`
  - `reason` → `seal` (cargo del firmante)
  - `location` → `department` + `entity`
- **Formato de Firma**: Nuevo diseño visual profesional con información completa
- **Título del Servicio**: "Digital PDF Signing Service" → "Visual PDF Signing Service"
- **Versión del Servicio**: 1.0.0 → 2.0.0
- **Texto de Detección**: "Digitally Signed by" → "Visually Signed by"
- **Identificador de Firma**: "Digitally Signed by TEST SERVER" → "Visually Signed by TEST SERVER"

### 🗑️ Removido

- **Certificados Digitales**: Eliminación completa del sistema de certificados PEM
- **Configuraciones de Certificados**: Removidas `CERT_PATH` y `KEY_PATH` de `config.py`
- **Validación de Certificados**: Función `verify_certificate_availability` eliminada
- **Dependencias Criptográficas**: Removidas dependencias relacionadas con firma digital
- **Archivos de Certificados**: Eliminados `signing_cert.pem` y `signing_key.pem`
- **PDFs de Prueba**: Removidos archivos de test del repositorio

### 🐛 Corregido

- **Estabilidad del Sistema**: Eliminación de errores relacionados con certificados faltantes
- **Validación de Parámetros**: Mejoras en la validación de entrada de datos
- **Manejo de Errores**: Sistema más robusto de manejo de excepciones
- **Posicionamiento de Firmas**: Algoritmo mejorado para cálculo de posiciones

### 🔒 Seguridad

- **Validación de Archivos**: Verificación mejorada de tipos de archivo PDF
- **Sanitización de Entrada**: Validación robusta de parámetros de entrada
- **Límites de Tamaño**: Mantenimiento de límites de 10MB para archivos PDF

### 📚 Documentación

- **README.md**: Reescritura completa para reflejar el sistema de firma visual
- **API_DOCUMENTATION.md**: Actualización integral con nuevos endpoints y parámetros
- **Ejemplos de Uso**: Nuevos ejemplos con cURL, JavaScript y Python
- **Guía de Migración**: Documentación para migrar desde v1.0.0 a v2.0.0
- **Especificaciones Técnicas**: Detalles del nuevo formato de firma visual

### 🧹 Limpieza

- **Estructura de Archivos**: Limpieza del repositorio eliminando archivos innecesarios
- **`.gitignore`**: Actualización para excluir archivos de prueba y certificados
- **Código Legacy**: Eliminación de código relacionado con certificados digitales
- **Imports**: Limpieza de importaciones no utilizadas

### ✅ Estado del Sistema v2.0.0

- ✅ Servidor funcionando correctamente en `http://localhost:8001`
- ✅ Endpoint `/sign-pdf` operativo con nuevos parámetros
- ✅ Sistema de firma visual completamente funcional
- ✅ Layout de 2 columnas optimizado
- ✅ Detección de firmas existentes mejorada
- ✅ Validaciones de espacio y parámetros activas
- ✅ Documentación interactiva disponible
- ✅ Sistema sin dependencias de certificados digitales

---

## [1.0.1] - 2025-01-01

### 🐛 Correcciones

- **CRÍTICO**: Corregido error "name 'logger' is not defined" en `app/layout.py`
  - Agregada importación faltante: `import logging`
  - Configurado logger del módulo: `logger = logging.getLogger(__name__)`
  - El error impedía el funcionamiento del endpoint `/sign-pdf`

### 📚 Documentación

- Actualizado `README.md` con sección de troubleshooting
- Agregada documentación del error del logger y su solución
- Mejorados comentarios en `app/layout.py`:
  - Documentación más detallada del logger
  - Explicación mejorada de la función `count_existing_signatures`
  - Detalles del algoritmo de detección de firmas

### 🧹 Limpieza

- Eliminados archivos de caché de Python (`__pycache__`)
- Mantenimiento general del código

### ✅ Estado del Sistema v1.0.1

- ✅ Servidor funcionando correctamente en `http://localhost:8000`
- ✅ Endpoint `/sign-pdf` operativo
- ✅ Sistema de detección de firmas funcionando
- ✅ Layout de 2 columnas implementado
- ✅ Validaciones de espacio activas

## [1.0.0] - 2024-12-XX

### 🎉 Lanzamiento Inicial

- Implementación completa del microservicio de firma digital
- Sistema de layout automático de 2 columnas
- Detección de firmas existentes para evitar superposiciones
- API RESTful con FastAPI
- Soporte para certificados PEM
- Validaciones de seguridad y espacio
- Documentación completa

---

### Tipos de Cambios

- `🎉 Agregado` para nuevas funcionalidades
- `📝 Cambiado` para cambios en funcionalidades existentes
- `❌ Deprecado` para funcionalidades que serán removidas
- `🗑️ Removido` para funcionalidades removidas
- `🐛 Corregido` para corrección de errores
- `🔒 Seguridad` para vulnerabilidades corregidas
- `📚 Documentación` para cambios en documentación
- `🧹 Limpieza` para mantenimiento y limpieza de código

### Guía de Migración v1.0.0 → v2.0.0

#### Cambios en la API

**Antes (v1.0.0):**
```bash
curl -X POST "http://localhost:8000/sign-pdf" \
  -F "signer_name=Juan Pérez" \
  -F "reason=Firma de contrato" \
  -F "location=Bogotá, Colombia"
```

**Ahora (v2.0.0):**
```bash
curl -X POST "http://localhost:8001/sign-pdf" \
  -F "name=Juan Pérez" \
  -F "seal=Director" \
  -F "department=Administración" \
  -F "entity=Mi Empresa S.A."
```

#### Cambios en el Health Check

**Antes (v1.0.0):**
```json
{
  "certificate_status": {
    "certificate_available": true,
    "ready_to_sign": true
  }
}
```

**Ahora (v2.0.0):**
```json
{
  "signature_system": {
    "type": "visual_signature",
    "description": "Sistema de firma visual sin certificados digitales"
  }
}
```