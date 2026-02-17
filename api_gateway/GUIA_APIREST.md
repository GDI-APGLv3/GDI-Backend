# Guia REST API - GDI MCP Server

## Resumen

REST API para integraciones externas con el sistema GDI (Gestion Documental Inteligente).

**Base URL**:
- Produccion: `https://mcp.gdilatam.com/api/v1`
- Local: `http://localhost:8005/api/v1`

**Autenticacion**: API Key via header `X-API-Key`

---

## 1. Autenticacion

### Headers Requeridos

| Header | Descripcion | Requerido |
|--------|-------------|-----------|
| `X-API-Key` | API Key del tenant | Si |
| `X-User-ID` | UUID del usuario (para operaciones con permisos) | Depende del endpoint |

### Obtener API Key

Las API Keys se almacenan en la tabla `public.api_keys`. Para crear una:

```sql
-- Ejecutar en la base de datos
INSERT INTO public.api_keys (key_hash, schema_name, description, is_active)
VALUES (
    encode(sha256('MI-API-KEY-SECRETA'::bytea), 'hex'),
    'tenant_municipalidad',
    'API Key para integracion X',
    true
);
```

### Ejemplo de Request

```bash
curl -X GET "https://mcp.gdilatam.com/api/v1/cases/search" \
  -H "X-API-Key: MI-API-KEY-SECRETA" \
  -H "X-User-ID: 123e4567-e89b-12d3-a456-426614174000"
```

---

## 2. Endpoints de Expedientes

### 2.1 Buscar Expedientes

```
GET /api/v1/cases/search
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (requerido)

**Query Parameters**:

| Parametro | Tipo | Descripcion |
|-----------|------|-------------|
| `search` | string | Buscar por numero, referencia o contenido |
| `status` | string | `active`, `inactive`, `archived` |
| `date_filter` | string | `today`, `week`, `month`, `year` |
| `sector_filter` | string | Acronimo del sector |
| `page` | int | Pagina (default 1) |
| `page_size` | int | Resultados por pagina (default 20, max 100) |

**Ejemplo**:
```bash
curl -X GET "https://mcp.gdilatam.com/api/v1/cases/search?search=panaderia&status=active&page=1" \
  -H "X-API-Key: MI-API-KEY" \
  -H "X-User-ID: uuid-usuario"
