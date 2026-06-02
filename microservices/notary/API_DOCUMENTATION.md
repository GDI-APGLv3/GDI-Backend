# Documentación de la API - Servicio de Firma Digital de PDFs

## Información General

**URL Base:** `http://localhost:8000`  
**Versión:** 2.0.0  
**Protocolo:** HTTP/HTTPS  
**Formato de datos:** JSON, multipart/form-data  
**Tipo de Firma:** Digital (sin certificados PEM)

## Autenticación

El servicio utiliza autenticación por API Key que debe incluirse en el header de las peticiones:

```http
X-API-Key: tu_api_key_aqui
```

## Endpoints Disponibles

### 1. Health Check

**Endpoint:** `GET /health`  
**Descripción:** Verifica el estado del servicio y proporciona información sobre el sistema de firma digital.

#### Respuesta Exitosa (200)
```json
{
  "status": "healthy",
  "service": "Notary",
  "version": "2.0.0",
  "signature_system": {
    "type": "digital_signature",
    "version": "2.0",
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

#### Ejemplo de uso
```bash
curl -X GET "http://localhost:8000/health" \
  -H "X-API-Key: your-api-key"
```

---

### 2. Firmar PDF Digitalmente

**Endpoint:** `POST /sign-pdf`  
**Descripción:** Aplica una firma digital profesional a un documento PDF con posicionamiento automático y estampado opcional.

#### Parámetros

| Parámetro | Tipo | Requerido | Descripción |
|-----------|------|-----------|-------------|
| `pdf_file` | File | ✅ | Archivo PDF a firmar (máx. 10MB) |
| `name` | String | ✅ | Nombre del firmante (1-100 caracteres) |
| `seal` | String | ✅ | Cargo o sello del firmante (1-50 caracteres) |
| `department` | String | ✅ | Departamento del firmante (1-100 caracteres) |
| `entity` | String | ✅ | Entidad del firmante (1-100 caracteres) |
| `document_number` | String | ❌ | Número del documento para estampado (máx. 40 caracteres) |
| `city` | String | ❌ | Ciudad para el estampado (requerida si se proporciona document_number) |

#### Funcionalidades

1. **Firma Digital Profesional:** Aplica firma digital con información completa del firmante
2. **Layout de 2 Columnas:** Posicionamiento automático en formato de 2 columnas
3. **Detección de Firmas Existentes:** Evita superposición con firmas previas
4. **Estampado Opcional:** Agrega sello con número y fecha si se proporciona `document_number`
5. **Nomenclatura Inteligente:** Nombra el archivo según las reglas establecidas

#### Layout de Firmas

El sistema implementa un layout inteligente de 2 columnas:

```
┌─────────────────────────────────────┐
│           "end-text"                │ ← Punto de referencia
│                                     │
│  [Firma 1]      [Firma 2]          │ ← Fila 1
│  (x=50)         (x=270)             │
│                                     │
│  [Firma 3]      [Firma 4]          │ ← Fila 2
│  (x=50)         (x=270)             │
└─────────────────────────────────────┘
```

**Especificaciones:**
- **Columna Izquierda:** x = 50 puntos (firmas impares: 1, 3, 5...)
- **Columna Derecha:** x = 270 puntos (firmas pares: 2, 4, 6...)
- **Ancho de Firma:** 200 puntos
- **Alto de Firma:** 80 puntos
- **Espaciado entre Filas:** 20 puntos

#### Formato de Firma Digital

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

#### Nomenclatura de Archivos

- **Con número de documento:** `[numero].pdf` (ej: `12345678.pdf`)
- **Sin número de documento:** Mantiene el nombre original del archivo

#### Ejemplos de Uso

##### Firma Digital Simple (sin estampado)
```bash
curl -X POST "http://localhost:8000/sign-pdf" \
  -H "X-API-Key: your-api-key" \
  -F "pdf_file=@documento.pdf" \
  -F "name=Juan Pérez" \
  -F "seal=Director" \
  -F "department=Administración" \
  -F "entity=Mi Empresa S.A."
