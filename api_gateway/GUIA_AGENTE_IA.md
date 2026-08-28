# GDI - Guia Agente IA (v3.2)

Sistema de Gestion Documental para gobiernos LATAM. Expedientes, documentos oficiales, firma digital multi-firmante.

**Documentacion oficial (publica): https://gdi-agplv3.github.io/GDI-Docs/** — ante CUALQUIER duda sobre un concepto, flujo, campo o error, consultala y verifica ahi. Es la fuente de verdad.

## Capacidades

**LECTURA**: Buscar expedientes/documentos/legajos, leer contenido HTML, ver historial, firmas pendientes, notas, memos, tipos, estados, usuarios. Busqueda por significado (semantic_search).

**ESCRITURA**: Crear borradores (incluidas NOTAs y MEMOs via create_document con el tipo correspondiente), guardar contenido+firmantes, iniciar firma, rechazar documentos, asignar expedientes, proponer/rechazar propuestas.

**NO puedes**: Crear expedientes, transferir propiedad, firmar, eliminar borradores, vincular oficiales, archivar. Estas acciones se hacen desde la app web.

## Conceptos clave

- **Expediente**: Carpeta de tramite que agrupa documentos
- **Documento**: Borrador (draft) -> en firma (pending_sign) -> firmado (signed). Tambien: rejected, cancelled
- **NOTA / MEMO**: Son tipos de documento. NOTA = comunicacion entre sectores. MEMO = comunicacion entre personas. Se crean con create_document.
- **Legajo (RLM)**: Registro Legajo Multiproposito. Ficha estructurada por familia de registro.
- **Sector**: Area del municipio (Legal, Hacienda, etc.)
- **Visibilidad del documento**: cada documento es **interno** (default), **reservado** (acceso restringido) o **publico** (GDI-098). En search_documents/get_document aparece como `document_type_visibility`, `is_reserved`, `is_public`. No expongas contenido reservado a quien no corresponde.
- **Campos controlados (FFCC / formularios)**: algunos tipos tienen `has_fields=true` (p.ej. formularios). La carga de esos campos por MCP **no esta soportada aun**; usa tipos sin campos o deriva a la app web.
- **Multi-tenant**: Cada municipalidad es independiente. Si el usuario tiene varias, usar `tenant_id`

## Tools (42) - Referencia rapida

### Expedientes
| Tool | Accion |
|------|--------|
| search_cases | Ver expedientes del usuario/sector (por estado, fecha, texto). Usar para "mis expedientes", "expedientes activos", "expedientes de esta semana" |
| get_case | Detalle (usar include_documents=true para docs) |
| get_case_history | Historial cronologico + ai_summary |
| get_case_documents | Docs oficiales y propuestos |
| get_case_permissions | Que puede hacer el usuario |
| get_case_by_number | Buscar por numero exacto (EE-2026-...) |
| prepare_assignment | Ver sectores disponibles antes de asignar |
| assign_case | Asignar a sector (no transfiere propiedad) |
| get_case_responsibles | Ver responsables (admin/adicionales) del expediente |
| add_case_responsible | Agregar responsable ADMIN/ADDITIONAL. El `sector_id` debe ser el sector del responsable **con acceso a ese expediente** (obtenelo de prepare_assignment.user_sectors_in_case o get_user_info); un sector sin acceso da "Error de validacion" |
| remove_case_responsible | Quitar responsable, soft delete (valida ver+editar) |

### Documentos
| Tool | Accion |
|------|--------|
| search_documents | Buscar por texto, tipo, estado |
| get_document | Metadata + ai_summary |
| get_document_content | HTML completo (solo oficiales firmados) |
| get_pending_signatures | Docs esperando MI firma |
| get_signature_details | Estado de cada firmante |
| search_document_by_number | Buscar por numero oficial |
| create_document | Crear borrador. El `document_type_acronym` debe existir: **verificalo SIEMPRE con get_document_types** (ojo: Informe = `IF`, Dictamen = `DICTA`). Para NOTA/MEMO pasar recipients={to,cc,bcc} |
| save_document | Guardar contenido + firmantes. `signers=[{user_id, email, is_numerator}]`. Para firmarte a VOS mismo: `user_id`/`email` de get_user_info. Para otros firmantes: search_users |
| start_signing | Enviar a firma (irreversible) |
| reject_document | Rechazar con motivo |

### Propuestas
| Tool | Accion |
|------|--------|
| propose_document | Proponer borrador para expediente |
| reject_proposal | Rechazar propuesta |

### Notas (recibidas/enviadas)
| Tool | Accion |
|------|--------|
| get_notes | Recibidas (unread_only=true para no leidas) |
| get_sent_notes | Enviadas |
| get_archived_notes | Archivadas |
| get_note_detail | Detalle de una nota |

### Memos (recibidos/enviados)
| Tool | Accion |
|------|--------|
| get_memos | Recibidos (unread_only=true para no leidos) |
| get_sent_memos | Enviados |
| get_archived_memos | Archivados |
| get_memo_detail | Detalle de un memo |

### Legajos (RLM)
| Tool | Accion |
|------|--------|
| search_records | Buscar legajos por familia, estado, texto |
| get_record | Detalle completo de un legajo |
| get_registry_families | Familias de registro disponibles |

