# Mapa de Puertas - GDI Backend

> Ultima actualizacion: 2026-02-13
>
> **Frontend** = Backend :8000 (Auth0 JWT) | **MCP** = Gateway :8005 /mcp (OAuth RFC 9728) | **REST** = Gateway :8005 /api/v1/* (API Key)

---

## EXPEDIENTES

| # | Operacion | Descripcion | Front | MCP | REST |
|---|-----------|-------------|:-----:|:---:|:----:|
| 1 | Buscar expedientes | Buscar por texto, estado, fecha, sector | X | X | X |
| 2 | Buscar por numero | Buscar expediente por numero exacto | X | X | X |
| 3 | Detalle expediente | Datos completos con docs opcionales | X | X | X |
| 4 | Historial/movimientos | Historial con ai_summary | X | X | X |
| 5 | Documentos del exp | Listar docs oficiales y propuestos | X | X | X |
| 6 | Permisos sobre exp | Permisos del usuario sobre expediente | X | X | X |
| 7 | Crear expediente | Crear con caratula CAEX (PDFComposer+Notary) | X | | X |
| 8 | Transferir expediente | Cambiar propiedad a otro sector (genera PV) | X | | X |
| 9 | Asignar tarea | Asignar exp a sector sin transferir propiedad | X | X | X |
| 10 | Cerrar asignacion | Cerrar tarea asignada | X | | X |
| 11 | Preparar asignacion | Sectores disponibles para asignar | X | X | X |
| 12 | Preparar transferencia | Sectores disponibles para transferir | X | X | X |
| 13 | Vincular documento | Vincular doc oficial a expediente | X | | X |
| 14 | Proponer documento | Proponer borrador para expediente | X | X | X |
| 15 | Aceptar propuesta | Aceptar documento propuesto | X | | X |
| 16 | Rechazar propuesta | Rechazar documento propuesto | X | X | X |
| 17 | Usuarios de sector | Usuarios activos de un sector especifico | X | | X |
| 18 | Sectores disponibles (legacy) | Sectores para transferencia (endpoint viejo) | X | | |
| 19 | Subsanar documento | Reemplazar doc oficial erroneo en expediente | X | | |

## DOCUMENTOS

| # | Operacion | Descripcion | Front | MCP | REST |
|---|-----------|-------------|:-----:|:---:|:----:|
| 20 | Buscar documentos | Buscar por texto en contenido completo | X | X | X |
| 21 | Buscar por numero oficial | Buscar doc por numero exacto (ej: IF-2026-001) | X | X | X |
| 22 | Detalle documento | Detalle con ai_summary | X | X | X |
| 23 | Contenido HTML | Contenido HTML completo (solo oficiales) | X | X | X |
| 24 | URL documento oficial | URL firmada de R2 para descarga PDF | X | | X |
| 25 | Detalles de firma | Info firmantes, estados, fechas de firma | X | X | X |
| 26 | Detalles para editor | Datos para el editor web de borradores | X | | |
| 27 | Pendientes de firma | Documentos esperando MI firma | X | X | X |
| 28 | Verificar permisos firmante | Verificar si usuario puede firmar documento | X | | X |
| 29 | Autocompletado docs | Autocompletar nombre/numero de documentos | X | | |
| 30 | Crear documento | Crear borrador nuevo (draft) | X | X | X |
| 31 | Importar PDF externo | Subir PDF externo como documento importado | X | | X |
| 32 | Guardar borrador | Guardar cambios en borrador (PATCH) | X | X | X |
| 33 | Reemplazar PDF importado | Reemplazar PDF de documento importado | X | | X |
| 34 | Preview documento | Generar preview PDF con marca de agua | X | | |
| 35 | Preview auto-save | Preview con autoguardado | X | | |
| 36 | Enviar a firmar | Iniciar proceso de firma | X | X | X |
| 37 | Firmar documento | Firma digital (irreversible) | X | | X |
| 38 | Rechazar documento | Rechazar doc en proceso de firma | X | X | X |
| 39 | Eliminar borrador | Eliminar draft/rejected | X | | X |

## SISTEMA / CATALOGOS

| # | Operacion | Descripcion | Front | MCP | REST |
|---|-----------|-------------|:-----:|:---:|:----:|
| 40 | Tipos de documentos | Catalogo: INF, DICT, CAEX, PV, etc. | X | X | X |
| 41 | Estados de documentos | Catalogo: draft, sent_to_sign, signed, etc. | X | X | X |
| 42 | Sectores y departamentos | Catalogo de sectores con departamentos | X | | X |
| 43 | Templates de expedientes | Templates disponibles para crear expedientes | X | X | X |
| 44 | Info usuario (por ID) | Datos de un usuario especifico | X | X | X |
| 45 | Buscar usuarios | Buscar por nombre/apellido (min 4 chars MCP) | X | X | X |
| 46 | Listar todos los usuarios | Lista paginada de usuarios activos | X | | X |
| 47 | Guia del agente IA | Documentacion completa del sistema para IA | | X | |
| 48 | Listar mis tenants | Municipalidades disponibles (multi-tenant) | | X | |

## DASHBOARD

| # | Operacion | Descripcion | Front | MCP | REST |
|---|-----------|-------------|:-----:|:---:|:----:|
| 49 | Feed de actividad | Actividad reciente del usuario | X | | |
| 50 | Estadisticas | Contadores y metricas del dashboard | X | | |

## NOTAS

| # | Operacion | Descripcion | Front | MCP | REST |
|---|-----------|-------------|:-----:|:---:|:----:|
| 51 | Notas recibidas | Listar notas recibidas (paginado, filtros) | X | X | X |
| 52 | Notas enviadas | Listar notas enviadas | X | X | X |
| 53 | Notas archivadas | Listar notas archivadas | X | X | X |
| 54 | Archivar/desarchivar nota | Toggle archivo de nota | X | | X |
| 55 | Detalle de nota | Ver nota completa | X | X | X |

## AUTH / SISTEMA INTERNO

| # | Operacion | Descripcion | Front | MCP | REST |
|---|-----------|-------------|:-----:|:---:|:----:|
| 56 | Onboarding | Info multi-tenant del usuario autenticado | X | | |
| 57 | Login | Autenticacion via Auth0 | X | | |
| 58 | Perfil usuario | Ver perfil propio | X | | |
| 59 | Actualizar perfil | Editar perfil propio | X | | |
| 60 | Mis documentos | Documentos del usuario con filtros avanzados | X | | |
| 61 | Health check | Estado de servicios | X | | |

---

## RESUMEN

| Puerta | Total | Lectura | Escritura |
|--------|:-----:|:-------:|:---------:|
| **Frontend** (:8000) | 59 | 42 | 17 |
| **MCP** (:8005) | 32 | 23 | 9 |
| **REST** (:8005) | 44 | 29 | 15 |

### Solo Frontend (no disponible externamente)
- Editor details, autocompletado, preview, auto-save preview
- Sectores disponibles (legacy), subsanar documento
- Perfil (ver/editar), mis documentos, onboarding, login
- Dashboard stats, dashboard feed, health check

### Solo MCP (no disponible por REST)
- `get_agent_guide` - Guia para agentes IA
- `list_my_tenants` - Seleccion multi-tenant

### Operaciones bloqueadas de MCP (alto impacto)
Estas operaciones estan en REST pero NO en MCP por ser irreversibles:
- Crear expediente (genera CAEX + PV)
- Transferir expediente (cambia propiedad)
- Cerrar asignacion
- Vincular documento a expediente
- Aceptar propuesta
- Firmar documento (firma digital irreversible)
- Eliminar borrador (sin retorno)

### Operaciones MCP con advertencia
Estas tools MCP incluyen ADVERTENCIA para que el agente confirme con el usuario:
- `reject_document` - Rechazar documento (sin retorno)
- `reject_proposal` - Rechazar propuesta (sin retorno)