```

##### Firma Digital con Estampado
```bash
curl -X POST "http://localhost:8000/sign-pdf" \
  -H "X-API-Key: your-api-key" \
  -F "pdf_file=@contrato.pdf" \
  -F "name=María García" \
  -F "seal=Gerente" \
  -F "department=Recursos Humanos" \
  -F "entity=Corporación ABC" \
  -F "document_number=12345678" \
  -F "city=Bogotá"
```

#### Respuestas

##### Éxito (200)
```http
HTTP/1.1 200 OK
Content-Type: application/pdf
Content-Disposition: attachment; filename="12345678.pdf"
Content-Length: [tamaño_del_archivo]

[Contenido binario del PDF con firma digital]
```

##### Error de Validación (400)
```json
{
  "detail": "PDF file too large. Maximum size: 10MB",
  "error_code": "FILE_TOO_LARGE"
}
```

##### Error de Autenticación (401)
```json
{
  "detail": "Invalid API key"
}
```

##### Error de Procesamiento (500)
```json
{
  "detail": "Error processing PDF: [descripción del error]"
}
```

## Códigos de Error

| Código | Descripción |
|--------|-------------|
| `FILE_TOO_LARGE` | Archivo excede el tamaño máximo (10MB) |
| `INVALID_PDF_FILE` | Archivo no es un PDF válido |
| `INVALID_API_KEY` | API Key inválida o faltante |
| `PROCESSING_ERROR` | Error durante el procesamiento del documento |
| `INSUFFICIENT_SPACE` | No hay espacio suficiente para la firma |
| `INVALID_PARAMETERS` | Parámetros de entrada inválidos |

## Algoritmo de Posicionamiento

El servicio utiliza un algoritmo inteligente para posicionar las firmas visuales:

1. **Detección de "end-text":** Busca el texto "end-text" en la última página
2. **Conteo de firmas existentes:** Detecta firmas visuales previas
3. **Cálculo de posición:** Determina la posición en el layout de 2 columnas
4. **Validación de espacio:** Verifica que hay espacio suficiente
5. **Aplicación de firma:** Inserta la firma digital en la posición calculada

### Especificaciones de Layout

- **Tamaño de página:** Letter (612 x 792 puntos)
- **Límite inferior:** y = 100 puntos (margen de seguridad)
- **Offset desde "end-text":** 100 puntos
- **Altura de firma:** 80 puntos
- **Separación entre filas:** 20 puntos

## Configuración del Estampado

### Posicionamiento del Sello
- **Ciudad y fecha:** Y = 675 puntos desde el borde inferior
- **Número de documento:** Y = 660 puntos desde el borde inferior
- **Posición X:** 55.4 puntos desde el borde izquierdo
- **Fuente:** Helvetica 11pt
- **Color:** Negro

### Formato de Fecha
El sello utiliza formato de fecha en español:
- Ejemplo: "15 de enero de 2025"

## Validaciones

### Archivo PDF
- **Formato:** Solo archivos PDF válidos
- **Tamaño máximo:** 10MB
- **Formato de página:** Letter (612x792 puntos)

### Parámetros de Entrada
- **name:** 1-100 caracteres, solo letras, números y espacios
- **seal:** 1-50 caracteres
- **department:** 1-100 caracteres
- **entity:** 1-100 caracteres
- **document_number:** Máximo 40 caracteres (opcional)
- **city:** Requerida si se proporciona document_number

### Espacio Disponible
- Verifica que haya espacio suficiente para la firma
- Mínimo y = 100 puntos desde el borde inferior
- Detecta firmas existentes para evitar superposición

## Límites y Restricciones

- **Tamaño máximo de archivo:** 10MB
- **Formatos soportados:** PDF únicamente
- **Páginas máximas:** Sin límite específico
- **Firmas por documento:** Múltiples (limitado por espacio disponible)
- **Timeout de request:** 30 segundos

## Ejemplos de Integración

### JavaScript (Fetch API)
```javascript
const formData = new FormData();
formData.append('pdf_file', pdfFile);
formData.append('name', 'Juan Pérez');
formData.append('seal', 'Director');
formData.append('department', 'Administración');
formData.append('entity', 'Mi Empresa S.A.');
formData.append('document_number', '12345678');
formData.append('city', 'Bogotá');

