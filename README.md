[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

# GDI Backend - Sistema de Gestión Documental Institucional

**Version 1.2** (Phase 2-6 Complete - Direct Integrations)

API REST para la gestión integral de documentos y expedientes institucionales en entidades gubernamentales. Sistema modular construido con FastAPI y PostgreSQL.

**Arquitectura actualizada (Octubre 2025):**
- Integración directa con **PDFComposer** para generación de PDFs
- Integración directa con **Notary API** para firma digital
- Almacenamiento directo en **Cloudflare R2** (S3-compatible)
- Legal Orchestrator completamente eliminado (solo usado en preview)

---

## Características Principales

- **Gestión completa de documentos** - Crear, editar, firmar y gestionar documentos oficiales
- **Sistema de expedientes** - Organizar documentos por casos con seguimiento de movimientos
- **Firma digital integrada** - Proceso de firma con múltiples firmantes y numeración automática
- **Numeración global** - Sistema centralizado de numeración por año y tipo de documento
- **Integración directa PDFComposer** - Generación de PDFs oficiales sin intermediarios (Phases 2, 5, 6)
- **Integración directa Notary API** - Firma digital directa con manejo automático FULLPAGE (Phases 3, 4, 5, 6)
- **Almacenamiento Cloudflare R2** - Storage directo S3-compatible para PDFs firmados y pendientes
- **Multi-departamento y multi-sector** - Gestión por departamentos y sectores organizacionales
- **Transferencia de expedientes** - Sistema completo de transferencia y asignación entre sectores
- **Carátulas automáticas** - Generación automática de carátulas para expedientes (Phase 5)
- **Pases automáticos** - Generación automática de pases de transferencia (Phase 6)

---

## Arquitectura del Sistema

El proyecto sigue **Clean Architecture** con organización por dominios:

```
Backend/
│
├── 📍 endpoints/          # Capa de Presentación (API REST)
│   ├── cases/             # Expedientes
│   ├── documents/         # Documentos
│   ├── users/             # Usuarios
│   └── system/            # Sistema
│
├── 🔧 services/           # Capa de Lógica de Negocio
│   ├── cases/             # Servicios de expedientes
│   │   ├── cover_creator.py         # Phase 5: CAEX directo
│   │   ├── transfer_document_creator.py  # Phase 6: PV directo
│   │   └── _document_creator_base.py     # Router unificado
│   ├── documents/         # Servicios de documentos
│   │   ├── signing.py              # Phase 2: Inicio firma directo
│   │   ├── numerator.py            # Phase 3: Numerador directo
│   │   └── ...
│   ├── shared/            # Servicios compartidos
│   │   ├── pdfcomposer_api.py     # Phase 2, 5, 6: PDFComposer
│   │   ├── notary_api.py          # Phase 3, 4, 5, 6: Notary
│   │   ├── pdf_utils.py           # FULLPAGE handling
│   │   └── external_api.py        # Legacy (preview only)
│   └── storage/           # Almacenamiento (Cloudflare R2)
│       └── cloudflare.py          # R2 client con boto3
│
├── 📋 models/             # Modelos Pydantic (Contratos de Datos)
│   ├── documents/         # Schemas de documentos
│   ├── users/             # Schemas de usuarios
│   └── shared/            # Schemas compartidos
│
├── 🛠️ shared/             # Componentes Compartidos
│   ├── database.py        # Gestión de conexiones PostgreSQL
│   ├── exceptions.py      # Excepciones personalizadas
│   ├── validation.py      # Validadores comunes
│   ├── numbering.py       # Numeración centralizada con advisory locks
│   ├── utils.py           # Utilidades generales
│   └── config.py          # Configuración
│
├── 📦 migrations/         # Migraciones de base de datos
└── 🧪 tests/              # Tests unitarios
```

---

## API Endpoints

### 📂 Expedientes (`/api/v1/cases`)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/v1/cases` | Listar expedientes con filtros y paginación |
| POST | `/api/v1/cases` | Crear nuevo expediente (con `create_official_doc=true` genera CAEX - Phase 5) |
| GET | `/api/v1/cases/{case_id}` | Obtener detalle de expediente |
| GET | `/api/v1/cases/{case_id}/prepare-actions` | Obtener acciones disponibles |
| POST | `/api/v1/cases/{case_id}/transfer` | Transferir expediente (con `create_official_doc=true` genera PV - Phase 6) |
| POST | `/api/v1/cases/{case_id}/assign` | Asignar tarea (con `create_official_doc=true` genera PV - Phase 6) |
| POST | `/api/v1/cases/{case_id}/close-assign` | Cerrar asignación de tarea |
| GET | `/api/v1/cases/{case_id}/available-sectors` | Sectores disponibles para transferir |
| GET | `/api/v1/cases/sectors/{sector_id}/users` | Usuarios de un sector |
| GET | `/api/v1/cases/{case_id}/case-history` | Obtener historial completo de movimientos |
| GET | `/api/v1/cases/{case_id}/documents` | Obtener documentos del expediente (activos + inactivos) |
| GET | `/api/v1/cases/{case_id}/movements` | Obtener movimientos del expediente |
| GET | `/api/v1/cases/{case_id}/permissions` | Obtener permisos del usuario sobre el expediente |
| POST | `/api/v1/cases/{case_id}/documents/link` | Vincular documento a expediente |
| GET | `/api/v1/cases/health` | Health check del sistema |

### 📄 Documentos (`/api/v1/documents`)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/api/v1/documents` | Crear documento borrador |
| PUT | `/api/v1/documents/{doc_id}` | Guardar cambios en borrador |
| GET | `/api/v1/documents/{doc_id}/editor-details` | Datos para editor (borrador) |
| GET | `/api/v1/documents/{doc_id}/preview` | Vista previa HTML |
| GET | `/api/v1/documents/{doc_id}/preview/download` | Descargar PDF de preview |
| POST | `/api/v1/documents/{doc_id}/start-signing` | Iniciar proceso de firma (Phase 2: PDFComposer directo) |
| GET | `/api/v1/documents/{doc_id}/signature-details` | Detalles de documento en firma |
| POST | `/api/v1/documents/{doc_id}/sign` | Firmar documento (Phase 4: Notary directo) |
| POST | `/api/v1/documents/{doc_id}/sign-numerator` | Firma de numerador (Phase 3: Notary directo) |
| POST | `/api/v1/documents/{doc_id}/reject` | Rechazar documento |
| GET | `/api/v1/documents/states` | Estados de documentos |
| GET | `/api/v1/documents/types` | Tipos de documentos |

### 👥 Usuarios (`/api/v1/users`)

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/v1/users/search` | Buscar usuarios por nombre |
| GET | `/api/v1/users/{user_id}/documents` | Documentos de un usuario |
| GET | `/api/v1/users/documents` | Documentos del usuario actual |
| GET | `/api/v1/users/profile` | Perfil del usuario |
| POST | `/api/v1/users` | Crear nuevo usuario |

### ⚙️ Sistema

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/` | Root endpoint |
| GET | `/api/v1/system/health` | Health check (PÚBLICO - sin autenticación) |
| GET | `/docs` | Documentación Swagger UI |
| GET | `/redoc` | Documentación ReDoc |
| GET | `/openapi.json` | Schema OpenAPI |

---

## Flujo de Trabajo de Documentos

### 1. Ciclo de Vida de un Documento

```
draft → sent_to_sign → signed → official
   ↓
rejected → draft
```

#### Estados del Documento:
- **draft**: Borrador en edición
- **sent_to_sign**: Enviado para firma digital
- **signed**: Firmado por todos los firmantes
- **rejected**: Rechazado por algún firmante (vuelve a draft)
- **official**: Documento oficial con numeración asignada

### 2. Proceso de Firma Digital (Phases 2-4)

1. **Creación del borrador** (`POST /documents`)
   - El usuario crea un documento y selecciona firmantes

2. **Edición y guardado** (`PUT /documents/{doc_id}`)
   - El usuario edita contenido HTML y guarda cambios

3. **Inicio de firma** (`POST /documents/{doc_id}/start-signing`) - **Phase 2**
   - Se genera PDF con PDFComposer directo (sin Legal Orchestrator)
   - PDF se sube a Cloudflare R2 bucket 'tosign'
   - Estado cambia a `sent_to_sign`
   - Se guarda `document_generate_id` en BD

4. **Firmantes firman** (`POST /documents/{doc_id}/sign`) - **Phase 4**
   - Cada firmante firma en orden establecido usando Notary API directo
   - PDF se descarga de R2, se firma, se sobrescribe en R2
   - Se registra timestamp de firma
   - Manejo automático de error FULLPAGE (agrega página en blanco con pypdf)

5. **Numerador finaliza** (`POST /documents/{doc_id}/sign-numerator`) - **Phase 3**
   - El numerador firma último usando Notary API directo
   - Se asigna número oficial (advisory lock 888888)
   - PDF firmado se sube a R2 bucket 'oficial'
   - PDF temporal se elimina de bucket 'tosign' (soft-fail)
   - Se genera carátula si está vinculado a expediente
   - Estado cambia a `signed` → `official`

### 3. Sistema de Numeración

**Numeración por Tipo de Documento:**
- Cada tipo tiene secuencia independiente
- Formato: `{acronym}-{number}/{year}` (ej: `MEMO-123/2025`)

**Numeración Global:**
- Secuencia única por año para todos los documentos
- Formato: `{global_sequence}/{year}` (ej: `456/2025`)
- Se asigna al momento de la firma del numerador

**Tablas Involucradas:**
- `document_draft` - Borradores en edición
- `official_documents` - Documentos oficiales numerados
- `document_counters` - Contadores por tipo y año
- `global_document_counters` - Contador global por año

---

## Integraciones Externas

### Nueva Arquitectura (Phases 2-6 - Octubre 2025)

Sistema de integración directa eliminando intermediarios:

#### Phase 2: Generación de PDFs (Documentos Manuales)
```
GDI Backend → PDFComposer API (Railway)
POST /generate-pdf
{
  "document_content": "<html>...",
  "document_reference": "ref",
  "document_type_name": "Memorándum",
  "document_type_acronym": "MEMO",
  "signers": [...],
  "logo_url": "https://..."
}
← Returns: PDF bytes

GDI Backend → Cloudflare R2 (S3-compatible)
PUT /tenant-test-tosign/{document_id}.pdf
← PDF almacenado en bucket 'tosign'
```

#### Phase 3: Firma Digital Numerador
```
1. Descargar PDF desde R2:
   GDI Backend → Cloudflare R2
   GET /tenant-test-tosign/{document_id}.pdf
   ← Returns: Signed URL (600s expiry)

2. Firmar con Notary API:
   GDI Backend → Notary API (Railway)
   POST /sign-pdf
   {
     pdf_file: binary,
     name: "Juan Pérez",
     seal: "Subsecretaria",
     department: "Admin General",
     entity: "Municipalidad Test",
     document_number: "IF-2025-000000157-MT-DGOBR",  # CON número oficial
     city: "LATAM"
   }

   Si responde FULLPAGE (400):
     ├─→ Agregar página en blanco con pypdf
     └─→ Reintentar POST /sign-pdf

   ← Returns: PDF firmado (bytes)

3. Subir PDF firmado a R2 oficial:
   GDI Backend → Cloudflare R2
   PUT /tenant-test-oficial/{official_number}.pdf
   ← PDF firmado almacenado permanentemente

4. Cleanup (soft-fail):
   DELETE /tenant-test-tosign/{document_id}.pdf
```

#### Phase 4: Firma Digital Común
```
1-2. Igual que Phase 3 (descargar y firmar)
   PERO en Notary:
     document_number: ""  # VACÍO para firmante común
     city: ""             # VACÍO para firmante común

3. Sobrescribir PDF en R2 tosign:
   GDI Backend → Cloudflare R2
   PUT /tenant-test-tosign/{document_id}.pdf
   ← PDF sobrescrito para siguiente firmante
```

#### Phase 5: Carátulas CAEX (Documentos Automáticos)
```
1. Generar PDF con PDFComposer:
   GDI Backend → PDFComposer API
   POST /create-case/
   {
     urlLogo, NameAcronyType, document_type, reference,
     case_number, acrony_case_type, case_type, case_motive,
     initiating_division, creator
   }
   ← Returns: PDF bytes (template caratula.html)

2. Firmar con Notary:
   POST /sign-pdf
   {
     pdf_file: binary,
     document_number: "CAEX-2025-00000034-SMG-ADGEN",  # CON número oficial
     city: "LATAM"
   }

3. Subir directo a R2 oficial:
   PUT /tenant-test-oficial/{official_number}.pdf
   (NO pasa por bucket 'tosign')
```

#### Phase 6: Pases PV (Transferencias/Asignaciones)
```
1. Generar PDF con PDFComposer:
   GDI Backend → PDFComposer API
   POST /move/
   {
     urlLogo, NameAcronyType, document_type, reference,
     tipo_movimiento, area_requiriente, area_receptora, motivo
   }
   ← Returns: PDF bytes (template movimiento.html)

2-3. Igual que Phase 5 (firmar y subir a oficial)
```

**Archivos clave:**
- `services/shared/pdfcomposer_api.py` - PDFComposer integration (Phases 2, 5, 6)
- `services/shared/notary_api.py` - Notary integration (Phases 3, 4, 5, 6)
- `services/shared/pdf_utils.py` - FULLPAGE handling
- `services/storage/cloudflare.py` - R2 client con boto3
- `services/documents/numerator.py` - Phase 3 implementation
- `services/documents/signing.py` - Phases 2 y 4 implementation
- `services/cases/_document_creator_base.py` - Phases 5 y 6 router
- `services/cases/cover_creator.py` - Phase 5 CAEX creator
- `services/cases/transfer_document_creator.py` - Phase 6 PV creator

### Email Service (Invitaciones Automáticas)

Sistema opcional de envío de emails HTML a usuarios inactivos durante el proceso de firma.

**Flujo de invitación:**
```
POST /documents/{doc_id}/start-signing
     │
     ├─→ Genera PDF (PDFComposer)
     ├─→ Sube a R2
     └─→ Si EMAIL_SERVICE_URL configurado:
          │
          ├─→ Filtra firmantes inactivos (estado != 1 OR invited_at IS NULL)
          │
          └─→ Para cada firmante inactivo:
              POST EMAIL_SERVICE_URL/send-invitation
              {
                "email": "user@example.com",
                "name": "Juan Pérez",
                "document_reference": "MEMO-001",
                "cta_url": "https://nuevogdi.framer.website/"
              }
              │
              └─→ UPDATE users SET invited_at = NOW()
```

**Características:**
- ✅ Best-effort: No bloquea proceso de firma si email falla
- ✅ Plantilla HTML personalizada con branding GDI
- ✅ CTA link apunta a: https://nuevogdi.framer.website/
- ✅ Tracking de invitaciones en `users.invited_at`
- ⚠️ Requiere configurar `EMAIL_SERVICE_URL` y `EMAIL_API_KEY`

**Variables de entorno:**
```bash
EMAIL_SERVICE_URL=https://your-email-service.railway.app
EMAIL_API_KEY=your-email-api-key
```

**Archivo clave:**
- `services/documents/signing.py:404-489` - Función `_send_user_invitations()`

### Legal Orchestrator (DEPRECATED - Solo Preview)

**Estado actual:**
- ❌ NO se usa para inicio de firma (Phase 2: PDFComposer directo)
- ❌ NO se usa para firma común (Phase 4: Notary directo)
- ❌ NO se usa para firma numerador (Phase 3: Notary directo)
- ❌ NO se usa para CAEX (Phase 5: PDFComposer + Notary directo)
- ❌ NO se usa para PV (Phase 6: PDFComposer + Notary directo)
- ⚠️ SOLO se usa para preview de documentos (pendiente migración)

**Razones de migración:**
- Eliminar punto único de falla
- Reducir latencia (menos saltos de red)
- Mayor control sobre el flujo de datos
- Manejo granular de errores (ej: FULLPAGE)

Ver la documentacion de Phases 2-6 para detalles completos

---

## Sistema de Expedientes

Los expedientes organizan documentos relacionados por caso.

### Características:

- **Número de expediente**: Formato `EXP-{year}-{sequence}` (ej: `EXP-2025-00123`)
- **Propietario**: Departamento y sector responsable
- **Movimientos**: Historial de transferencias y asignaciones
- **Documentos vinculados**: Múltiples documentos pueden pertenecer a un expediente
- **Carátula automática**: Se genera al crear expediente con `create_official_doc=true` (Phase 5)
- **Pases automáticos**: Se generan en transferencias con `create_official_doc=true` (Phase 6)

### Tipos de Movimientos:

1. **creation**: Creación inicial del expediente
2. **transfer**: Transferencia de propiedad entre sectores
3. **assignment**: Asignación temporal de tarea a otro sector

### Creación de Expediente con Carátula (Phase 5):

```json
POST /api/v1/cases
{
  "case_template_id": "uuid",
  "reference": "Referencia del expediente",
  "motive": "Motivo de creación",
  "create_official_doc": true  ← Genera CAEX automáticamente
}
```

### Transferencia de Expedientes con Pase (Phase 6):

```json
POST /api/v1/cases/{case_id}/transfer
{
  "target_sector_id": "uuid",
  "reason": "Motivo de la transferencia",
  "transfer_ownership": true,
  "assigned_user_id": "uuid (opcional)",
  "create_official_doc": true  ← Genera PV automáticamente
}
```

**Archivos clave:**
- `endpoints/cases/transfer_case.py`
- `services/cases/cover_creator.py` (Phase 5)
- `services/cases/transfer_document_creator.py` (Phase 6)
- `services/cases/_document_creator_base.py` (Router unificado)

---

## Base de Datos

### Tablas Principales:

**Documentos:**
- `document_draft` - Borradores en edición
- `official_documents` - Documentos oficiales con número
- `document_signers` - Firmantes de documentos
- `document_types` - Tipos de documentos
- `document_states` - Estados de documentos
- `document_counters` - Contadores por tipo/año
- `global_document_counters` - Contador global

**Expedientes:**
- `cases` - Expedientes principales
- `case_movements` - Historial de movimientos
- `case_documents` - Relación expedientes-documentos

**Usuarios y Organización:**
- `users` - Usuarios del sistema
- `departments` - Departamentos
- `sectors` - Sectores dentro de departamentos
- `municipalities` - Municipalidades

### Conexión:

El sistema usa PostgreSQL en Railway (producción) con configuración en `database.py`:
- Host: `your-db-host:5432`
- Base de datos: `your-database`
- Pool de conexiones con `psycopg2`

---

## Instalación y Configuración

### Requisitos:

- Python 3.11 o 3.12
- PostgreSQL 12+
- Acceso a internet (para Railway DB)

### CORS (Cross-Origin Resource Sharing)

El backend tiene configurado CORS para permitir peticiones desde el frontend:

**Orígenes permitidos automáticamente:**
- `http://localhost:3000` (React/Next.js)
- `http://localhost:5173` (Vite)
- `http://localhost:8080` (Vue/otros)
- `http://127.0.0.1:3000`
- `http://127.0.0.1:5173`
- `http://127.0.0.1:8080`

**Configuración para Railway:**
Cuando el backend está en Railway y necesitas acceder desde el frontend en producción, configura la variable de entorno:

```bash
FRONTEND_URL=https://tu-frontend.railway.app
```

Esto agregará automáticamente tu frontend de Railway a los orígenes permitidos.

**Archivo de configuración:** `main.py:55-78`

### Instalación Local:

```bash
# 1. Navegar a la carpeta del proyecto
cd GDI-Backend

# 2. Crear entorno virtual
python3 -m venv .venv

# 3. Activar entorno virtual
source .venv/bin/activate        # Linux/Mac
# .venv\Scripts\activate         # Windows

# 4. Instalar dependencias
pip install -r requirements.txt

# 5. Configurar variables de entorno
# Copiar .env.example a .env y completar con tus credenciales
cp .env.example .env
# Editar .env con tus valores reales

# Variables de entorno requeridas:
#
# Database (PostgreSQL):
#   DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
#   Nota: Puerto 6432 = PgBouncer (auto-detectado), 5432 = PostgreSQL directo
#
# APIs Externas (Phases 2-6):
#   PDFCOMPOSER_URL, PDFCOMPOSER_API_KEY
#   NOTARY_URL, NOTARY_API_KEY
#
# Cloudflare R2 (Storage):
#   CF_R2_ENDPOINT, CF_R2_ACCESS_KEY_ID, CF_R2_SECRET_ACCESS_KEY
#   CF_R2_BUCKET_OFICIAL, CF_R2_BUCKET_TOSIGN
#
# Email Service (Opcional):
#   EMAIL_SERVICE_URL, EMAIL_API_KEY
#
# Ver .env.example para detalles completos

# 6. Iniciar servidor
python main.py
```

### Servidor Corriendo:

- **URL principal**: `http://127.0.0.1:8000`
- **Swagger Docs**: `http://127.0.0.1:8000/docs`
- **ReDoc**: `http://127.0.0.1:8000/redoc`

### Hot-Reload:

El servidor detecta automáticamente cambios en el código y se recarga.

---

## Desarrollo

### Agregar Nuevo Endpoint:

1. **Crear modelo Pydantic** en `models/{dominio}/`
2. **Implementar servicio** en `services/{dominio}/`
3. **Definir endpoint** en `endpoints/{dominio}/`
4. El sistema auto-descubre el endpoint (carga dinámica en `main.py`)

**Ejemplo:**

```python
# models/documents/export.py
from pydantic import BaseModel

class ExportDocumentRequest(BaseModel):
    format: str  # pdf, docx, etc.

# services/documents/export.py
def export_document(document_id: str, format: str):
    # Lógica de exportación
    pass

# endpoints/documents/export_document.py
from fastapi import APIRouter
from models.tags import Tags

router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.post("/documents/{doc_id}/export")
async def export_document_endpoint(doc_id: str, request: ExportDocumentRequest):
    return export_document(doc_id, request.format)
```

### Testing:

```bash
# Ejecutar todos los tests
python -m pytest

# Tests con cobertura
python -m pytest --cov=.

# Test específico
python run_tests.py
```

---

## Tecnologías

| Tecnología | Uso |
|------------|-----|
| **FastAPI** | Framework web moderno |
| **Pydantic** | Validación de datos y serialización |
| **PostgreSQL** | Base de datos relacional |
| **psycopg2** | Driver PostgreSQL |
| **httpx** | Cliente HTTP para APIs externas |
| **boto3** | AWS SDK para Cloudflare R2 (S3-compatible) |
| **pypdf** | Manipulación de PDFs (FULLPAGE handling) |
| **Uvicorn** | Servidor ASGI |
| **Python 3.12** | Lenguaje de programación |

---

## Seguridad y Autenticación

### Modo Desarrollo:
- Header `X-User-ID` para identificar usuario
- Sin autenticación real (TESTING_MODE=true)

### Modo Producción:
- Sistema de autenticación en `auth.py`
- Validación de permisos por sector y departamento
- Control de acceso basado en roles

---

## Estructura de Respuestas

Todas las respuestas siguen formato estándar:

**Éxito:**
```json
{
  "success": true,
  "data": { ... },
  "message": "Operación exitosa"
}
```

**Error:**
```json
{
  "detail": "Descripción del error"
}
```

---

## Logs y Debugging

El sistema usa `safe_print()` para logging consistente:

```python
from shared.utils import safe_print

safe_print(f"[MODULE] Mensaje de log")
```

Los logs se muestran en consola con formato:
```
[INFO] 2025-10-23 - module - Mensaje
```

---

## Migrar Base de Datos

```bash
# Ejecutar migración
python run_migration.py
```

Las migraciones están en `migrations/` y se aplican automáticamente.

---

## Deployment

### Railway (Producción)

El proyecto se despliega automáticamente en Railway con las siguientes consideraciones:

#### 1. Configuración Automática

Railway detecta automáticamente:
- **Procfile**: Usa Gunicorn con 8 workers (configurado en `Procfile`)
- **Puerto dinámico**: Variable `PORT` asignada automáticamente
- **requirements.txt**: Instala dependencias automáticamente

#### 2. Variables de Entorno Requeridas

Configura estas variables en Railway Dashboard → Variables:

**Base de Datos (Crítico):**
```bash
DB_HOST=your-railway-db-host.railway.app
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your-railway-db-password
DB_NAME=railway
```

**APIs Externas:**
```bash
# PDFComposer (Phase 2, 5, 6)
PDFCOMPOSER_URL=https://your-pdfcomposer.railway.app
PDFCOMPOSER_API_KEY=your-api-key

# Notary API (Phase 3, 4, 5, 6)
NOTARY_URL=https://your-notary.railway.app
NOTARY_API_KEY=your-api-key
```

**Cloudflare R2:**
```bash
CF_R2_ENDPOINT=https://your-account.r2.cloudflarestorage.com
CF_R2_ACCESS_KEY_ID=your-key-id
CF_R2_SECRET_ACCESS_KEY=your-secret-key
CF_R2_BUCKET_OFICIAL=tenant-name-oficial
CF_R2_BUCKET_TOSIGN=tenant-name-tosign
CF_R2_SIGN_EXPIRATION=600
```

**CORS (si frontend en Railway):**
```bash
FRONTEND_URL=https://your-frontend.railway.app
```

**Opcional:**
```bash
TESTING_MODE=false  # Desactivar en producción
```

#### 3. Configuración Automática de Producción

El código detecta automáticamente el entorno:

```python
# main.py - Líneas 143-150
is_production = os.getenv("PORT") is not None
default_host = "0.0.0.0" if is_production else "127.0.0.1"
port = int(os.getenv("PORT", 8000))
reload = False if is_production else True
```

**Modo Producción (Railway):**
- Host: `0.0.0.0` (acepta todas las conexiones)
- Puerto: Dinámico (asignado por Railway)
- Hot-reload: Desactivado
- Workers: 8 (Gunicorn)

**Modo Desarrollo (Local):**
- Host: `127.0.0.1` (solo localhost)
- Puerto: `8000` (por defecto)
- Hot-reload: Activado
- Workers: 1 (Uvicorn)

#### 4. Checklist para Deploy

- [ ] Crear proyecto en Railway
- [ ] Conectar repositorio GitHub (rama `santiago4`)
- [ ] Configurar todas las variables de entorno
- [ ] Agregar PostgreSQL database (Railway add-on)
- [ ] Verificar que el build es exitoso
- [ ] Probar endpoints en `/docs`
- [ ] Configurar `FRONTEND_URL` si es necesario

#### 5. Archivos de Configuración

- **Procfile**: Comando de inicio con Gunicorn
- **requirements.txt**: Dependencias Python
- **.env.example**: Plantilla de variables de entorno
- **.gitignore**: Excluye `.env` y archivos sensibles

---

## Roadmap Futuro

- Implementación de notificaciones por email
- Dashboard de estadísticas
- Búsqueda avanzada full-text
- Exportación masiva de documentos
- Integración con sistemas externos
- API webhooks para eventos
- Migrar preview de documentos a PDFComposer directo (eliminar Legal Orchestrator)

---

## Soporte

Para reportar bugs o solicitar features:
- Crear issue en repositorio GitHub
- Contactar equipo de desarrollo GDI

---

## Licencia

Este proyecto esta licenciado bajo [AGPL-3.0](LICENSE).

## Contribuir

Ver [CONTRIBUTING.md](CONTRIBUTING.md) para guia de contribucion.

## Seguridad

Ver [SECURITY.md](SECURITY.md) para reportar vulnerabilidades.
