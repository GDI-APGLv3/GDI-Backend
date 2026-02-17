# Guia para Agente IA - MCP GDI Latam

## Resumen Ejecutivo

Eres un agente IA con acceso al sistema de Gestion Documental Inteligente (GDI) de gobiernos latinoamericanos a traves del protocolo MCP (Model Context Protocol).

**Capacidades actuales**: LECTURA + ESCRITURA SELECTIVA
- Buscar y consultar expedientes y documentos
- Leer contenido HTML de documentos oficiales
- Ver tipos, estados, templates y usuarios
- Ver historial de movimientos de expedientes
- Crear documentos borrador y guardar cambios
- Iniciar proceso de firma
- **Rechazar documentos** con motivo
- Asignar expedientes a otros sectores (sin transferir propiedad)
- **Proponer documentos** borrador para expedientes
- **Rechazar propuestas** de documentos
- **Buscar documentos por numero oficial exacto**
- **Ver detalles de firma** de cada firmante
- **Consultar notas** enviadas, archivadas y detalle

---

## 0. Conexion al Servidor

### Transporte
- **Protocolo**: Streamable HTTP (JSON-RPC sobre HTTP)
- **Metodo**: `POST /mcp` — todas las llamadas van por POST
- **GET /mcp**: NO soportado (no hay SSE/streaming)
- **DELETE /mcp**: Terminar sesion

### URLs
| Entorno | URL |
|---------|-----|
| Produccion | `https://mcp.gdilatam.com/mcp` |
| Local | `http://localhost:8005/mcp` |

### Ejemplo de Request
```http
POST /mcp HTTP/1.1
Content-Type: application/json
Authorization: Bearer <jwt-token>

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "search_cases",
    "arguments": {"search": "panaderia"}
  }
}
```

### Endpoints Utiles
| Endpoint | Metodo | Proposito |
|----------|--------|-----------|
| `/` | GET | Status del servicio |
| `/health` | GET | Health check detallado |
| `/mcp` | POST | Endpoint MCP (JSON-RPC) |
| `/.well-known/mcp.json` | GET | Manifest del servidor |
| `/.well-known/oauth-protected-resource` | GET | Discovery OAuth (RFC 9728) |

---

## 1. Contexto del Sistema GDI

### Que es GDI
Sistema de gestion documental para municipalidades que permite:
- Crear documentos oficiales (informes, resoluciones, dictamenes, etc.)
- Firmar documentos digitalmente (multi-firmante)
- Organizar documentos en expedientes
- Transferir expedientes entre sectores
- Mantener trazabilidad completa

### Arquitectura Multi-Tenant
- Cada municipalidad tiene su propio schema en la base de datos
- El `municipality_id` es **OBLIGATORIO** en cada consulta
- El sistema traduce `municipality_id` -> `schema_name` automaticamente

### Conceptos Clave

| Concepto | Descripcion |
|----------|-------------|
| **Expediente (Case)** | Carpeta que agrupa documentos relacionados a un tramite |
| **Documento** | Archivo oficial (informe, dictamen, acta, etc.) |
| **Sector** | Area/oficina del municipio (ej: Hacienda, Legal, Obras) |
| **Departamento** | Subunidad dentro de un sector |
| **Firma Digital** | Proceso de validacion oficial de documentos |

### Estados de Documentos
```
draft          -> Borrador, en edicion
sent_to_sign   -> Enviado a firma, esperando firmantes
signed         -> Firmado por todos los firmantes
rejected       -> Rechazado por algun firmante
official       -> Documento oficial final (ya firmado y numerado)
```

### Estados de Expedientes
```
active    -> Expediente activo en tramite
inactive  -> Expediente pausado/inactivo
archived  -> Expediente archivado/cerrado
```

---

## 2. Autenticacion OAuth 2.0

### Flujo Automatico (RFC 9728)

La autenticacion es **automatica** para clientes MCP compatibles (Claude Code, ChatGPT, Gemini):

1. Cliente llama una tool sin auth
2. Server responde **401** con header:
   ```
   WWW-Authenticate: Bearer resource_metadata="/.well-known/oauth-protected-resource"
   ```
3. Cliente hace GET a `/.well-known/oauth-protected-resource`
4. Server devuelve:
   ```json
   {
     "resource": "https://mcp.gdilatam.com",
     "authorization_servers": ["https://gdilatam.us.auth0.com"]
   }
   ```
5. Cliente obtiene metadata OAuth de Auth0
6. Se abre navegador para login
7. Usuario autoriza, cliente recibe JWT
8. Cliente usa JWT automaticamente en todas las llamadas

### Que se resuelve automaticamente

Con OAuth, el servidor inyecta automaticamente:
- `user_id`: Del JWT (claim "sub" o via /userinfo)
- `municipality_id`: De la tabla users segun el email del JWT

**NO necesitas enviar estos parametros en las tools.**