fetch('http://localhost:8000/sign-pdf', {
  method: 'POST',
  headers: {
    'X-API-Key': 'your-api-key'
  },
  body: formData
})
.then(response => {
  if (response.ok) {
    return response.blob();
  }
  throw new Error('Error en la firma digital');
})
.then(blob => {
  // Descargar el PDF con firma digital
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = '12345678.pdf';
  a.click();
})
.catch(error => {
  console.error('Error:', error);
});
```

### Python (requests)
```python
import requests

url = "http://localhost:8000/sign-pdf"
headers = {"X-API-Key": "your-api-key"}

with open("documento.pdf", "rb") as pdf_file:
    files = {"pdf_file": pdf_file}
    data = {
        "name": "Juan Pérez",
        "seal": "Director",
        "department": "Administración",
        "entity": "Mi Empresa S.A.",
        "document_number": "12345678",
        "city": "Bogotá"
    }
    
    response = requests.post(url, headers=headers, files=files, data=data)
    
    if response.status_code == 200:
        with open("documento_firmado_visual.pdf", "wb") as output:
            output.write(response.content)
        print("Firma visual aplicada exitosamente")
    else:
        print(f"Error: {response.status_code} - {response.text}")
```

### cURL Avanzado
```bash
# Firma visual con todos los parámetros
curl -X POST "http://localhost:8000/sign-pdf" \
  -H "X-API-Key: your-api-key" \
  -H "Accept: application/pdf" \
  -F "pdf_file=@documento.pdf" \
  -F "name=Juan Pérez González" \
  -F "seal=Director Ejecutivo" \
  -F "department=Dirección General" \
  -F "entity=Corporación Internacional S.A." \
  -F "document_number=DOC-2024-001234" \
  -F "city=Medellín" \
  --output "documento_firmado_visual.pdf" \
  --write-out "Status: %{http_code}\nTime: %{time_total}s\n"
```

## Notas Técnicas

- **Tipo de Firma:** Visual únicamente (no criptográfica)
- **Bibliotecas:** pypdf, PyMuPDF (fitz), ReportLab
- **Sistema de Coordenadas:** PDF estándar (origen en esquina inferior izquierda)
- **Fuentes:** Helvetica (incluida en PDF estándar)
- **Codificación:** UTF-8 para todos los textos
- **Formato de Fecha:** UTC con formato ISO 8601

## Migración desde v1.0.0

### Cambios Principales
- ✅ Eliminación de certificados digitales
- ✅ Cambio de parámetros: `signer_name` → `name`, `reason`/`location` → `seal`/`department`/`entity`
- ✅ Puerto por defecto: 8000 → 8001
- ✅ Nuevo formato de firma digital
- ✅ Mejoras en el algoritmo de posicionamiento

### Guía de Migración
```bash
# Antes (v1.0.0)
curl -F "signer_name=Juan" -F "reason=Firma" -F "location=Bogotá" ...

# Ahora (v2.0.0)
curl -F "name=Juan" -F "seal=Director" -F "department=Admin" -F "entity=Empresa" ...
```

## Soporte y Documentación

- **Documentación Interactiva:** http://localhost:8000/docs (Swagger UI)
- **Documentación Alternativa:** http://localhost:8000/redoc (ReDoc)
- **Health Check:** http://localhost:8000/health
- **Versión de la API:** Incluida en todas las respuestas del health check