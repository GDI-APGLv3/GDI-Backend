# Conectar tu IA a GDI (Gestión Documental Inteligente)

GDI es un sistema gubernamental para gestionar expedientes y documentos.
Puedes conectar tu asistente IA (Claude, ChatGPT, Gemini) para consultar tus expedientes usando lenguaje natural.

**Acceso: Solo lectura** - Tu IA puede buscar y leer, no puede modificar datos.

---

## Datos de Conexión

| Campo | Valor |
|-------|-------|
| URL MCP | `https://mcp.gdilatam.com/mcp` |
| OpenAPI | `https://mcp.gdilatam.com/.well-known/openapi.json` |
| Auth Server | `https://<your-tenant>.auth0.com` |
| Protocolo | OAuth 2.0 |

---

## 1. Claude Code (CLI) - Recomendado

**Paso 1: Agregar servidor**
```bash
claude mcp add --transport http gdi https://mcp.gdilatam.com/mcp
```

**Paso 2: Autenticarte**
```
/mcp
```
Selecciona "gdi" → "Authenticate" → Se abre navegador → Login con tu cuenta GDI → Listo.

---

## 2. Claude Desktop

**Paso 1:** Edita el archivo de configuración:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gdi": {
      "type": "http",
      "url": "https://mcp.gdilatam.com/mcp"
    }
  }
}
```

**Paso 2:** Reinicia Claude Desktop

**Paso 3:** Al consultar expedientes, se abre navegador → Login → Listo.

---

## 3. ChatGPT (Custom GPT)

**Paso 1:** Ve a chat.openai.com → "Explore GPTs" → "Create"

**Paso 2:** En "Configure" → "Create new action" → "Import from URL":
```
https://mcp.gdilatam.com/.well-known/openapi.json
```

**Paso 3:** En "Authentication" selecciona **OAuth** (se configura automáticamente via DCR)

**Paso 4:** Guarda → Usa el GPT → Se abre navegador → Login con tu cuenta GDI → Listo.

---

## 4. Otros LLMs con MCP

URL: `https://mcp.gdilatam.com/mcp`
Discovery: `https://mcp.gdilatam.com/.well-known/oauth-protected-resource`

Protocolo: OAuth 2.0 (RFC 9728)

---

## Qué Puedes Preguntar

Una vez conectado, prueba estas consultas:

- "¿Qué expedientes tengo activos?"
- "Busca expedientes de Juan Pérez"
- "¿Qué documentos tiene el expediente EE-2026-000017?"
- "¿Tengo firmas pendientes?"
- "Dame un resumen del expediente de la panadería"
- "¿En qué sector está mi expediente?"

---

## Tools Disponibles (15)

| Tool | Descripción |
|------|-------------|
| `search_cases` | Buscar expedientes por número, asunto o contenido |
| `get_case` | Ver detalle de un expediente |
| `get_case_history` | Historial completo + resumen IA |
| `get_case_documents` | Documentos vinculados |
| `get_case_permissions` | Qué puedo hacer con este expediente |
| `search_documents` | Buscar documentos |
| `get_document` | Ver detalle + resumen IA |
| `get_document_content` | Contenido HTML completo |
| `get_pending_signatures` | Documentos esperando mi firma |
| `get_document_types` | Tipos de documentos disponibles |
| `get_sectors` | Sectores y departamentos |
| `get_user_info` | Mi información de usuario |
| `get_case_templates` | Tipos de expedientes |
| `list_my_tenants` | Mis municipalidades (multi-tenant) |
| `get_agent_guide` | Guía completa del sistema |

---

## Troubleshooting

### "Authentication required" / 401
- Ejecuta `/mcp` en Claude Code y selecciona "Authenticate"
- En ChatGPT, verifica que el OAuth esté bien configurado

### "Usuario no encontrado"
- Tu email no está registrado en GDI
- Contacta al administrador de tu municipalidad

### "multi_tenant_selection_required"
- Tienes acceso a múltiples municipalidades
- Usa `list_my_tenants` para ver cuáles
- Especifica `tenant_id` en las siguientes consultas

### ChatGPT: "Invalid redirect URI"
- El Callback URL no está registrado en Auth0
- Envía el Callback URL exacto al admin de GDI

---

## Soporte

- Documentación: https://docs.gdilatam.com
- Issues: Contacta al administrador de tu municipalidad