### Multi-Tenant

Si tu usuario tiene acceso a multiples municipalidades:
1. Usa `list_my_tenants` para ver tus organizaciones
2. Agrega `tenant_id` en las llamadas para especificar cual usar

### Endpoints de Discovery

| Endpoint | RFC | Proposito |
|----------|-----|-----------|
| `/.well-known/oauth-protected-resource` | 9728 | Descubre Auth0 |
| `/.well-known/oauth-authorization-server` | 8414 | Metadata OAuth |
| `/.well-known/mcp.json` | MCP | Manifest del servidor |

---

## 3. Herramientas MCP Disponibles

### 3.0 get_agent_guide
**Proposito**: Obtener esta guia completa para entender el sistema GDI.

**Parametros**: Ninguno requerido (funciona con OAuth automatico).

**Cuando usar**:
- Al conectarte por primera vez al MCP
- Si no sabes que herramientas usar
- Si necesitas entender como autenticarte
- "Dame la guia del sistema"
- "Como funciona esto?"

**Ejemplo de respuesta**:
```json
{
  "guide": "# Guia para Agente IA - MCP GDI Latam\n...",
  "version": "2.1",
  "tools_count": 35,
  "last_updated": "2026-02-13"
}
```

**RECOMENDACION**: Llamar esta herramienta al inicio de cada sesion para entender las capacidades del sistema.

---

### 3.1 get_document_types
**Proposito**: Listar todos los tipos de documentos disponibles.

**Parametros**: Ninguno (OAuth resuelve municipalidad automaticamente).
- `tenant_id` (opcional): Si tienes acceso a multiples municipalidades.

**Cuando usar**:
- "Que tipos de documentos existen?"
- "Cual es el acronimo para un informe?"
- Para validar tipos antes de buscar documentos

**Tipos comunes**:
| Acronimo | Nombre | Uso |
|----------|--------|-----|
| INF | Informe | Informes tecnicos, administrativos |
| DICT | Dictamen | Dictamenes legales o tecnicos |
| ACTA | Acta de Inspeccion | Inspecciones en terreno |
| CAEX | Caratula | Portada de expedientes (auto-generada) |
| PV | Pase de Vista | Transferencias (auto-generado) |
| HCOM | Certificado Habilitacion | Habilitaciones comerciales |
| PERMI | Permiso General | Permisos varios |

---

### 3.2 search_cases
**Proposito**: Buscar expedientes con filtros y paginacion.

**Parametros** (todos opcionales, OAuth inyecta user_id y municipality_id):
- `tenant_id`: Si tienes acceso a multiples municipalidades
- `page` (default 1)
- `page_size` (default 20, max 100)
- `search` - Busqueda inteligente (ver abajo)
- `status` - "active", "inactive", "archived"
- `date_filter` - "today", "week", "month", "year"
- `sector_filter` - Acronimo del sector

**BUSQUEDA INTELIGENTE (parametro `search`)**:
- **Case-insensitive**: "PANADERIA" = "panaderia" = "Panaderia"
- **Ignora acentos**: "comercial" encuentra "habilitacion comercial"
- **Busqueda parcial**: "mitre" encuentra "Av. Mitre 123"
- **Busca en EXPEDIENTE**: case_number, reference (asunto)
- **Busca en DOCUMENTOS VINCULADOS**: official_number, reference, contenido HTML
- **Ejemplo**: buscar "mitre" encuentra expedientes cuyo asunto menciona "Mitre" O que tienen documentos que mencionan "Mitre" en su contenido.

**Cuando usar**:
- "Buscar expedientes activos"
- "Expedientes creados esta semana"
- "Buscar expediente EXP-2025-0001"
- "Buscar expedientes que mencionen 'licitacion'"

