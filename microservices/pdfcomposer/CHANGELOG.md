# Changelog

Todos los cambios notables de este proyecto serán documentados en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto adhiere al [Versionado Semántico](https://semver.org/lang/es/).

## [2.2.0] - 2025-10-13

### ⭐ Añadido
- **Gunicorn como servidor de producción**: Migración de Uvicorn standalone a Gunicorn con 4 workers
- **Archivo `gunicorn_conf.py`**: Configuración centralizada y extensible para Gunicorn
- **Soporte IPv6 completo**: Bind a `[::]` para compatibilidad con Railway internal networking
- **Variables de entorno para Gunicorn**: `GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`, `GUNICORN_MAX_REQUESTS`, `LOG_LEVEL`
- **Hooks de lifecycle**: Logging detallado de eventos de workers (start, stop, reload)

### 🔧 Cambiado
- **Dockerfile**: Actualizado para usar `gunicorn` con archivo de configuración
- **Puerto de producción**: Configuración flexible con soporte IPv6 (`[::]:PORT`)
- **main.py**: Clarificación de modo desarrollo vs producción en bloque `__main__`
- **Timeout aumentado**: De 30s a 120s para operaciones de generación de PDF

### 🎨 Mejoras
- **Concurrencia mejorada**: 4 workers para procesamiento paralelo de requests
- **Auto-restart de workers**: Max 1000 requests por worker para prevenir memory leaks
- **Graceful shutdown**: Timeout de 30s para finalizar requests en progreso
- **Logs mejorados**: Access logs y error logs a stdout/stderr para Railway

### 📚 Documentación
- **README.md**: Sección nueva sobre configuración de Gunicorn
- **DEPLOYMENT.md**: Actualizado con variables de entorno opcionales y notas sobre IPv6
- **Ejemplos de ejecución**: Agregadas opciones para desarrollo y producción local

## [2.1.0] - 2025-10-01

### ⭐ Añadido
- **Nuevo endpoint `/generate-pdf/`**: Servicio que utiliza plantilla `generate-pdf.html` para documentos finales
- **Marca de agua en preview**: Texto "PREVISUALIZACIÓN" diagonal en `plantilla.html`
- **Campos blancos en generate-pdf**: Ciudad/fecha y número en color blanco para completar manualmente
- **Función `generate_general_pdf()`**: Nueva función en `pdf_service.py` para documentos finales
- **Configuración Railway optimizada**: Networking interno con `gotenberg.railway.internal`
- **Archivo `.env.example`**: Plantilla de variables de entorno para Railway
- **Campo `NameAcronyType`**: Nuevo campo requerido en `/preview-pdf/` y `/generate-pdf/` para acrónimo organizacional
- **Campo `acrony_case_type`**: Nuevo campo en `/create-case/` para acrónimo del tipo de expediente

### 🔧 Cambiado
- **Modelos Pydantic**: Migración de `HttpUrl` a `str` para mayor flexibilidad
- **Puerto desarrollo**: Cambiado de 8000 a 8002 para testing local
- **Puerto producción**: Dockerfile usa puerto 8000 fijo (Railway maneja mapping dinámico)
- **URL Gotenberg**: Configurada para Railway internal networking
- **Versión FastAPI**: Actualizada a 2.1.0
- **Campo `Text`**: Cambiado de string a JSON (`Dict[str, Any]`) para contenido flexible HTML
- **Datetime**: Actualizado de `datetime.utcnow()` (deprecated) a `datetime.now(timezone.utc)`

### 🎨 Mejoras UI/UX
- **Template preview**: Marca de agua semitransparente para identificar vistas previas
- **Template final**: Campos preparados para completar manualmente
- **Documentación**: README completamente reorganizado y actualizado
- **Headers consistentes**: Alineación de estructura de cabecera entre todas las plantillas HTML

### 🐛 Corregido
- **Validación tipos**: Eliminados errores HttpUrl en modelos Pydantic
- **Compatibilidad Railway**: Optimizada integración con servicios internos (puerto fijo en Dockerfile)
- **Gestión imágenes**: Mejorada conversión base64 sin conversiones innecesarias
- **CSS duplicado**: Eliminada clase `.divider` duplicada en todas las plantillas
- **Renderizado JSON**: Corregida extracción del campo `html` del objeto JSON en campo `Text`

### 📚 Documentación
- **README.md**: Completamente reescrito con enfoque en funcionalidades actuales
- **Eliminación archivos obsoletos**: Limpieza de documentación desactualizada
- **Ejemplos actualizados**: Todos los curl examples con puerto 8002 y configuración actual

## [2.0.0] - 2025-09-30

### ⭐ Añadido
- **Endpoint `/create-case/`**: Generación de carátulas de expedientes
- **Plantilla `caratula.html`**: Template específico para carátulas administrativas
- **Modelo `CaseRequest`**: Validación Pydantic para datos de expedientes
- **Función `generate_case_pdf()`**: Lógica específica para carátulas
- **Fecha automática**: Generación automática de timestamp UTC
- **Nombres únicos**: Sistema UUID para archivos PDF

### 🔧 Cambiado
- **Arquitectura interna**: Refactorización completa usando modelos Pydantic
- **Estructura proyecto**: Organización mejorada con separación clara de responsabilidades
- **Validación datos**: Sistema robusto de validación con Pydantic

## [1.0.0] - 2025-09-29

### ⭐ Añadido
- **Endpoint `/preview-pdf/`**: Funcionalidad inicial de generación PDF
- **Plantilla `plantilla.html`**: Template base para documentos
- **Integración Gotenberg**: Conversión HTML a PDF
- **Autenticación API Key**: Sistema de protección con X-API-Key
- **Soporte logos**: Integración dinámica de imágenes desde URL
- **Docker support**: Containerización para deployment
- **FastAPI framework**: API REST con documentación automática

---

**Repositorio**: https://github.com/GDI-APGLv3/GDI-Backend
- **Guía de despliegue**: Instrucciones específicas para Railway

## [1.0.0] - 2025-09-15

### ⭐ Añadido
- **Endpoint `/preview-pdf/`**: Funcionalidad básica de generación de PDF
- **Plantilla `plantilla.html`**: Plantilla HTML base
- **Integración Gotenberg**: Conexión con servicio de renderizado
- **Autenticación API Key**: Sistema básico de autenticación
- **Dockerfile**: Configuración para contenedorización
- **Modelo `PDFRequest`**: Validación básica de datos

### 🏗️ Infraestructura
- **FastAPI**: Framework web base
- **Docker**: Soporte para contenedores
- **Railway**: Configuración para despliegue

---

## Tipos de Cambios

- `⭐ Añadido` para nuevas funcionalidades
- `🔧 Cambiado` para cambios en funcionalidades existentes
- `🗑️ Eliminado` para funcionalidades removidas
- `🐛 Corregido` para corrección de errores
- `🔒 Seguridad` en caso de vulnerabilidades
- `📚 Documentación` para cambios en documentación
- `🏗️ Infraestructura` para cambios en la infraestructura del proyecto