# GDI-Backend MCP Server

Model Context Protocol (MCP) Server para GDI-Backend con REST API pública.

## Características

- **MCP**: JSON-RPC 2.0 via HTTP (tools para Claude/LLMs)
- **REST API**: Endpoints HTTP simples para software externo
- **Autenticación Diferenciada**:
  - MCP: API Key simple (backward compatible) o Auth0 JWT
  - REST API: API Key por municipalidad (tabla `public.api_keys`)
- **Tools**: Solo lectura (12 herramientas)
- **Multi-tenant**: Soporta múltiples municipalidades

## Arquitectura

```
GDI-MCP Server (Starlette) - Puerto 8005
├── /health                    GET    (sin auth)
├── /mcp                       POST   (MCP JSON-RPC, API Key)
└── /api/v1/                   REST API (API Key por Schema)
    ├── /cases/*
    ├── /documents/*
    └── /system/*
```

---

## REST API v1

API REST pública para software externo con autenticación por API Key.

### Autenticación

```bash
# Header requerido
X-API-Key: sk_live_abc123...

# Header opcional (algunos endpoints)
X-User-ID: uuid-del-usuario
```

La API Key determina automáticamente la municipalidad y schema. No es necesario pasar `municipality_id`.

### Endpoints de Expedientes

| Endpoint | Method | Headers | Descripción |
|----------|--------|---------|-------------|
| `/api/v1/cases/search` | GET | X-API-Key, X-User-ID | Buscar expedientes |
| `/api/v1/cases/{case_id}` | GET | X-API-Key | Detalle de expediente |
| `/api/v1/cases/{case_id}/history` | GET | X-API-Key | Historial de movimientos |
| `/api/v1/cases/{case_id}/documents` | GET | X-API-Key | Documentos vinculados |
| `/api/v1/cases/{case_id}/permissions` | GET | X-API-Key, X-User-ID | Permisos del usuario |

### Endpoints de Documentos

| Endpoint | Method | Headers | Descripción |
|----------|--------|---------|-------------|
| `/api/v1/documents/search` | GET | X-API-Key, X-User-ID | Buscar documentos |
| `/api/v1/documents/pending-signatures` | GET | X-API-Key, X-User-ID | Firmas pendientes |
| `/api/v1/documents/{document_id}` | GET | X-API-Key | Detalle de documento |
| `/api/v1/documents/{document_id}/content` | GET | X-API-Key | Contenido HTML (solo oficiales) |

### Endpoints de Sistema

| Endpoint | Method | Headers | Descripción |
|----------|--------|---------|-------------|
| `/api/v1/system/document-types` | GET | X-API-Key | Tipos de documentos |
| `/api/v1/system/sectors` | GET | X-API-Key | Sectores y departamentos |
| `/api/v1/system/users/{user_id}` | GET | X-API-Key | Información de usuario |
| `/api/v1/system/case-templates` | GET | X-API-Key | Templates de expedientes |

### Ejemplos de Uso

```bash
# Buscar expedientes activos
curl -X GET "https://mcp.gdilatam.com/api/v1/cases/search?status=active&page=1" \
  -H "X-API-Key: sk_live_abc123..." \
  -H "X-User-ID: ece4d900-0b8a-4ad8-8e60-c52df9d3f25d"

# Obtener detalle de expediente con documentos
curl -X GET "https://mcp.gdilatam.com/api/v1/cases/651022d7-dea1-4410-a072-7831ebbad089?include_documents=true" \
  -H "X-API-Key: sk_live_abc123..."

# Listar tipos de documentos
curl -X GET "https://mcp.gdilatam.com/api/v1/system/document-types" \
  -H "X-API-Key: sk_live_abc123..."

# Buscar documentos de un expediente
curl -X GET "https://mcp.gdilatam.com/api/v1/documents/search?case_id=651022d7-dea1-4410-a072-7831ebbad089" \
  -H "X-API-Key: sk_live_abc123..." \
  -H "X-User-ID: ece4d900-0b8a-4ad8-8e60-c52df9d3f25d"
```

### Códigos de Error

| Código | Significado |
|--------|-------------|
| 400 | Parámetro inválido o faltante |
| 401 | API Key inválida, expirada o inactiva |
| 403 | Sin permisos para esta acción |
| 404 | Recurso no encontrado |
| 500 | Error interno del servidor |

---

## MCP Tools (JSON-RPC)

### Tools Disponibles

#### Expedientes (5 tools)

1. **search_cases**: Buscar expedientes con filtros
2. **get_case**: Detalle de expediente
3. **get_case_history**: Historial de movimientos
4. **get_case_documents**: Documentos vinculados
5. **get_case_permissions**: Permisos del usuario

#### Documentos (4 tools)

6. **search_documents**: Buscar documentos
7. **get_document**: Detalle de documento
8. **get_document_content**: Contenido HTML (solo oficiales)
9. **get_pending_signatures**: Firmas pendientes

#### Sistema (3 tools)

10. **get_document_types**: Tipos de documentos
11. **get_sectors**: Sectores y departamentos
12. **get_user_info**: Información del usuario
13. **get_case_templates**: Templates de expedientes