**Ejemplo de respuesta**:
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
      "access_reason": "ADMINSECTOR",
      "admin_sector": {
        "acronym": "OOPA#PRIV",
        "department": "Obras Particulares"
      },
      "assigned_sectors": []
    }
  ],
  "total": 4,
  "page": 1,
  "page_size": 20,
  "total_pages": 1
}
```

**Notas sobre `access_reason`**:
- `ADMINSECTOR`: El usuario es del sector administrador del expediente
- `ASSIGNEDSECTOR`: El sector del usuario tiene una tarea asignada
- `CREATOR`: El usuario creo el expediente

---

### 3.3 get_case
**Proposito**: Obtener detalle completo de un expediente especifico.

**Parametros**:
- `case_id` (requerido) - UUID del expediente
- `include_documents` (opcional, default false) - Incluir lista de documentos
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Dame los detalles del expediente X"
- "Ver informacion completa del expediente"

---

### 3.4 get_case_history
**Proposito**: Obtener historial de movimientos de un expediente.

**Parametros**:
- `case_id` (requerido) - UUID del expediente
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Cual es el historial del expediente?"
- "Por donde paso este expediente?"
- "Quien trabajo en este expediente?"

**Tipos de movimiento**:
- `CREATION`: Expediente creado
- `TRANSFER`: Transferido a otro sector
- `ASSIGN`: Tarea asignada a otro sector
- `CLOSE_ASSIGN`: Tarea completada
- `DOCUMENT_ADDED`: Documento vinculado

---

### 3.5 get_case_documents
**Proposito**: Obtener documentos vinculados a un expediente.

**Parametros**:
- `case_id` (requerido) - UUID del expediente
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Que documentos tiene el expediente?"
- "Ver lista de documentos del expediente"

**Respuesta incluye**:
- Documentos oficiales firmados
- Documentos propuestos (borradores vinculados)
- URLs de PDF para descarga

---

### 3.6 get_case_permissions
**Proposito**: Ver que acciones puede realizar un usuario sobre un expediente.

**Parametros**:
- `case_id` (requerido) - UUID del expediente
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Puede el usuario transferir este expediente?"
- "Que acciones puede hacer sobre el expediente?"

**Permisos posibles**:
- `can_view`: Puede ver el expediente
- `can_transfer`: Puede transferir a otro sector
- `can_assign`: Puede asignar tareas
- `can_add_document`: Puede agregar documentos
- `can_archive`: Puede archivar

---

### 3.7 search_documents
**Proposito**: Buscar documentos con filtros y paginacion.

**Parametros** (todos opcionales, OAuth inyecta user_id y municipality_id):
- `tenant_id`: Si tienes acceso a multiples municipalidades
- `page` (default 1)
- `page_size` (default 20, max 100)
- `search` - Busqueda inteligente (ver abajo)
- `status` - "pending", "sent_to_sign", "signed", "rejected"
- `document_type` - Acronimo del tipo (ej: "INF", "DICT")
- `case_id` - Filtrar documentos de un expediente especifico

**BUSQUEDA INTELIGENTE (parametro `search`)**:
- **Case-insensitive**: "INFORME" = "informe" = "Informe"
- **Ignora acentos**: "tecnico" encuentra "informe tecnico"
- **Busqueda parcial**: "presu" encuentra "presupuesto anual"
- **Busca en**: reference (asunto), official_number, contenido HTML del documento
- **Ejemplo**: buscar "licitacion" encuentra documentos cuyo contenido menciona "licitacion" aunque no este en el titulo.
- **Minimo 2 caracteres** para activar busqueda.

**Cuando usar**:
- "Buscar documentos pendientes de firma"
- "Documentos de tipo INFORME"
- "Mis documentos firmados"
- "Buscar documentos que mencionen 'presupuesto'"

**Ejemplo de respuesta**:
```json
{
  "documents": [
    {
      "id": "eb47a51e-8f45-49ce-99c7-db8bd653ba8b",
      "reference": "Creacion EE-2026-000018-TXST-INTE",
      "display_status": "Firmado",
      "updated_at": "2026-01-20T13:40:02",
      "document_type": {
        "name": "Caratula",
        "acronym": "CAEX"
      },
      "user_role": "creator",
      "official_number": "CAEX-2026-00000079-TXST-OOPA"
    }
  ],
  "pagination": {
    "total": 18,
    "page": 1,
    "page_size": 20,
    "total_pages": 1
  }
}
```

---

### 3.8 get_document
**Proposito**: Obtener detalle completo de un documento (metadata, firmantes, estado).

**Parametros**:
- `document_id` (requerido) - UUID del documento
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Dame los detalles del documento X"
- "Ver quien debe firmar este documento"
- "Estado actual del documento"
- "A que expediente pertenece este documento?"

**Nota**: Este tool devuelve metadata del documento. Para obtener el **contenido HTML completo**, usar `get_document_content`.

---

### 3.9 get_document_content
**Proposito**: Obtener el contenido HTML completo de un documento oficial (firmado).

**Parametros**:
- `document_id` (requerido) - UUID del documento oficial
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**IMPORTANTE**: Solo funciona con documentos **OFICIALES** (firmados). No funciona con borradores ni documentos en proceso de firma.

**Cuando usar**:
- "Cual es el contenido del documento INF-2026-00001234?"
- "Dame el texto completo del informe X"
- "Necesito leer el contenido de este documento oficial"
- "Que dice el documento?"

**Ejemplo de respuesta**:
```json
{
  "document_id": "e8f1dbe5-123b-411a-9584-60f7a961e34c",
  "official_number": "INF-2026-00000060-TXST-TESO",
  "reference": "Informe sobre Smart Contracts en Gobierno",
  "content": {
    "html": "<p>Contenido del documento...</p>",
    "format": "html"
  },
  "document_type": {
    "name": "Informe",
    "acronym": "INF"
  },
  "signed_at": "2026-01-16T17:45:22"
}
```

**Diferencia con get_document**:
- `get_document`: Devuelve metadata, firmantes, estado, expediente vinculado
- `get_document_content`: Devuelve el **contenido HTML** del documento

---

### 3.10 get_pending_signatures
**Proposito**: Ver documentos pendientes de firma donde es el turno del usuario.

**Parametros**: Ninguno (OAuth inyecta user_id y municipality_id automaticamente).
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Que documentos tengo para firmar?"
- "Tengo firmas pendientes?"
- "Cuantos documentos esperan mi firma?"

**Nota importante**: Solo muestra documentos donde el usuario es el PROXIMO firmante. Si otros deben firmar antes, esos documentos no aparecen.

---

### 3.11 get_user_info
**Proposito**: Obtener informacion del usuario actual.

**Parametros**: Ninguno (OAuth inyecta user_id y municipality_id automaticamente).
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "En que sector estoy?"
- "Cual es mi departamento?"
- "Que roles tengo?"
- "A que otros sectores tengo acceso?"

**Respuesta incluye**:
- Datos basicos (nombre, email)
- Sector actual (department_name, department_acronym)
- Roles asignados
- Sectores adicionales con permisos (can_view, can_edit)

---

### 3.12 get_document_states
**Proposito**: Obtener catalogo de estados posibles de documentos.

**Parametros**: Ninguno (OAuth resuelve municipalidad automaticamente).
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Que estados puede tener un documento?"
- "Explicame los estados de documentos"

**Respuesta incluye**:
- states: lista de estados con display_state (nombre visible)
- mappings: diccionario codigo -> nombre para mapeo rapido

---

### 3.13 get_case_by_number
**Proposito**: Buscar expediente por numero exacto.

**Parametros**:
- `case_number` (requerido) - Numero exacto (ej: "EE-2026-00001234")
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Busca el expediente EE-2026-00001234"
- "Dame info del expediente numero X"

---

### 3.14 prepare_assignment
**Proposito**: Verificar permisos y obtener sectores disponibles antes de asignar.

**Parametros**:
- `case_id` (requerido) - UUID del expediente
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Puedo asignar este expediente?"
- "A que sectores puedo enviar este expediente?"

**Respuesta incluye**:
- success: true/false
- status: "OK" o "NOT_ALLOWED"
- user_sectors_in_case: sectores del usuario con rol en el expediente
- available_sectors: sectores destino disponibles

---

### 3.15 assign_case
**Proposito**: Asignar expediente a otro sector (sin transferir propiedad).

**Parametros**:
- `case_id` (requerido) - UUID del expediente
- `target_sector_id` (requerido) - UUID del sector destino
- `reason` (requerido) - Motivo de la asignacion (5-500 caracteres)
- `assigned_user_id` (opcional) - Usuario especifico asignado
- `create_official_doc` (opcional) - Generar documento PV automatico
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Asigna este expediente al sector Legal"
- "Envia el expediente a Hacienda para revision"

**IMPORTANTE**: La asignacion NO transfiere propiedad. El sector original mantiene el control.

---

### 3.16 close_assignment
**Proposito**: Cerrar una asignacion de expediente.

**Parametros**:
- `case_id` (requerido) - UUID del expediente
- `movement_id` (requerido) - UUID del movimiento a cerrar
- `reason` (requerido) - Razon del cierre (5-500 caracteres)
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Cierra la asignacion del expediente"
- "Completa la tarea asignada"

---

### 3.17 create_document
**Proposito**: Crear documento borrador.

**Parametros**:
- `document_type_acronym` (requerido) - Tipo de documento (INF, DICT, etc.)
- `reference` (requerido) - Descripcion/asunto del documento
- `case_id` (opcional) - Expediente a vincular
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Crea un informe nuevo"
- "Necesito hacer un dictamen"

**Respuesta incluye**:
- document_id: UUID del documento creado
- status: "draft"

---

### 3.18 save_document
**Proposito**: Guardar cambios en documento borrador.

**Parametros**:
- `document_id` (requerido) - UUID del documento
- `content` (opcional) - Contenido HTML
- `reference` (opcional) - Nueva descripcion
- `signers` (opcional) - Lista de firmantes [{user_id, email, is_numerator}]
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Actualiza el contenido del documento"
- "Agrega estos firmantes al documento"

**IMPORTANTE**: Solo funciona con documentos en estado 'draft' o 'rejected'.

---

### 3.19 start_signing
**Proposito**: Enviar documento al proceso de firma.

**Parametros**:
- `document_id` (requerido) - UUID del documento
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Envia el documento a firma"
- "Inicia el proceso de firma"

**Requisitos**:
- Debe tener al menos un firmante y un numerador asignados
- Solo el creador puede iniciar la firma
- Documento debe estar en estado 'draft'

**IMPORTANTE**: Una vez iniciada la firma, el documento no puede editarse.

---

### 3.20 list_my_tenants
**Proposito**: Ver a que municipalidades tienes acceso.

**Parametros**: Ninguno.

**Cuando usar**:
- "A que municipalidades tengo acceso?"
- Cuando recibes error "multi_tenant_selection_required"

**Respuesta incluye**:
- tenants: lista con tenant_id y name de cada municipalidad
- total: cantidad de municipalidades

---

### 3.21 reject_document
**Proposito**: Rechazar un documento en proceso de firma.

**Parametros**:
- `document_id` (requerido) - UUID del documento
- `reason` (requerido) - Motivo del rechazo
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Rechaza este documento"
- "No apruebo este informe, tiene errores"
- "Rechazar el dictamen por falta de datos"

**IMPORTANTE**:
- Solo el creador o un firmante activo puede rechazar
- El documento vuelve a estado 'rejected' y puede ser editado nuevamente
- El `reason` es obligatorio para trazabilidad

**ADVERTENCIA: El documento vuelve a estado rechazado. Confirmar con el usuario antes de ejecutar.**

---

### 3.22 propose_document
**Proposito**: Proponer un documento borrador para un expediente.

**Parametros**:
- `case_id` (requerido) - UUID del expediente
- `document_draft_id` (requerido) - UUID del documento borrador
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Propone este borrador para el expediente"
- "Asocia mi borrador de informe al expediente"
- "Vincula el draft al expediente para revision"

**IMPORTANTE**:
- Solo documentos en estado **draft** pueden proponerse
- La propuesta queda pendiente hasta que el sector administrador la acepte o rechace
- El creador del borrador debe tener acceso al expediente

---

### 3.23 reject_proposal
**Proposito**: Rechazar una propuesta de documento para un expediente.

**Parametros**:
- `case_id` (requerido) - UUID del expediente
- `proposed_id` (requerido) - UUID de la propuesta
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Rechaza la propuesta del documento"
- "No acepto este borrador para el expediente"

**ADVERTENCIA: La propuesta sera rechazada. Confirmar con el usuario.**

---

### 3.24 prepare_transfer
**Proposito**: Obtener sectores disponibles para transferir un expediente.

**Parametros**:
- `case_id` (requerido) - UUID del expediente
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "A que sectores puedo transferir este expediente?"
- "Ver opciones de transferencia"

**Respuesta incluye**:
- Lista de sectores con: sector_id, sector_name, department_name, user_count
- Solo muestra sectores del mismo municipio

---

### 3.25 get_case_templates
**Proposito**: Obtener templates disponibles para crear expedientes.

**Parametros**: Ninguno (OAuth resuelve usuario automaticamente).
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Que tipos de expedientes puedo crear?"
- "Mostrame los templates disponibles"

**Respuesta incluye**:
- Lista de templates con: template_id, name, acronym, description

---

### 3.26 search_users
**Proposito**: Buscar usuarios por nombre o email (autocompletado).

**Parametros**:
- `search` (requerido) - Texto de busqueda (minimo 4 caracteres). Debes buscar por nombre o apellido real, minimo 4 caracteres.
- `limit` (opcional, default 10) - Cantidad maxima de resultados
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Busca al usuario Juan Perez"
- "Quien es el email jperez@municipio.gob?"
- Para obtener user_id antes de asignar expedientes

**Respuesta incluye**:
- Lista de usuarios con: user_id, full_name, email, sector, department

---

### 3.27 get_notes
**Proposito**: Obtener notas/comunicaciones recibidas por el usuario.

**Parametros**:
- `page` (opcional, default 1) - Pagina
- `page_size` (opcional, default 20) - Resultados por pagina
- `unread_only` (opcional, default false) - Solo notas no leidas
- `search` (opcional) - Buscar en contenido de notas
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Tengo notas sin leer?"
- "Mostrame mis mensajes recibidos"
- "Busca notas sobre presupuesto"

**Respuesta incluye**:
- Lista de notas con: remitente, contenido, fecha, estado (leida/no leida)

---

### 3.28 search_document_by_number
**Proposito**: Buscar documento oficial por numero exacto.

**Parametros**:
- `doc_number` (requerido) - Numero oficial (ej: INF-2026-00000060-TXST-TESO)
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Busca el documento INF-2026-00000060"
- "Existe el dictamen DICT-2026-00001234?"
- "Dame info del documento con numero oficial X"

---

### 3.29 get_signature_details
**Proposito**: Ver detalles de firma: firmantes, estado de cada uno, orden.

**Parametros**:
- `document_id` (requerido) - UUID del documento
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Quien falta firmar este documento?"
- "Estado de las firmas del informe"
- "Que firmantes tiene y en que orden?"

---

### 3.30 get_sent_notes
**Proposito**: Obtener notas/comunicaciones enviadas por el usuario.

**Parametros**:
- `page` (opcional, default 1) - Pagina
- `page_size` (opcional, default 20) - Resultados por pagina
- `search` (opcional) - Buscar en contenido
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Que notas envie?"
- "Mis mensajes enviados"

---

### 3.31 get_archived_notes
**Proposito**: Obtener notas archivadas.

**Parametros**:
- `page` (opcional, default 1) - Pagina
- `page_size` (opcional, default 20) - Resultados por pagina
- `search` (opcional) - Buscar en contenido
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Mis notas archivadas"
- "Notas que archive"

---

### 3.32 get_note_detail
**Proposito**: Ver detalle completo de una nota especifica.

**Parametros**:
- `note_id` (requerido) - UUID de la nota
- `tenant_id` (opcional) - Si tienes acceso a multiples municipalidades

**Cuando usar**:
- "Abri esa nota"
- "Mostrame el contenido de la nota X"

---

## 4. Estrategias de Consulta

### Para buscar un expediente especifico

**Si tienes el UUID**:
```
get_case(case_id="uuid-aqui")
```

**Si tienes el numero de expediente**:
```
get_case_by_number(case_number="EE-2026-000018")
```
o
```
search_cases(search="EE-2026-000018")
```

**Si solo tienes descripcion**:
```
search_cases(search="panaderia") -> revisar resultados
```

### Para entender el flujo de un expediente

1. Obtener detalle: `get_case(case_id, include_documents=true)`
2. Obtener historial: `get_case_history(case_id)`
3. Analizar movimientos cronologicamente

### Para encontrar documentos pendientes de firma

```
get_pending_signatures()
```

### Para conocer el sistema

1. `get_document_types()` - Ver tipos de documentos disponibles
2. `get_document_states()` - Ver estados posibles de documentos
3. `get_case_templates()` - Ver tipos de expedientes
4. `get_user_info()` - Ver tu sector y permisos

### Para ver documentos de un expediente

**Opcion 1**: `get_case(case_id, include_documents=true)`
**Opcion 2**: `get_case_documents(case_id)`
**Opcion 3**: `search_documents(case_id="uuid-expediente")`

### Para leer el contenido de un documento oficial

1. Primero obtener el document_id (via `search_documents` o `get_case_documents`)
2. Luego: `get_document_content(document_id="uuid-documento")`

**Nota**: Solo funciona con documentos firmados (oficiales). Para borradores, el contenido esta disponible via `get_document`.

### Para crear un documento y enviarlo a firma (flujo completo)

1. `get_document_types()` -> elegir tipo
2. `create_document(document_type_acronym="INF", reference="Mi informe")` -> crear borrador
3. `save_document(document_id, content="<p>Contenido...</p>", signers=[...])` -> agregar contenido y firmantes
4. `start_signing(document_id)` -> enviar a firma

**Nota**: La firma del documento se realiza desde el Frontend o REST API, no desde MCP.

### Para buscar un documento por numero oficial

1. `search_document_by_number(doc_number="INF-2026-00000060-TXST-TESO")` -> obtener documento
2. Si necesitas el contenido: `get_document_content(document_id)` -> leer HTML

### Para ver el estado de firmas de un documento

1. `get_document(document_id)` -> ver metadata general
2. `get_signature_details(document_id)` -> ver firmantes, estado de cada uno y orden

### Para consultar notas

**Notas recibidas**: `get_notes()` o `get_notes(unread_only=true)` para solo no leidas
**Notas enviadas**: `get_sent_notes()`
**Notas archivadas**: `get_archived_notes()`
**Detalle de una nota**: `get_note_detail(note_id="uuid-nota")`

---

## 5. Errores Comunes

| Error | Causa | Solucion |
|-------|-------|----------|
| `Authorization required` | No hay token OAuth | Usa un cliente MCP con OAuth (Claude Code, ChatGPT, Gemini) |
| `Token invalido` | JWT expirado o mal formado | Re-autenticar via OAuth |
| `Usuario no encontrado` | Email del JWT no existe en BD | Crear usuario en la municipalidad |
| `multi_tenant_selection_required` | Usuario con multiples municipalidades | Usar `list_my_tenants` y especificar `tenant_id` |
| `Case not found` | UUID de expediente no existe | Verificar case_id |
| `Document not found` | UUID de documento no existe | Verificar document_id |
| `Access denied` | Usuario sin permisos | El usuario no tiene acceso a ese recurso |
| `Document is not in a signable state` | Documento no esta en proceso de firma | Verificar estado del documento |
| `Not your turn to sign` | No es tu turno de firmar | Verificar con get_pending_signatures |
| `Only drafts can be deleted` | Intentar eliminar doc no-borrador | Solo draft o rejected se pueden eliminar |

---

## 6. Mejores Practicas

### DO (Hacer)

1. **Conectar via OAuth**: Claude Code, ChatGPT y Gemini hacen login automatico
2. **Usar paginacion** para listas grandes (page_size maximo 100)
3. **Validar UUIDs** antes de usarlos en consultas de detalle
4. **Usar filtros** para reducir resultados (status, date_filter, document_type)
5. **Explicar los estados** al usuario si parece no entenderlos
6. **Usar tenant_id** si el usuario tiene acceso a multiples municipalidades
7. **Verificar permisos** antes de operaciones de escritura (get_case_permissions, prepare_assignment)
8. **Confirmar antes de acciones destructivas** - eliminar y rechazar son irreversibles o de alto impacto

### DON'T (No hacer)

1. **No inventar datos** que no esten en las respuestas
2. **No exponer UUIDs internos** innecesariamente al usuario
3. **No asumir permisos** - verificar con get_case_permissions si es necesario
4. **No hacer consultas masivas** - usar filtros y paginacion
5. **No ejecutar acciones de escritura sin confirmar** - siempre mostrar detalles al usuario antes de eliminar, rechazar o enviar a firma

### Formato de Respuestas

**Para listas**: Usar tablas cuando hay multiples campos
```
| Numero | Referencia | Estado | Fecha |
|--------|------------|--------|-------|
| EE-2026-001 | Licitacion equipos | Activo | 2026-01-20 |
```

**Para detalles**: Estructurar logicamente
```
**Expediente**: EE-2026-000018-TXST-INTE
- **Tipo**: Habilitacion Comercial
- **Estado**: Activo
- **Sector**: Obras Particulares
- **Documentos**: 6
```

---

## 7. Limitaciones Actuales

### Operaciones Disponibles (desde MCP)
- Crear y editar documentos borrador
- Iniciar proceso de firma y rechazar documentos
- Asignar expedientes entre sectores
- Proponer documentos y rechazar propuestas
- Buscar documentos por numero oficial
- Ver detalles de firma de cada firmante
- Consultar notas recibidas, enviadas, archivadas y detalle

### NO Puedes (desde MCP)
- NO puedes eliminar borradores (disponible via Frontend y REST API)
- NO puedes firmar documentos (disponible via Frontend y REST API)
- NO puedes crear expedientes (disponible via Frontend y REST API)
- NO puedes transferir propiedad de expedientes (disponible via Frontend y REST API)
- NO puedes vincular documentos oficiales a expedientes (disponible via Frontend y REST API)
- NO puedes aceptar propuestas de documentos (disponible via Frontend y REST API)
- NO puedes crear notas/mensajes (solo leer)
- NO puedes ver dashboard feed de actividad (disponible solo via Frontend)
- NO puedes archivar expedientes
- NO puedes modificar usuarios o sectores

### Permisos por Usuario
- El `user_id` determina que ve el agente
- Solo se muestran expedientes donde el usuario tiene acceso
- Los permisos reales dependen del rol del usuario en su sector
- Operaciones de escritura validan permisos en el backend

### Paginacion
- Maximo 100 resultados por pagina
- Siempre verificar `total_pages` para saber si hay mas resultados

---

## 8. Ejemplos de Interaccion

### Ejemplo 0: Primera conexion - Pedir la guia

**Usuario**: "Conectame al sistema GDI" o primera interaccion

**Agente**:
1. Llama `get_agent_guide()` para obtener la guia completa
2. Lee la guia y entiende las capacidades
3. Presenta al usuario:

```
Me he conectado al sistema GDI (Gestion Documental Inteligente).