```

**Respuesta**:
```json
{
  "cases": [
    {
      "id": "95a5f55d-2d68-49e8-8513-22996aa4fc7e",
      "case_number": "EE-2026-000018-TXST-INTE",
      "reference": "Habilitacion Comercial - Panaderia La Estrella",
      "last_modified_at": "2026-01-20 13:40:37",
      "case_type": {
        "name": "Habilitacion Comercial",
        "acronym": "HABI"
      },
      "admin_sector": {
        "acronym": "OOPA#PRIV",
        "department": "Obras Particulares"
      }
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20,
  "total_pages": 1
}
```

---

### 2.2 Buscar por Numero Exacto

```
GET /api/v1/cases/number/{case_number}
```

**Headers**: `X-API-Key` (requerido)

**Path Parameters**:

| Parametro | Tipo | Descripcion |
|-----------|------|-------------|
| `case_number` | string | Numero exacto (ej: `EE-2026-000018-TXST-INTE`) |

**Ejemplo**:
```bash
curl -X GET "https://mcp.gdilatam.com/api/v1/cases/number/EE-2026-000018-TXST-INTE" \
  -H "X-API-Key: MI-API-KEY"
```

---

### 2.3 Detalle de Expediente

```
GET /api/v1/cases/{case_id}
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (opcional)

**Path Parameters**:

| Parametro | Tipo | Descripcion |
|-----------|------|-------------|
| `case_id` | UUID | ID del expediente |

**Query Parameters**:

| Parametro | Tipo | Descripcion |
|-----------|------|-------------|
| `include_documents` | bool | Incluir documentos vinculados (default false) |

**Ejemplo**:
```bash
curl -X GET "https://mcp.gdilatam.com/api/v1/cases/95a5f55d-2d68-49e8-8513-22996aa4fc7e?include_documents=true" \
  -H "X-API-Key: MI-API-KEY"
```

---

### 2.4 Historial de Expediente

```
GET /api/v1/cases/{case_id}/history
```

**Headers**: `X-API-Key` (requerido)

**Path Parameters**:

| Parametro | Tipo | Descripcion |
|-----------|------|-------------|
| `case_id` | UUID | ID del expediente |

**Respuesta incluye**:
- `ai_summary`: Resumen IA del expediente
- `movements`: Lista de movimientos con fecha, tipo, mensaje y resumen IA
- `documents`: Lista de documentos con ai_summary

---

### 2.5 Documentos del Expediente

```
GET /api/v1/cases/{case_id}/documents
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (opcional)

**Respuesta**:
```json
{
  "official": [...],
  "proposed": [...],
  "total_official": 5,
  "total_proposed": 2
}
```

---

### 2.6 Permisos sobre Expediente

```
GET /api/v1/cases/{case_id}/permissions
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (requerido)

**Respuesta**:
```json
{
  "can_view": true,
  "can_transfer": false,
  "can_assign": true,
  "can_archive": false,
  "can_link_documents": true,
  "ownership_level": "participant"
}
```

---

### 2.7 Preparar Asignacion

```
GET /api/v1/cases/{case_id}/prepare-assignment
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (requerido)

Verifica permisos y retorna sectores disponibles para asignar.

**Respuesta**:
```json
{
  "success": true,
  "status": "OK",
  "user_sectors_in_case": [
    {"sector_id": "uuid", "acronym": "HAC#PRIV", "role": "ADMIN"}
  ],
  "available_sectors": [
    {"sector_id": "uuid", "acronym": "LEGAL#PRIV", "department": "Legal"}
  ]
}
```

---

### 2.8 Asignar Expediente

```
POST /api/v1/cases/{case_id}/assign
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (requerido)

**Body**:
```json
{
  "target_sector_id": "uuid-sector-destino",
  "reason": "Solicito revision legal del expediente",
  "assigned_user_id": "uuid-usuario-opcional",
  "create_official_doc": false
}
```

**Respuesta**:
```json
{
  "success": true,
  "movement_id": "uuid-movimiento",
  "case_number": "EE-2026-000018",
  "action_type": "asignado",
  "target_sector": "LEGAL#PRIV"
}
```

---

### 2.9 Cerrar Asignacion

```
POST /api/v1/cases/{case_id}/close-assign
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (requerido)

**Body**:
```json
{
  "movement_id": "uuid-movimiento-a-cerrar",
  "reason": "Tarea completada satisfactoriamente"
}
```

---

## 3. Endpoints de Documentos

### 3.1 Buscar Documentos

```
GET /api/v1/documents/search
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (requerido)

**Query Parameters**:

| Parametro | Tipo | Descripcion |
|-----------|------|-------------|
| `search` | string | Buscar por numero, referencia o contenido |
| `status` | string | `pending`, `sent_to_sign`, `signed`, `rejected` |
| `document_type` | string | Acronimo del tipo (INF, DICT, etc.) |
| `case_id` | UUID | Filtrar por expediente |
| `page` | int | Pagina (default 1) |
| `page_size` | int | Resultados por pagina (default 20, max 100) |

**Ejemplo**:
```bash
curl -X GET "https://mcp.gdilatam.com/api/v1/documents/search?status=signed&document_type=INF" \
  -H "X-API-Key: MI-API-KEY" \
  -H "X-User-ID: uuid-usuario"
```

---

### 3.2 Firmas Pendientes

```
GET /api/v1/documents/pending-signatures
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (requerido)

Retorna documentos donde es el turno del usuario para firmar.

**Respuesta**:
```json
{
  "documents": [
    {
      "document_id": "uuid",
      "reference": "Informe sobre habilitacion",
      "document_type": "INF",
      "signer_role": "signer",
      "creator": "Juan Perez",
      "sent_to_sign_at": "2026-01-30T10:00:00"
    }
  ],
  "total": 1
}
```

---

### 3.3 Detalle de Documento

```
GET /api/v1/documents/{document_id}
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (opcional)

**Respuesta incluye**:
- `ai_summary`: Resumen IA del contenido
- `state_category`: `editing` o `signing`
- `status`: Estado actual
- `details`: Firmantes, fechas, etc.
- `linked_case`: Expediente vinculado

---

### 3.4 Contenido HTML del Documento

```
GET /api/v1/documents/{document_id}/content
```

**Headers**: `X-API-Key` (requerido)

**IMPORTANTE**: Solo funciona con documentos OFICIALES (firmados).

**Respuesta**:
```json
{
  "document_id": "uuid",
  "official_number": "INF-2026-00000060-TXST-TESO",
  "reference": "Informe sobre presupuesto",
  "content": {
    "html": "<p>Contenido del documento...</p>",
    "format": "html"
  },
  "signed_at": "2026-01-16T17:45:22"
}
```

---

### 3.5 URL de Descarga PDF

```
GET /api/v1/documents/{document_id}/url
```

**Headers**: `X-API-Key` (requerido)

Genera URL firmada temporal para descargar el PDF.

**Respuesta**:
```json
{
  "document_id": "uuid",
  "official_number": "INF-2026-00000060-TXST-TESO",
  "pdf_url": "https://r2.cloudflare.com/signed-url...",
  "expires_in": 600
}
```

---

### 3.6 Crear Documento

```
POST /api/v1/documents/
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (requerido)

**Body**:
```json
{
  "document_type_acronym": "INF",
  "reference": "Informe sobre revision de expediente",
  "case_id": "uuid-expediente-opcional"
}
```

**Respuesta** (201 Created):
```json
{
  "document_id": "uuid-nuevo-documento",
  "status": "draft"
}
```

---

### 3.7 Guardar Documento

```
PATCH /api/v1/documents/{document_id}
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (requerido)

**Body** (todos los campos opcionales):
```json
{
  "content": "<p>Contenido HTML del documento</p>",
  "reference": "Nueva referencia",
  "signers": [
    {"user_id": "uuid", "is_numerator": false},
    {"email": "firmante@municipio.gob", "is_numerator": true}
  ]
}
```

**IMPORTANTE**: Solo funciona con documentos en estado `draft` o `rejected`.

---

### 3.8 Iniciar Firma

```
POST /api/v1/documents/{document_id}/start-signing
```

**Headers**: `X-API-Key` (requerido), `X-User-ID` (requerido)

**Requisitos**:
- Documento en estado `draft`
- Al menos un firmante y un numerador asignados
- Solo el creador puede iniciar

**Respuesta**:
```json
{
  "success": true,
  "message": "Proceso de firma iniciado"
}
```

---

## 4. Endpoints de Sistema

### 4.1 Tipos de Documentos

```
GET /api/v1/system/document-types
```

**Headers**: `X-API-Key` (requerido)

**Respuesta**:
```json
{
  "document_types": [
    {"name": "Informe", "acronym": "INF"},
    {"name": "Dictamen", "acronym": "DICT"},
    {"name": "Caratula", "acronym": "CAEX"}
  ],
  "total": 10
}
```

---

### 4.2 Estados de Documentos

```
GET /api/v1/system/document-states
```

**Headers**: `X-API-Key` (requerido)

**Respuesta**:
```json
{
  "states": [
    {"code": "draft", "display_state": "En edicion"},
    {"code": "sent_to_sign", "display_state": "Firmar ahora"},
    {"code": "signed", "display_state": "Firmado"}
  ],
  "mappings": {
    "draft": "En edicion",
    "sent_to_sign": "Firmar ahora"
  },
  "total": 5
}
```

---

### 4.3 Informacion de Usuario

```
GET /api/v1/system/users/{user_id}
```

**Headers**: `X-API-Key` (requerido)

**Respuesta**:
```json
{
  "user_id": "uuid",
  "full_name": "Juan Perez",
  "email": "juan@municipio.gob",
  "sector": {
    "id": "uuid",
    "acronym": "HAC",
    "department_name": "Hacienda"
  },
  "roles": ["admin", "user"],
  "additional_sectors": [
    {"sector_id": "uuid", "sector_acronym": "LEGAL", "can_view": true, "can_edit": false}
  ]
}
```

---

## 5. Codigos de Error

| Codigo | Descripcion |
|--------|-------------|
| 400 | Bad Request - Parametros invalidos |
| 401 | Unauthorized - API Key invalida o faltante |
| 403 | Forbidden - Sin permisos para la operacion |
| 404 | Not Found - Recurso no encontrado |
| 409 | Conflict - Estado invalido para la operacion |
| 500 | Internal Server Error |

**Formato de error**:
```json
{
  "error": "Mensaje descriptivo del error"
}
```

---

## 6. Limites y Buenas Practicas

### Rate Limiting
- No hay rate limiting actualmente
- Recomendado: max 100 requests/minuto

### Paginacion
- Maximo 100 resultados por pagina
- Siempre verificar `total_pages` para iterar

### Timeouts
- Timeout recomendado: 30 segundos
- Operaciones de escritura pueden tardar mas

### Caching
- Los endpoints de catalogos (`document-types`, `document-states`) pueden cachearse
- Los endpoints de busqueda NO deben cachearse

---

## 7. Ejemplos de Integracion

### Python

```python
import requests

API_KEY = "mi-api-key"
BASE_URL = "https://mcp.gdilatam.com/api/v1"
USER_ID = "uuid-usuario"

headers = {
    "X-API-Key": API_KEY,
    "X-User-ID": USER_ID
}

# Buscar expedientes
response = requests.get(
    f"{BASE_URL}/cases/search",
    params={"search": "habilitacion", "status": "active"},
    headers=headers
)
cases = response.json()

# Obtener historial
case_id = cases["cases"][0]["id"]
response = requests.get(
    f"{BASE_URL}/cases/{case_id}/history",
    headers={"X-API-Key": API_KEY}
)
history = response.json()
```

### JavaScript/Node.js

```javascript
const API_KEY = "mi-api-key";
const BASE_URL = "https://mcp.gdilatam.com/api/v1";
const USER_ID = "uuid-usuario";

// Buscar documentos
const response = await fetch(
  `${BASE_URL}/documents/search?status=signed&document_type=INF`,
  {
    headers: {
      "X-API-Key": API_KEY,
      "X-User-ID": USER_ID
    }
  }
);
const documents = await response.json();
```

### cURL

```bash
# Buscar expedientes
curl -X GET "https://mcp.gdilatam.com/api/v1/cases/search?search=licitacion" \
  -H "X-API-Key: mi-api-key" \
  -H "X-User-ID: uuid-usuario"

# Crear documento
curl -X POST "https://mcp.gdilatam.com/api/v1/documents/" \
  -H "X-API-Key: mi-api-key" \
  -H "X-User-ID: uuid-usuario" \
  -H "Content-Type: application/json" \
  -d '{"document_type_acronym": "INF", "reference": "Informe de prueba"}'
```

---

## 8. Diferencias con MCP Protocol

| Aspecto | REST API | MCP Protocol |
|---------|----------|--------------|
| Autenticacion | API Key (header) | OAuth 2.0 (JWT) |
| Clientes | Cualquier HTTP client | Claude Code, ChatGPT, Gemini |
| User ID | Manual (header) | Automatico (del JWT) |
| Formato | JSON standard | JSON-RPC |
| Uso tipico | Integraciones backend | Agentes IA conversacionales |

---

*Documento generado: 2026-01-31*
*Version API: v1*
*Endpoints disponibles: 18*