### Ejemplo MCP

```bash
curl -X POST "https://mcp.gdilatam.com/mcp" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "search_cases",
      "arguments": {
        "api_key": "gdi-mcp-key-2025",
        "municipality_id": "d729f774-8d63-4e99-a322-aebf9e7377a6",
        "user_id": "ece4d900-0b8a-4ad8-8e60-c52df9d3f25d",
        "status": "active"
      }
    }
  }'
```

---

## API Keys

### Tabla `public.api_keys`

Las API Keys se almacenan en la tabla `public.api_keys`:

```sql
CREATE TABLE public.api_keys (
    id UUID PRIMARY KEY,
    api_key VARCHAR(64) NOT NULL UNIQUE,  -- sk_live_xxx o sk_test_xxx
    municipality_id UUID NOT NULL,         -- Determina el schema
    name VARCHAR(100) NOT NULL,            -- "Sistema Contable ABC"
    description TEXT,
    is_active BOOLEAN DEFAULT true,
    expires_at TIMESTAMP,                  -- NULL = no expira
    last_used_at TIMESTAMP,                -- Se actualiza en cada uso
    rate_limit_per_minute INT DEFAULT 60,
    created_by VARCHAR(100)
);
```

### Crear API Key

```sql
INSERT INTO public.api_keys (api_key, municipality_id, name, description, created_by)
VALUES (
    'sk_live_' || encode(gen_random_bytes(24), 'hex'),
    'uuid-de-municipalidad',
    'Software Contable ABC',
    'Key para integración con sistema contable',
    'admin@municipio.gob.ar'
);
```

---

## Setup

### 1. Variables de Entorno

```env
# Database
DATABASE_URL=postgresql://...
DB_HOST=...
DB_PORT=5432
DB_USER=...
DB_PASSWORD=...
DB_NAME=...

# MCP (para tools JSON-RPC)
MCP_API_KEY=gdi-mcp-key-2025

# Puerto
PORT=8005
```

### 2. Crear Tabla API Keys

```bash
# Ejecutar migración
psql $DATABASE_URL -f GDI-BD/sql/migrations/012_add_api_keys.sql
```

### 3. Ejecutar Server

```bash
# Desarrollo
python api_gateway/http_server.py

# Producción (Railway)
# Start Command: uvicorn api_gateway.http_server:app --host 0.0.0.0 --port $PORT
```

---

## Deploy en Railway

1. **Crear servicio**:
   - Name: `gdi-mcp-server`
   - Start Command: `uvicorn api_gateway.http_server:app --host 0.0.0.0 --port $PORT`

2. **Variables** (copiar de GDI-Backend):
   ```env
   DATABASE_URL=...
   MCP_API_KEY=<generar-key-segura>
   PORT=8005
   ```

3. **Generar dominio**:
   ```bash
   railway domain
   ```

4. **Ejecutar migración**:
   ```bash
   psql $DATABASE_URL -f GDI-BD/sql/migrations/012_add_api_keys.sql
   ```

---

## Archivos

| Archivo | Propósito |
|---------|-----------|
| `http_server.py` | Server HTTP (MCP + REST API) |
| `auth.py` | Validación API Key MCP (legacy) |
| `auth_mcp.py` | Validación JWT Auth0 para MCP |
| `auth_rest.py` | Validación API Key REST (tabla) |
| `rest_api.py` | Handlers REST API |
| `context.py` | Contexto multi-tenant |
| `tools/cases.py` | Tools de expedientes |
| `tools/documents.py` | Tools de documentos |
| `tools/system.py` | Tools de sistema |

---

## Seguridad

- **REST API**: API Key por municipalidad (aislamiento automático)
- **MCP**: API Key simple o Auth0 JWT
- **Multi-tenant**: Cada API Key accede solo a su schema
- **Solo lectura**: Ningún endpoint puede modificar datos
- **Expiración**: Soporte para API Keys con fecha de vencimiento
- **Tracking**: `last_used_at` se actualiza en cada request

---

## Integración con Servicios Existentes

El server reutiliza 100% de los servicios existentes:

| Tool/Endpoint | Servicio |
|---------------|----------|
| search_cases | `CaseService.get_cases_by_user()` |
| get_case | `CaseService.get_case_detail()` |
| get_case_history | `CaseService.get_case_history()` |
| get_case_documents | `CaseService.get_case_documents()` |
| get_case_permissions | `CaseService.get_case_permissions()` |
| search_documents | `get_user_documents()` |
| get_document | `get_unified_document_details()` |
| get_document_content | `get_official_document_content()` |
| get_pending_signatures | Query directo |
| get_document_types | `get_all_document_types()` |
| get_sectors | `SectorService.get_all_sectors_with_departments()` |
| get_user_info | Query directo |
| get_case_templates | Query directo |

**NO hay duplicación de lógica de negocio.**

---

**IMPORTANTE**: Este server NO reemplaza la API REST principal de GDI-Backend. Es un canal adicional para integraciones externas y agentes IA.