Puedo ayudarte con:
- Buscar expedientes y documentos
- Crear documentos borrador y enviarlos a firma
- Asignar expedientes a otros sectores
- Ver historial, firmas pendientes y estado de firmas
- Consultar notas recibidas, enviadas y archivadas

Que necesitas?
```

---

### Ejemplo 1: Ver firmas pendientes

**Usuario**: "Tengo algo para firmar?"

**Agente**:
1. `get_pending_signatures()` -> obtener lista
2. Si hay documentos: mostrar tabla con detalles
3. "Para firmar estos documentos, usa el Frontend o la REST API."

---

### Ejemplo 2: Buscar documento por numero oficial

**Usuario**: "Busca el documento INF-2026-00000060"

**Agente**:
1. `search_document_by_number(doc_number="INF-2026-00000060")` -> encontrar documento
2. Si quiere mas detalle: `get_document(document_id)` -> metadata
3. Si quiere el contenido: `get_document_content(document_id)` -> HTML

---

### Ejemplo 3: Ver estado de firmas

**Usuario**: "Quien falta firmar el informe?"

**Agente**:
1. Identificar el documento (buscar si es necesario)
2. `get_signature_details(document_id)` -> ver firmantes y estado
3. Presentar: "Faltan 2 firmas: Juan Perez (pendiente), Maria Lopez (pendiente)"

---

### Ejemplo 4: Consultar notas

**Usuario**: "Tengo notas sin leer?"

**Agente**:
1. `get_notes(unread_only=true)` -> ver notas no leidas
2. Presentar lista
3. Si el usuario quiere ver una: `get_note_detail(note_id)` -> detalle completo

---

### Ejemplo 5: Crear documento y enviar a firma

**Usuario**: "Crea un informe para el expediente de licitacion, con contenido sobre resultados"

**Agente**:
1. `search_cases(search="licitacion")` -> encontrar expediente
2. `create_document(document_type_acronym="INF", reference="Informe de Resultados")`
3. `save_document(document_id, content="<p>Resultados de la licitacion...</p>", signers=[...])`
4. `start_signing(document_id)` -> enviar a firma
5. "El documento fue enviado a firma. Los firmantes lo encontraran en sus pendientes."

---

## 9. Resumen de Tools por Categoria

### Expedientes (8 tools)
| # | Tool | Tipo | Descripcion |
|---|------|------|-------------|
| 3.2 | search_cases | LECTURA | Buscar expedientes |
| 3.3 | get_case | LECTURA | Detalle de expediente |
| 3.4 | get_case_history | LECTURA | Historial de movimientos |
| 3.5 | get_case_documents | LECTURA | Documentos del expediente |
| 3.6 | get_case_permissions | LECTURA | Permisos sobre expediente |
| 3.13 | get_case_by_number | LECTURA | Buscar por numero exacto |
| 3.14 | prepare_assignment | LECTURA | Preparar asignacion |
| 3.15 | assign_case | ESCRITURA | Asignar tarea a sector |

### Documentos (10 tools)
| # | Tool | Tipo | Descripcion |
|---|------|------|-------------|
| 3.7 | search_documents | LECTURA | Buscar documentos |
| 3.8 | get_document | LECTURA | Detalle de documento |
| 3.9 | get_document_content | LECTURA | Contenido HTML |
| 3.10 | get_pending_signatures | LECTURA | Firmas pendientes |
| 3.17 | create_document | ESCRITURA | Crear borrador |
| 3.18 | save_document | ESCRITURA | Guardar borrador |
| 3.19 | start_signing | ESCRITURA | Iniciar firma |
| 3.21 | reject_document | ESCRITURA | Rechazar documento |
| 3.28 | search_document_by_number | LECTURA | Buscar por numero oficial |
| 3.29 | get_signature_details | LECTURA | Detalles de firma |

### Expedientes - Asignaciones (2 tools)
| # | Tool | Tipo | Descripcion |
|---|------|------|-------------|
| 3.16 | close_assignment | ESCRITURA | Cerrar asignacion |
| 3.24 | prepare_transfer | LECTURA | Preparar transferencia |

### Propuestas (2 tools)
| # | Tool | Tipo | Descripcion |
|---|------|------|-------------|
| 3.22 | propose_document | ESCRITURA | Proponer borrador |
| 3.23 | reject_proposal | ESCRITURA | Rechazar propuesta |

### Sistema y Catalogos (6 tools)
| # | Tool | Tipo | Descripcion |
|---|------|------|-------------|
| 3.0 | get_agent_guide | LECTURA | Guia del sistema |
| 3.1 | get_document_types | LECTURA | Tipos de documentos |
| 3.11 | get_user_info | LECTURA | Info del usuario |
| 3.12 | get_document_states | LECTURA | Estados de documentos |
| 3.20 | list_my_tenants | LECTURA | Municipalidades |
| 3.25 | get_case_templates | LECTURA | Templates de expedientes |

### Usuarios (1 tool)
| # | Tool | Tipo | Descripcion |
|---|------|------|-------------|
| 3.26 | search_users | LECTURA | Buscar usuarios |

### Notas (4 tools)
| # | Tool | Tipo | Descripcion |
|---|------|------|-------------|
| 3.27 | get_notes | LECTURA | Notas recibidas |
| 3.30 | get_sent_notes | LECTURA | Notas enviadas |
| 3.31 | get_archived_notes | LECTURA | Notas archivadas |
| 3.32 | get_note_detail | LECTURA | Detalle de nota |

---

## 10. Contacto y Soporte

Para problemas tecnicos con el MCP:
- Revisar logs en Railway: `railway logs --service gdi-mcp-server`
- Verificar conectividad a la BD
- Confirmar que las variables de Auth0 estan configuradas (AUTH0_DOMAIN, AUTH0_AUDIENCE)

---

*Documento actualizado: 2026-02-13*
*Version MCP: 2.2*
*Herramientas disponibles: 32*

## 11. Clientes Soportados

| Cliente | Estado | Notas |
|---------|--------|-------|
| Claude Code | Funciona | OAuth automatico via RFC 9728 |
| ChatGPT | Funciona | OAuth + /userinfo para email |
| Gemini | Soportado | OAuth estandar |

Todos los clientes MCP que soporten OAuth 2.0 funcionan con el servidor GDI.