### Busqueda semantica ⭐ HERRAMIENTA PRINCIPAL DE BUSQUEDA
| Tool | Accion |
|------|--------|
| semantic_search | **PRIMERA OPCION cuando el usuario quiere encontrar algo.** Busca en el CONTENIDO de todos los documentos oficiales por significado usando IA. Encuentra documentos aunque el usuario no use las palabras exactas. Usar siempre que la consulta sea "busca algo sobre X", "encontra documentos de Y", "hay algo relacionado con Z". |

### Sistema
| Tool | Accion |
|------|--------|
| get_agent_guide | Esta guia |
| get_document_types | Tipos habilitados (IF, DICTA, NOTA, MEMO...) |
| get_document_states | Estados posibles |
| get_case_templates | Templates de expedientes |
| get_sectors | Sectores y departamentos |
| get_user_info | Mi sector, roles, permisos |
| list_my_tenants | Mis municipalidades |
| search_users | Buscar por nombre/email (min 4 chars) |

## Decidir que tool de busqueda usar

Esta es la decision mas importante. Ante cualquier consulta de busqueda:

**"Ver mis expedientes / los del sector / los activos"** → `search_cases`
- El usuario quiere ver expedientes que le pertenecen o tiene acceso
- Ejemplos: "mostrame mis expedientes", "que expedientes tengo activos", "expedientes de esta semana"

**"Encontrar algo sobre un tema"** → `semantic_search` (PRIMERA OPCION)
- El usuario busca por concepto, no sabe exactamente donde esta
- Ejemplos: "busca documentos sobre habilitaciones", "algo relacionado con multas de transito", "encontra documentos que hablen de licitaciones"
- Busca en el CONTENIDO de todos los documentos oficiales por significado

**"Buscar un expediente especifico"** → `search_cases(search="...")`
- Cuando conoce el numero, nombre de empresa, persona o referencia exacta

**"Buscar un documento por numero"** → `search_document_by_number`
- Conoce el numero oficial: IF-2026-..., DICTA-2026-..., etc.

**Regla de oro**: Si el usuario dice "busca", "encontra", "hay algo sobre", "documentos que hablen de" → `semantic_search`. Si dice "mis expedientes", "ver expedientes", "expedientes del sector" → `search_cases`.

## Flujos comunes

**Firmas pendientes**: `get_pending_signatures()`

**Ver mis expedientes**: `search_cases()` -> listar con case_number, reference, ai_summary

**Encontrar algo sobre un tema**: `semantic_search(query="...")` -> mostrar resultados con similarity y chunk_text relevante

**Investigar expediente especifico**: `search_cases(search="panaderia")` -> `get_case_history(case_id)` -> narrar con ai_summary

**Crear y enviar a firma**: `get_document_types()` (confirmar el acronimo real) -> `create_document(document_type_acronym, reference[, case_id])` (pasar `case_id` para vincularlo a un expediente) -> `save_document(id, content, signers=[{user_id, email, is_numerator:true}])` -> `start_signing(id)`.
   - **De donde salen los signers**: para firmarte a VOS mismo, `user_id` y `email` los da `get_user_info`. Para otros firmantes, `search_users`.
   - `start_signing` exige al menos un firmante y un numerador (`is_numerator:true`), y solo el **creador** del documento puede iniciarla.

**Crear NOTA** (entre sectores): `get_document_types()` -> confirmar acronimo "NOTA" -> `create_document(document_type_acronym="NOTA", reference, recipients={to:[sector_uuid], cc:[], bcc:[]})` -> `save_document` -> `start_signing`
   - Los **UUID de sectores** destino salen de `get_sectors()` (lista completa de sectores con su departamento), de `prepare_assignment(case_id).available_sectors` (usando cualquier expediente accesible) o de `get_user_info` (tus propios sectores).

**Crear MEMO** (entre personas): `search_users()` para UUIDs -> `create_document(document_type_acronym="MEMO", reference, recipients={to:[user_uuid], cc:[], bcc:[]})` -> `save_document` -> `start_signing`

**Leer documento oficial**: `search_document_by_number("IF-2026-...")` -> `get_document_content(id)`

## Reglas

- Confirmar con el usuario antes de rechazar o enviar a firma
- No inventar datos, no exponer UUIDs innecesariamente
- Usar paginacion (max 100 por pagina)
- Busqueda textual es case-insensitive e ignora acentos
- Busqueda semantica solo sobre documentos oficiales (firmados)
- get_document_content solo funciona con docs firmados (oficiales)
- Si recibes "multi_tenant_selection_required", usar list_my_tenants
- Ante un concepto, campo o error que no conozcas, consulta la documentacion oficial (link al inicio) antes de responder. No inventes comportamiento.

## Errores frecuentes

| Error | Solucion |
|-------|----------|
| Authorization required | Re-autenticar via OAuth |
| multi_tenant_selection_required | Usar list_my_tenants + tenant_id |
| Case/Document not found | Verificar UUID |
| Access denied | Usuario sin permisos para ese recurso |
| Not your turn to sign | Verificar con get_pending_signatures |
| Error de validacion (add_case_responsible) | `sector_id` sin acceso al expediente — tomarlo de prepare_assignment.user_sectors_in_case o get_user_info |
