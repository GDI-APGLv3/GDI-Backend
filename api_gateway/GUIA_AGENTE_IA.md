# GDI - Guia Agente IA (v3.1)

Sistema de Gestion Documental para gobiernos LATAM. Expedientes, documentos oficiales, firma digital multi-firmante.

Docs completos: https://gdi-apglv3.github.io/GDI-Docs/

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
- **Multi-tenant**: Cada municipalidad es independiente. Si el usuario tiene varias, usar `tenant_id`

## Tools (39) - Referencia rapida

### Expedientes
| Tool | Accion |
|------|--------|
| search_cases | Buscar por texto, numero, estado, fecha |
| get_case | Detalle (usar include_documents=true para docs) |
| get_case_history | Historial cronologico + ai_summary |
| get_case_documents | Docs oficiales y propuestos |
| get_case_permissions | Que puede hacer el usuario |
| get_case_by_number | Buscar por numero exacto (EE-2026-...) |
| prepare_assignment | Ver sectores disponibles antes de asignar |
| assign_case | Asignar a sector (no transfiere propiedad) |

### Documentos
| Tool | Accion |
|------|--------|
| search_documents | Buscar por texto, tipo, estado |
| get_document | Metadata + ai_summary |
| get_document_content | HTML completo (solo oficiales firmados) |
| get_pending_signatures | Docs esperando MI firma |
| get_signature_details | Estado de cada firmante |
| search_document_by_number | Buscar por numero oficial |
| create_document | Crear borrador de cualquier tipo (INF, DICT, NOTA, MEMO...). Para NOTA/MEMO pasar recipients={to,cc,bcc} |
| save_document | Guardar contenido + firmantes |
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

### Busqueda semantica
| Tool | Accion |
|------|--------|
| semantic_search | Busca documentos oficiales por significado (RAG con embeddings). Usar cuando la consulta es conceptual y no textual literal. |

### Sistema
| Tool | Accion |
|------|--------|
| get_agent_guide | Esta guia |
| get_document_types | Tipos habilitados (IF, DICTA, NOTA, MEMO...) |
| get_document_states | Estados posibles |
| get_case_templates | Templates de expedientes |
| get_user_info | Mi sector, roles, permisos |
| list_my_tenants | Mis municipalidades |
| search_users | Buscar por nombre/email (min 4 chars) |

## Flujos comunes

**Firmas pendientes**: `get_pending_signatures()`

**Investigar expediente**: `search_cases(search="panaderia")` -> `get_case_history(case_id)` -> narrar con ai_summary

**Crear y firmar documento**: `create_document(type, ref)` -> `save_document(id, content, signers)` -> `start_signing(id)`

**Crear NOTA** (entre sectores): `get_document_types()` -> confirmar acronimo "NOTA" -> `create_document(document_type_acronym="NOTA", reference, recipients={to:[sector_uuid], cc:[], bcc:[]})` -> `save_document` -> `start_signing`

**Crear MEMO** (entre personas): `search_users()` para UUIDs -> `create_document(document_type_acronym="MEMO", reference, recipients={to:[user_uuid], cc:[], bcc:[]})` -> `save_document` -> `start_signing`

**Leer documento oficial**: `search_document_by_number("IF-2026-...")` -> `get_document_content(id)`

**Buscar por significado** (cuando la consulta es conceptual): `semantic_search(query="deudas de tasa por servicios generales")` -> ranking por similitud semantica.

## Reglas

- Confirmar con el usuario antes de rechazar o enviar a firma
- No inventar datos, no exponer UUIDs innecesariamente
- Usar paginacion (max 100 por pagina)
- Busqueda textual es case-insensitive e ignora acentos
- Busqueda semantica solo sobre documentos oficiales (firmados)
- get_document_content solo funciona con docs firmados (oficiales)
- Si recibes "multi_tenant_selection_required", usar list_my_tenants

## Errores frecuentes

| Error | Solucion |
|-------|----------|
| Authorization required | Re-autenticar via OAuth |
| multi_tenant_selection_required | Usar list_my_tenants + tenant_id |
| Case/Document not found | Verificar UUID |
| Access denied | Usuario sin permisos para ese recurso |
| Not your turn to sign | Verificar con get_pending_signatures |
