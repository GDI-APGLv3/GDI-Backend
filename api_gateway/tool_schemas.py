from typing import Any, Dict, List

TOOL_SCHEMAS: List[Dict[str, Any]] = [
        {
            "name": "search_cases",
            "description": """🔍 BUSCADOR INTELIGENTE DE EXPEDIENTES - Busca en TODO el contenido.

El parámetro 'search' busca en:
- Número de expediente (EE-2026-000017)
- Asunto/referencia del expediente
- Contenido de TODOS los documentos del expediente
- Nombres de personas, empresas, direcciones, etc.

EJEMPLOS:
- "Busca expedientes de Juan Pérez" → search="Juan Pérez"
- "Expedientes de Av. San Martín 123" → search="San Martín 123"
- "¿Tengo algo de Panadería Don Luis?" → search="Panadería Don Luis"
- "Expedientes sobre habilitación comercial" → search="habilitación comercial"

RESPUESTA: Lista con case_number, reference, ai_summary (resumen IA del expediente), short_ai_summary (resumen corto 1-2 oraciones).
Para ver historial completo → get_case_history con el case_id.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"},
                    "page": {"type": "integer", "description": "Página (default 1)", "default": 1},
                    "page_size": {"type": "integer", "description": "Resultados por página (default 20, max 100)", "default": 20},
                    "search": {"type": "string", "description": "Buscar por número de expediente (ej: EE-2026-000017) o por referencia/asunto"},
                    "status": {"type": "string", "description": "Estado: active (en trámite), inactive, archived (cerrado)", "enum": ["active", "inactive", "archived"]},
                    "date_filter": {"type": "string", "description": "Filtro temporal: hoy, ayer, ultimos_7_dias, ultimos_30_dias", "enum": ["hoy", "ayer", "ultimos_7_dias", "ultimos_30_dias"]},
                    "sector_filter": {"type": "string", "description": "Filtrar por sector (usar acronym del sector, ej: HAC, LEGAL)"}
                },
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "cases": {
                        "type": "array",
                        "description": "Lista de expedientes encontrados",
                        "items": {
                            "type": "object",
                            "properties": {
                                "case_id": {"type": "string"},
                                "case_number": {"type": "string"},
                                "reference": {"type": "string"},
                                "status": {"type": "string"},
                                "ai_summary": {"type": "string"},
                                "short_ai_summary": {"type": "string"},
                                "created_at": {"type": "string"}
                            }
                        }
                    },
                    "total": {"type": "integer", "description": "Total de expedientes encontrados"},
                    "page": {"type": "integer"},
                    "page_size": {"type": "integer"},
                    "total_pages": {"type": "integer"}
                }
            }
        },
        {
            "name": "get_case",
            "description": """Obtiene el detalle completo de UN expediente específico. Usa esta tool para responder:
- "Dame los detalles del expediente EE-2026-000017"
- "¿Quién administra este expediente?"
- "¿Qué documentos tiene el expediente X?"
- "¿En qué sector está el expediente?"

USA include_documents=true si necesitas ver los documentos vinculados (oficiales firmados y propuestos en borrador).

RESPUESTA: case_number, reference (asunto), template (tipo de expediente), admin_sector (sector que lo administra actualmente), assigned_sectors (sectores con acceso), y opcionalmente documents.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente (obtenido de search_cases)"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"},
                    "include_documents": {"type": "boolean", "description": "true para incluir lista de documentos oficiales y propuestos", "default": False},
                    "include_movements": {"type": "boolean", "description": "true para incluir lista plana de movimientos (sin ai_summary, distinto de get_case_history)", "default": False}
                },
                "required": ["case_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "case_number": {"type": "string"},
                    "reference": {"type": "string"},
                    "status": {"type": "string"},
                    "template": {"type": "object", "description": "Tipo/template del expediente"},
                    "admin_sector": {"type": "object", "description": "Sector administrador actual"},
                    "assigned_sectors": {"type": "array", "items": {"type": "object"}, "description": "Sectores con acceso"},
                    "ai_summary": {"type": "string"},
                    "short_ai_summary": {"type": "string"},
                    "created_at": {"type": "string"},
                    "documents": {"type": "object", "description": "Documentos vinculados (solo si include_documents=true)"},
                    "movements": {"type": "object", "description": "Movimientos del expediente (solo si include_movements=true)"}
                }
            }
        },
        {
            "name": "get_case_history",
            "description": """⭐ PRIMERA TOOL A USAR cuando preguntan por un expediente específico.

Obtiene historial cronológico con resúmenes IA de cada documento.

USA ESTA TOOL PARA:
- "¿Qué pasó con el expediente EE-2026-000017?"
- "Contame sobre el expediente de la panadería"
- "¿En qué estado está mi trámite?"

RESPUESTA INCLUYE:
- ai_summary: RESUMEN INTELIGENTE del expediente
- short_ai_summary: RESUMEN CORTO del expediente (1-2 oraciones)
- movements: historial donde cada movimiento tiene:
  - created_at: fecha
  - message: acción realizada
  - resume: RESUMEN IA del documento (USAR para la narrativa)
- documents: lista con ai_summary de cada uno

⚠️ INSTRUCCIÓN CRÍTICA: USA LOS RESÚMENES

LEE el campo `ai_summary` de cada documento y construye una NARRATIVA de 1-2 párrafos.

EJEMPLO DE RESPUESTA CORRECTA:
"El 28/01/2026 se inició el expediente de Habilitación Comercial para JUGUETERIA FANTASIA S.A. en Calle Belgrano 789. La empresa solicitó habilitación para un local de 120 m² dedicado a la venta de juguetes, adjuntando escritura, planos y habilitación de bomberos.

Se realizó inspección que verificó: matafuegos vigentes, salida de emergencia señalizada y estanterías fijadas. El informe técnico fue favorable. El dictamen legal aprobó la solicitud. Se emitió el Certificado de Habilitación (HCOM-2026-00000210) con validez de un año."

❌ NUNCA: "El expediente trata sobre X. Está en el sector Y."
✅ SIEMPRE: Usa fechas + datos de resúmenes + narrativa 1-2 párrafos.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["case_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "case_number": {"type": "string"},
                    "reference": {"type": "string"},
                    "ai_summary": {"type": "string", "description": "Resumen inteligente del expediente"},
                    "short_ai_summary": {"type": "string"},
                    "movements": {
                        "type": "array",
                        "description": "Historial cronológico de movimientos",
                        "items": {
                            "type": "object",
                            "properties": {
                                "created_at": {"type": "string"},
                                "message": {"type": "string"},
                                "resume": {"type": "string", "description": "Resumen IA del documento asociado"}
                            }
                        }
                    },
                    "documents": {"type": "array", "items": {"type": "object"}}
                }
            }
        },
        {
            "name": "get_case_documents",
            "description": """Lista los documentos vinculados a un expediente, separados en oficiales (firmados) y propuestos (borradores). Usa esta tool para:
- "¿Qué documentos tiene el expediente X?"
- "¿Cuántos documentos firmados hay?"
- "¿Hay documentos pendientes de firma en este expediente?"

RESPUESTA:
- official: documentos ya firmados con número oficial (ej: INF-2026-00001234-TXST-LEGAL)
- proposed: borradores vinculados pendientes de firma
- total_official y total_proposed: conteos

NOTA: Para ver el contenido de un documento específico, usa get_document con el document_id.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["case_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "official": {
                        "type": "array",
                        "description": "Documentos oficiales firmados",
                        "items": {"type": "object"}
                    },
                    "proposed": {
                        "type": "array",
                        "description": "Documentos propuestos (borradores)",
                        "items": {"type": "object"}
                    },
                    "total_official": {"type": "integer"},
                    "total_proposed": {"type": "integer"}
                }
            }
        },
        {
            "name": "get_case_permissions",
            "description": """Consulta qué acciones puede realizar un usuario sobre un expediente específico. Usa esta tool para:
- "¿Puedo transferir este expediente?"
- "¿Qué puedo hacer con el expediente X?"
- "¿Tengo permisos para archivar?"

RESPUESTA (booleanos):
- can_view: puede ver el expediente
- can_transfer: puede transferir a otro sector
- can_assign: puede asignar sectores adicionales
- can_archive: puede archivar/cerrar el expediente
- can_link_documents: puede vincular documentos
- can_create_movements: puede crear movimientos
- ownership_level: nivel de propiedad (owner, creator, participant, viewer)""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["case_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "can_view": {"type": "boolean"},
                    "can_transfer": {"type": "boolean"},
                    "can_assign": {"type": "boolean"},
                    "can_archive": {"type": "boolean"},
                    "can_link_documents": {"type": "boolean"},
                    "can_create_movements": {"type": "boolean"},
                    "can_subsanar": {"type": "boolean"},
                    "ownership_level": {"type": "string", "description": "owner, creator, participant o viewer"}
                }
            }
        },
        {
            "name": "search_documents",
            "description": """🔍 BUSCADOR INTELIGENTE DE DOCUMENTOS - Busca en TODO el contenido.

El parámetro 'search' busca en:
- Número de documento (INF-2026-00001234)
- Asunto/referencia del documento
- Contenido COMPLETO del documento (texto, tablas, etc.)
- Nombres de personas, empresas, direcciones, montos, fechas, etc.

EJEMPLOS DE BÚSQUEDA:
- "Busca documentos de María García" → search="María García"
- "Informes sobre presupuesto" → search="presupuesto"
- "¿Dónde mencionamos calle Belgrano?" → search="Belgrano"

FILTROS:
- status: "En edición" (borrador/rechazado), "Firmar ahora" (te toca firmar), "En proceso de firma" (esperando otros), "Firmado" (oficial)
- document_type: INF, DICT, CAEX, etc.

EJEMPLO DE RESPUESTA AL USUARIO para "¿Qué tengo en mi buzón?":
"Tenés 12 documentos:
- 2 esperando tu firma (usa get_pending_signatures para verlos)
- 3 en proceso de firma (esperando otros firmantes)
- 5 borradores en edición
- 2 documentos oficiales firmados esta semana"

Para contenido completo → get_document_content.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"},
                    "page": {"type": "integer", "description": "Página (default 1)", "default": 1},
                    "page_size": {"type": "integer", "description": "Resultados por página (default 20, max 100)", "default": 20},
                    "search": {"type": "string", "description": "Buscar por número de documento (ej: INF-2026-00001234)"},
                    "status": {"type": "string", "description": "Filtrar por estado visual. Valores: \"En edición\" (borradores y rechazados), \"Firmar ahora\" (te toca firmar), \"En proceso de firma\" (esperando otros firmantes), \"Firmado\" (documentos oficiales).", "enum": ["En edición", "Firmar ahora", "En proceso de firma", "Firmado"]},
                    "document_type": {"type": "string", "description": "Filtrar por tipo de documento (acronym, ej: INF, DICT, CAEX). Usa get_document_types para ver tipos disponibles."},
                    "case_id": {"type": "string", "description": "Filtrar documentos vinculados a un expediente específico"},
                    "min_signers": {"type": "integer", "description": "Filtrar documentos con mínimo N firmantes (ej: 2 para docs con 2 o más firmas)"}
                },
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "documents": {
                        "type": "array",
                        "description": "Lista de documentos",
                        "items": {
                            "type": "object",
                            "properties": {
                                "document_id": {"type": "string"},
                                "reference": {"type": "string"},
                                "display_status": {"type": "string"},
                                "last_modified_at": {"type": "string", "format": "date-time"},
                                "acronym": {"type": "string"},
                                "official_number": {"type": "string"},
                                "signers": {"type": "array"}
                            }
                        }
                    },
                    "pagination": {
                        "type": "object",
                        "description": "Info de paginación: total, page, page_size, total_pages, has_next, has_previous"
                    }
                }
            }
        },
        {
            "name": "get_document",
            "description": """📄 DETALLE DE DOCUMENTO - Obtiene info completa incluyendo RESUMEN IA.

USA ESTA TOOL PARA:
- "Dame info del documento INF-2026-00001234"
- "¿De qué trata este informe?"
- "¿Quién debe firmar este documento?"
- "¿A qué expediente pertenece?"
- "¿Por qué fue rechazado?"

RESPUESTA INCLUYE:
- ai_summary: RESUMEN INTELIGENTE del contenido generado por IA
- short_resume: RESUMEN CORTO del documento (1-2 oraciones)
- state_category: 'editing' (borrador) o 'signing' (en proceso/firmado)
- status: estado actual
- details: firmantes, fechas, etc.
- linked_case: expediente vinculado (si existe)
- Para docs firmados: número oficial y URL del PDF

FLUJO RECOMENDADO:
1. Buscar documento → search_documents
2. Ver resumen y detalles → get_document (esta tool)
3. Si necesita contenido HTML completo → get_document_content""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID del documento (obtenido de search_documents o get_case_documents)"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["document_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "state_category": {"type": "string", "description": "editing o signing"},
                    "status": {"type": "string"},
                    "reference": {"type": "string"},
                    "document_type": {"type": "object"},
                    "ai_summary": {"type": "string", "description": "Resumen IA del contenido"},
                    "short_resume": {"type": "string"},
                    "details": {"type": "object", "description": "Firmantes, fechas, etc."},
                    "linked_case": {"type": "object", "description": "Expediente vinculado (o null)"},
                    "official_number": {"type": "string", "description": "Número oficial (si está firmado)"},
                    "created_at": {"type": "string"},
                    "signed_at": {"type": "string"}
                }
            }
        },
        {
            "name": "get_document_types",
            "description": """Lista todos los tipos de documentos disponibles en el sistema. Usa esta tool para:
- "¿Qué tipos de documentos puedo crear?"
- "¿Qué significa el tipo INF?"
- "¿Cuál es el acronym de un informe?"

RESPUESTA: Lista de tipos con name (nombre completo) y acronym (código corto).
Ejemplos comunes:
- INF (Informe)
- DICT (Dictamen)
- CAEX (Carátula de Expediente - se genera automáticamente)
- PV (Pase de Vista - se genera en transferencias)

Usa el acronym para filtrar en search_documents.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "document_types": {
                        "type": "array",
                        "description": "Lista de tipos de documentos",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "acronym": {"type": "string"}
                            }
                        }
                    },
                    "total": {"type": "integer"}
                }
            }
        },
        {
            "name": "get_user_info",
            "description": """Obtiene información del usuario actual. Usa esta tool para:
- "¿En qué sector estoy?"
- "¿Cuál es mi departamento?"
- "¿Qué roles tengo?"
- "¿A qué otros sectores tengo acceso?"

RESPUESTA:
- full_name, email: datos básicos del usuario
- sector: sector actual del usuario con department_name y department_acronym
- roles: lista de roles asignados (ej: admin, user, viewer)
- additional_sectors: otros sectores donde tiene permisos (can_view, can_edit)""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "full_name": {"type": "string"},
                    "email": {"type": "string"},
                    "profile_picture_url": {"type": "string"},
                    "estado": {"type": "string"},
                    "sector": {"type": "object", "description": "Sector actual con id, acronym, department_name"},
                    "roles": {"type": "array", "items": {"type": "string"}},
                    "additional_sectors": {"type": "array", "items": {"type": "object"}}
                }
            }
        },
        {
            "name": "get_pending_signatures",
            "description": """✍️ FIRMAS PENDIENTES - Documentos que esperan MI firma AHORA.

USA ESTA TOOL PARA:
- "¿Qué tengo para firmar?"
- "¿Tengo firmas pendientes?"
- "¿Qué hay en mi bandeja de firma?"

IMPORTANTE: Solo muestra documentos donde ES TU TURNO de firmar.
No incluye documentos donde otros deben firmar antes.

EJEMPLO DE RESPUESTA AL USUARIO:
"Tenés 2 documentos esperando tu firma:
- INF-2026-00001234: Informe sobre habilitación comercial
- DICT-2026-00000567: Dictamen de factibilidad

Además tenés 3 documentos en proceso de firma (esperando que otros firmen) y 2 borradores en edición."

RESPUESTA: Lista con:
- document_id, reference: identificación del documento
- document_type: tipo (INF, DICT, etc.)
- signer_role: "signer" (firmante) o "numerator" (numerador)
- creator: quién creó el documento
- sent_to_sign_at: cuándo se envió a firma""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "pending_signatures": {
                        "type": "array",
                        "description": "Documentos pendientes de firma",
                        "items": {
                            "type": "object",
                            "properties": {
                                "document_id": {"type": "string"},
                                "reference": {"type": "string"},
                                "document_number": {"type": "string"},
                                "document_type": {"type": "object"},
                                "signer_role": {"type": "string", "description": "signer o numerator"},
                                "signing_order": {"type": "integer"},
                                "sent_to_sign_at": {"type": "string"},
                                "creator": {"type": "object"}
                            }
                        }
                    },
                    "total": {"type": "integer"}
                }
            }
        },
        {
            "name": "get_document_content",
            "description": """📖 CONTENIDO COMPLETO - Lee el texto íntegro de un documento oficial.

KEYWORDS DE DETECCIÓN - USA ESTA TOOL SI EL USUARIO DICE:
- "léeme", "lee el documento", "leer el documento"
- "contenido completo", "texto completo"
- "qué dice exactamente", "qué dice el documento"
- "mostrame el contenido", "dame el texto"

USA ESTA TOOL SOLO SI:
- El resumen IA (ai_summary) no es suficiente
- El usuario pide explícitamente "leer todo el documento"
- Necesitas citar textualmente algo del documento

IMPORTANTE: Solo funciona con documentos OFICIALES (firmados).
Para borradores, el contenido está en get_document.

RESPUESTA:
- content.html: texto HTML completo del documento
- official_number: número oficial
- reference: asunto
- signed_at: fecha de firma

FLUJO RECOMENDADO:
1. Primero usa get_document para ver el ai_summary (resumen)
2. Solo si necesitas el texto completo, usa esta tool""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID del documento oficial"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["document_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "official_number": {"type": "string"},
                    "reference": {"type": "string"},
                    "signed_at": {"type": "string"},
                    "content": {
                        "type": "object",
                        "description": "Contenido del documento",
                        "properties": {
                            "html": {"type": "string", "description": "Texto HTML completo del documento"}
                        }
                    },
                    "document_type": {"type": "object"}
                }
            }
        },
        {
            "name": "get_agent_guide",
            "description": """📚 GUÍA COMPLETA PARA AGENTES IA - USAR AL CONECTAR POR PRIMERA VEZ.

Devuelve la guía completa del sistema GDI con:
- Qué es GDI y para qué sirve
- Todas las tools disponibles y cuándo usar cada una
- Cómo funciona la autenticación OAuth
- Estrategias de consulta recomendadas
- Ejemplos de interacción
- Errores comunes y soluciones

RECOMENDADO: Llamar esta tool al inicio de cada sesión para entender el sistema.

NO REQUIERE PARÁMETROS - funciona sin autenticación.""",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "guide": {"type": "string", "description": "Texto completo de la guía en Markdown"},
                    "version": {"type": "string"},
                    "tools_count": {"type": "integer"},
                    "last_updated": {"type": "string"}
                }
            }
        },
        {
            "name": "list_my_tenants",
            "description": """🏢 LISTA MIS MUNICIPALIDADES - Ver a qué organizaciones tengo acceso.

USA ESTA TOOL CUANDO:
- Quieres ver a qué organizaciones tienes acceso
- El usuario dice "cambiemos de tenant/municipalidad"
- Necesitas recordar los tenant_id disponibles
- Recibiste un error "multi_tenant_selection_required"

RESPUESTA:
- tenants: lista con tenant_id, name de cada municipalidad
- total: cantidad de municipalidades donde tienes cuenta
- hint: instrucción de cómo usar tenant_id

EJEMPLO DE USO:
1. Usuario multi-tenant recibe error pidiendo seleccionar municipalidad
2. Llamar list_my_tenants para ver opciones
3. Preguntar al usuario cuál prefiere
4. En la siguiente llamada, usar tenant_id de la municipalidad elegida

NO REQUIERE tenant_id - funciona solo con OAuth.""",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "tenants": {
                        "type": "array",
                        "description": "Lista de municipalidades disponibles",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tenant_id": {"type": "string"},
                                "name": {"type": "string"}
                            }
                        }
                    },
                    "total": {"type": "integer"},
                    "hint": {"type": "string"}
                }
            }
        },
        {
            "name": "create_document",
            "description": """📝 CREAR DOCUMENTO - Crea un nuevo documento en estado borrador.

USA ESTA TOOL PARA:
- "Crea un informe nuevo"
- "Necesito hacer un dictamen"
- "Quiero crear un documento"
- "Crea una nota para el sector de tesorería" (document_type_acronym="NOTA", recipients con UUIDs de sectores)
- "Necesito enviar un memo a Juan Pérez" (document_type_acronym="MEMO", recipients con UUIDs de usuarios)

PARÁMETROS:
- document_type_acronym: Tipo de documento (INF, DICT, NOTA, MEMO, etc.) - usa get_document_types para ver opciones
- reference: Descripción/asunto del documento
- case_id: Expediente a vincular (opcional)
- recipients: Destinatarios para NOTA (sector UUIDs) o MEMO (user UUIDs).
  Formato: {"to": ["uuid1"], "cc": [], "bcc": []}
  - Para NOTA: los UUIDs son de sectores (usar get_sectors para obtenerlos)
  - Para MEMO: los UUIDs son de usuarios

RESPUESTA:
- document_id: UUID del documento creado
- status: "draft"

FLUJO TÍPICO:
1. create_document → obtener document_id
2. save_document → agregar contenido y firmantes
3. start_signing → enviar a proceso de firma""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_type_acronym": {"type": "string", "description": "Acrónimo del tipo (INF, DICT, NOTA, MEMO, etc.)"},
                    "reference": {"type": "string", "description": "Descripción/asunto del documento"},
                    "case_id": {"type": "string", "description": "UUID del expediente a vincular (opcional)"},
                    "recipients": {
                        "type": "object",
                        "description": "Destinatarios para NOTA (sector UUIDs) o MEMO (user UUIDs). Para NOTA: to/cc/bcc contienen UUIDs de sectores. Para MEMO: UUIDs de usuarios.",
                        "properties": {
                            "to":  {"type": "array", "items": {"type": "string", "format": "uuid"}, "description": "Destinatarios principales (UUIDs de sectores para NOTA, o usuarios para MEMO)"},
                            "cc":  {"type": "array", "items": {"type": "string", "format": "uuid"}, "description": "Copia (UUIDs de sectores para NOTA, o usuarios para MEMO)"},
                            "bcc": {"type": "array", "items": {"type": "string", "format": "uuid"}, "description": "Copia oculta (UUIDs de sectores para NOTA, o usuarios para MEMO)"}
                        }
                    },
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["document_type_acronym", "reference"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "document_id": {"type": "string"},
                    "status": {"type": "string", "description": "Estado del documento (draft)"},
                    "message": {"type": "string"},
                    "linked_to_case": {"type": "string", "description": "UUID del expediente vinculado (si se proporcionó case_id)"}
                }
            }
        },
        {
            "name": "save_document",
            "description": """💾 GUARDAR DOCUMENTO - Guarda cambios en un documento borrador.

USA ESTA TOOL PARA:
- "Actualiza el contenido del documento"
- "Agrega estos firmantes al documento"
- "Cambia la referencia del informe"

PARÁMETROS:
- document_id: UUID del documento (obtenido de create_document o search_documents)
- content: Contenido HTML del documento (opcional)
- reference: Nueva descripción (opcional)
- signers: Lista de firmantes [{user_id, email, is_numerator}] (opcional)

IMPORTANTE:
- Solo funciona con documentos en estado 'draft' o 'rejected'
- Debe proporcionar al menos un campo a actualizar

RESPUESTA:
- success: true/false
- last_modified_at: fecha de última modificación""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID del documento"},
                    "content": {"type": "string", "description": "Contenido HTML del documento"},
                    "reference": {"type": "string", "description": "Nueva descripción/referencia"},
                    "signers": {
                        "type": "array",
                        "description": "Lista de firmantes",
                        "items": {
                            "type": "object",
                            "properties": {
                                "user_id": {"type": "string"},
                                "email": {"type": "string"},
                                "is_numerator": {"type": "boolean"}
                            }
                        }
                    },
                    "recipients": {
                        "type": "object",
                        "description": "Destinatarios para NOTA (opcional). Objeto con sectors y/o users",
                        "properties": {
                            "sectors": {"type": "array", "items": {"type": "object"}, "description": "Sectores destinatarios"},
                            "users": {"type": "array", "items": {"type": "object"}, "description": "Usuarios destinatarios"}
                        }
                    },
                    "proposed_case_ids": {
                        "type": "array",
                        "description": "IDs de expedientes a vincular al documento (opcional)",
                        "items": {"type": "string"}
                    },
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["document_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "document_id": {"type": "string"},
                    "message": {"type": "string"},
                    "last_modified_at": {"type": "string"}
                }
            }
        },
        {
            "name": "start_signing",
            "description": """✍️ INICIAR FIRMA - Envía un documento al proceso de firma.

USA ESTA TOOL PARA:
- "Envía el documento a firma"
- "Inicia el proceso de firma"
- "Quiero que firmen este documento"

REQUISITOS:
- El documento debe tener al menos un firmante y un numerador asignados
- Solo el creador del documento puede iniciar la firma
- El documento debe estar en estado 'draft'

RESPUESTA:
- success: true/false
- message: confirmación

IMPORTANTE: Una vez iniciada la firma, el documento no puede editarse.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID del documento"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["document_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string", "description": "Mensaje de confirmación"}
                }
            }
        },
        {
            "name": "get_case_by_number",
            "description": """🔢 BUSCAR POR NÚMERO - Obtiene un expediente por su número exacto.

USA ESTA TOOL PARA:
- "Busca el expediente EE-2026-00001234"
- "Dame info del expediente número X"

RESPUESTA: Mismos datos que get_case pero buscando por número exacto.
Retorna null si no existe.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "Número exacto del expediente (ej: EE-2026-00001234)"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["case_number"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "case_number": {"type": "string"},
                    "reference": {"type": "string"},
                    "status": {"type": "string"},
                    "template": {"type": "object"},
                    "admin_sector": {"type": "object"},
                    "ai_summary": {"type": "string"},
                    "created_at": {"type": "string"}
                }
            }
        },
        {
            "name": "prepare_assignment",
            "description": """📋 PREPARAR ASIGNACIÓN - Verifica permisos y obtiene info para asignar.

USA ESTA TOOL ANTES DE assign_case para verificar que el usuario puede asignar.

RESPUESTA:
- success: true/false
- status: "OK" o "NOT_ALLOWED"
- user_sectors_in_case: sectores del usuario con acceso al expediente
- available_sectors: sectores destino disponibles""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["case_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "status": {"type": "string", "description": "OK o NOT_ALLOWED"},
                    "user_sectors_in_case": {
                        "type": "array",
                        "description": "Sectores del usuario en el expediente",
                        "items": {"type": "object"}
                    },
                    "available_sectors": {
                        "type": "array",
                        "description": "Sectores disponibles como destino",
                        "items": {"type": "object"}
                    },
                    "total": {"type": "integer"},
                    "total_available_sectors": {"type": "integer"}
                }
            }
        },
        {
            "name": "assign_case",
            "description": """📤 ASIGNAR EXPEDIENTE - Asigna un expediente a otro sector (sin transferir propiedad).

USA ESTA TOOL PARA:
- "Asigna este expediente al sector Legal"
- "Envía el expediente a Hacienda para revisión"

PARÁMETROS:
- case_id: UUID del expediente
- target_sector_id: UUID del sector destino (usar prepare_assignment para ver disponibles)
- reason: Motivo de la asignación (5-500 caracteres)
- assigned_user_id: Usuario específico (opcional)
- create_official_doc: Si true, genera documento PV automático

IMPORTANTE: La asignación NO transfiere propiedad. El sector original mantiene el control.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "target_sector_id": {"type": "string", "description": "UUID del sector destino"},
                    "reason": {"type": "string", "description": "Motivo de la asignación (5-500 chars)"},
                    "assigned_user_id": {"type": "string", "description": "UUID del usuario asignado (opcional)"},
                    "create_official_doc": {"type": "boolean", "description": "Generar documento PV automático", "default": False},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["case_id", "target_sector_id", "reason"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "assignment_id": {"type": "string", "description": "UUID del movimiento de asignación (nuevo o reutilizado)"},
                    "task_id": {"type": "string", "description": "UUID de la tarea creada en case_assignment_tasks"},
                    "action_type": {"type": "string", "description": "Siempre 'asignado'"},
                    "sector_acronym": {"type": "string", "description": "Acrónimo DPTO#SECTOR del sector asignado"},
                    "department_name": {"type": "string", "description": "Nombre del departamento del sector asignado"},
                    "is_new_assignment": {"type": "boolean", "description": "True si se creó una nueva asignación; False si el sector ya tenía una activa"},
                    "official_document": {
                        "type": "object",
                        "description": "Info del PV generado (solo si create_official_doc=true)",
                        "properties": {
                            "document_id": {"type": "string"},
                            "official_number": {"type": "string"}
                        }
                    }
                }
            }
        },
        {
            "name": "get_document_states",
            "description": """📊 ESTADOS DE DOCUMENTOS - Catálogo de estados posibles.

USA ESTA TOOL PARA:
- "¿Qué estados puede tener un documento?"
- "Explícame los estados de documentos"

RESPUESTA:
- states: lista de estados con display_state (nombre visible)
- mappings: diccionario código → nombre para mapeo rápido

ESTADOS COMUNES:
- draft / En edición
- sent_to_sign / Firmar ahora
- signing_process / En proceso de firma
- signed / Firmado
- rejected / Rechazado""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "states": {
                        "type": "array",
                        "description": "Lista de estados con display_state y code",
                        "items": {"type": "object"}
                    },
                    "mappings": {"type": "object", "description": "Diccionario código → nombre"},
                    "total": {"type": "integer"}
                }
            }
        },
        {
            "name": "reject_document",
            "description": """Rechazar documento - Rechaza un documento en proceso de firma.

USA ESTA TOOL PARA:
- "Rechaza este documento"
- "No apruebo el informe, devolvelo"
- "Rechazar con observaciones"

PARAMETROS:
- document_id: UUID del documento
- reason: Motivo del rechazo (REQUERIDO)

IMPORTANTE: El documento vuelve a estado 'rejected' y el creador puede corregirlo.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID del documento a rechazar"},
                    "reason": {"type": "string", "description": "Motivo del rechazo"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a multiples organizaciones)"}
                },
                "required": ["document_id", "reason"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string", "description": "Mensaje de confirmación"}
                }
            }
        },
        {
            "name": "propose_document",
            "description": """Proponer documento - Propone un documento borrador para vincular a un expediente.

USA ESTA TOOL PARA:
- "Propone este borrador para el expediente"
- "Sugiere vincular el documento al expediente"

NOTA: A diferencia de link_document_to_case, esto propone un BORRADOR que luego debe ser aceptado.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "document_draft_id": {"type": "string", "description": "UUID del documento borrador a proponer"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a multiples organizaciones)"}
                },
                "required": ["case_id", "document_draft_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "case_id": {"type": "string"},
                    "document_draft_id": {"type": "string"},
                    "message": {"type": "string", "description": "Mensaje de confirmación"}
                }
            }
        },
        {
            "name": "reject_proposal",
            "description": """Rechazar propuesta - Rechaza un documento propuesto sin vincularlo.

Desactiva la propuesta. El documento no se elimina.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "proposed_id": {"type": "string", "description": "UUID de la propuesta a rechazar"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a multiples organizaciones)"}
                },
                "required": ["case_id", "proposed_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string", "description": "Mensaje de confirmación"}
                }
            }
        },
        {
            "name": "get_case_templates",
            "description": """Templates de expedientes - Lista plantillas disponibles para crear expedientes.

USA ESTA TOOL PARA:
- "Que tipos de expedientes puedo crear?"
- Obtener case_template_id (por ejemplo para armar tramites TAD)""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a multiples organizaciones)"}
                },
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "templates": {
                        "type": "array",
                        "description": "Lista de plantillas de expedientes",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "acronym": {"type": "string"},
                                "description": {"type": "string"}
                            }
                        }
                    },
                    "total": {"type": "integer"}
                }
            }
        },
        {
            "name": "get_sectors",
            "description": """Sectores y departamentos - Lista plana de todos los sectores activos, cada uno con los datos de su departamento.

USA ESTA TOOL PARA:
- "Que sectores hay en la municipalidad?"
- Obtener UUIDs de sectores destino para create_document con recipients de tipo NOTA""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a multiples organizaciones)"}
                },
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "sectors": {
                        "type": "array",
                        "description": "Lista plana de sectores activos, cada uno con su departamento",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sector_id": {"type": "string"},
                                "sector_acronym": {"type": "string"},
                                "sector_color": {"type": "string"},
                                "department_id": {"type": "string"},
                                "department_acronym": {"type": "string"},
                                "department_name": {"type": "string"},
                                "department_color": {"type": "string"}
                            }
                        }
                    },
                    "total": {"type": "integer"}
                }
            }
        },
        {
            "name": "search_users",
            "description": """Buscar usuarios - Busca usuarios por nombre para autocompletado.

USA ESTA TOOL PARA:
- "Busca al usuario Juan Perez"
- Obtener user_id para asignar firmantes o usuarios

PARAMETROS:
- search: Texto de busqueda (minimo 4 caracteres)
- limit: Cantidad maxima de resultados (default 10)""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Texto a buscar (minimo 4 caracteres)", "minLength": 4},
                    "limit": {"type": "integer", "description": "Cantidad maxima de resultados", "default": 10},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a multiples organizaciones)"}
                },
                "required": ["search"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "users": {
                        "type": "array",
                        "description": "Lista de usuarios encontrados",
                        "items": {
                            "type": "object",
                            "properties": {
                                "full_name": {"type": "string"},
                                "sector": {"type": "string", "description": "Sector en formato DEPT#SECTOR"}
                            }
                        }
                    },
                    "total_found": {"type": "integer"}
                }
            }
        },
        {
            "name": "get_notes",
            "description": """Notas recibidas - Lista notas oficiales recibidas en los sectores del usuario.

USA ESTA TOOL PARA:
- "Tengo notas?"
- "Notas sin leer"
- "Busca notas sobre..."

PARAMETROS:
- unread_only: true para solo no leidas
- search: buscar en numero, referencia o contenido""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "Pagina (default 1)", "default": 1},
                    "page_size": {"type": "integer", "description": "Resultados por pagina (default 20)", "default": 20},
                    "unread_only": {"type": "boolean", "description": "Solo notas no leidas", "default": False},
                    "search": {"type": "string", "description": "Buscar en numero, referencia o contenido"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a multiples organizaciones)"}
                },
                "required": []
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "notes": {
                        "type": "array",
                        "description": "Lista de notas recibidas",
                        "items": {
                            "type": "object",
                            "properties": {
                                "document_id": {"type": "string"},
                                "official_number": {"type": "string"},
                                "reference": {"type": "string"},
                                "sender": {"type": "object"},
                                "is_read": {"type": "boolean"},
                                "signed_at": {"type": "string"}
                            }
                        }
                    },
                    "pagination": {
                        "type": "object",
                        "description": "Info de paginacion: page, page_size, total, total_pages"
                    }
                }
            }
        },
        {
            "name": "search_document_by_number",
            "description": "Buscar documento oficial por su numero exacto (ej: INF-2026-00000060-TXST-TESO). Devuelve el documento si existe.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "doc_number": {"type": "string", "description": "Numero oficial del documento (ej: INF-2026-00000060-TXST-TESO)"},
                    "tenant_id": {"type": "string", "description": "ID del tenant (opcional si solo tienes uno)"}
                },
                "required": ["doc_number"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean", "description": "Si el documento fue encontrado"},
                    "document": {
                        "type": "object",
                        "description": "Datos del documento oficial (o null si no encontrado)",
                        "properties": {
                            "document_id": {"type": "string"},
                            "official_number": {"type": "string"},
                            "reference": {"type": "string"},
                            "document_type": {"type": "object"},
                            "signed_at": {"type": "string"}
                        }
                    },
                    "search_term": {"type": "string"}
                }
            }
        },
        {
            "name": "get_signature_details",
            "description": "Obtener detalles de firma de un documento: lista de firmantes, estado de cada firmante (pendiente/firmado/rechazado), orden de firma.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID del documento"},
                    "tenant_id": {"type": "string", "description": "ID del tenant (opcional si solo tienes uno)"}
                },
                "required": ["document_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "document": {"type": "object", "description": "Info del documento"},
                    "current_signer": {"type": "object", "description": "Firmante actual (o null)"},
                    "signature_progress": {
                        "type": "object",
                        "description": "Progreso de la firma",
                        "properties": {
                            "signed_count": {"type": "integer"},
                            "total_count": {"type": "integer"},
                            "signers": {"type": "array", "items": {"type": "object"}}
                        }
                    },
                    "can_sign": {"type": "boolean", "description": "Si el usuario actual puede firmar"}
                }
            }
        },
        {
            "name": "get_sent_notes",
            "description": "Obtener notas/comunicaciones enviadas por el usuario. Incluye destinatarios y estado de lectura.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1, "description": "Pagina"},
                    "page_size": {"type": "integer", "default": 20, "description": "Resultados por pagina (max 100)"},
                    "search": {"type": "string", "description": "Buscar en contenido de notas"},
                    "tenant_id": {"type": "string", "description": "ID del tenant (opcional si solo tienes uno)"}
                }
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "notes": {
                        "type": "array",
                        "description": "Lista de notas enviadas",
                        "items": {
                            "type": "object",
                            "properties": {
                                "document_id": {"type": "string"},
                                "official_number": {"type": "string"},
                                "reference": {"type": "string"},
                                "recipients": {"type": "array", "items": {"type": "object"}},
                                "signed_at": {"type": "string"}
                            }
                        }
                    },
                    "pagination": {
                        "type": "object",
                        "description": "Info de paginacion: page, page_size, total, total_pages"
                    }
                }
            }
        },
        {
            "name": "get_archived_notes",
            "description": "Obtener notas archivadas por el usuario.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1, "description": "Pagina"},
                    "page_size": {"type": "integer", "default": 20, "description": "Resultados por pagina (max 100)"},
                    "search": {"type": "string", "description": "Buscar en contenido de notas"},
                    "tenant_id": {"type": "string", "description": "ID del tenant (opcional si solo tienes uno)"}
                }
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "notes": {"type": "array", "description": "Lista de notas archivadas", "items": {"type": "object"}},
                    "pagination": {"type": "object", "description": "page, page_size, total, total_pages"}
                }
            }
        },
        {
            "name": "get_note_detail",
            "description": "Obtener detalle completo de una nota especifica. Incluye contenido, remitente, destinatarios y estado.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "UUID de la nota/documento"},
                    "tenant_id": {"type": "string", "description": "ID del tenant (opcional si solo tienes uno)"}
                },
                "required": ["note_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "official_number": {"type": "string"},
                    "reference": {"type": "string"},
                    "content": {"type": "string"},
                    "signed_at": {"type": "string"},
                    "ai_summary": {"type": "string"},
                    "signers": {"type": "array"},
                    "document_type": {"type": "object", "description": "name, acronym"},
                    "department_name": {"type": "string"},
                    "recipients": {"type": "object"},
                    "my_access": {"type": "object", "description": "is_sender, recipient_type, is_archived, opened_at"},
                    "openings": {"type": "array", "description": "Solo si el usuario es el remitente"}
                }
            }
        },
        {
            "name": "get_memos",
            "description": "Memos recibidos - Lista memos oficiales recibidos por el usuario. Similar a notas pero persona-a-persona.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1, "description": "Pagina"},
                    "page_size": {"type": "integer", "default": 20, "description": "Resultados por pagina (max 100)"},
                    "search": {"type": "string", "description": "Buscar en contenido de memos"},
                    "tenant_id": {"type": "string", "description": "ID del tenant (opcional si solo tienes uno)"}
                }
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "memos": {"type": "array", "description": "Lista de memos recibidos", "items": {"type": "object"}},
                    "pagination": {"type": "object", "description": "page, page_size, total, total_pages"}
                }
            }
        },
        {
            "name": "get_sent_memos",
            "description": "Obtener memos enviados por el usuario. Incluye destinatarios y estado de lectura.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1, "description": "Pagina"},
                    "page_size": {"type": "integer", "default": 20, "description": "Resultados por pagina (max 100)"},
                    "search": {"type": "string", "description": "Buscar en contenido de memos"},
                    "tenant_id": {"type": "string", "description": "ID del tenant (opcional si solo tienes uno)"}
                }
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "memos": {"type": "array", "description": "Lista de memos enviados", "items": {"type": "object"}},
                    "pagination": {"type": "object", "description": "page, page_size, total, total_pages"}
                }
            }
        },
        {
            "name": "get_archived_memos",
            "description": "Obtener memos archivados por el usuario.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1, "description": "Pagina"},
                    "page_size": {"type": "integer", "default": 20, "description": "Resultados por pagina (max 100)"},
                    "search": {"type": "string", "description": "Buscar en contenido de memos"},
                    "tenant_id": {"type": "string", "description": "ID del tenant (opcional si solo tienes uno)"}
                }
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "memos": {"type": "array", "description": "Lista de memos archivados", "items": {"type": "object"}},
                    "pagination": {"type": "object", "description": "page, page_size, total, total_pages"}
                }
            }
        },
        {
            "name": "get_memo_detail",
            "description": "Obtener detalle completo de un memo especifico. Incluye contenido, remitente, destinatarios y estado.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memo_id": {"type": "string", "description": "UUID del memo/documento"},
                    "tenant_id": {"type": "string", "description": "ID del tenant (opcional si solo tienes uno)"}
                },
                "required": ["memo_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "official_number": {"type": "string"},
                    "reference": {"type": "string"},
                    "content": {"type": "string"},
                    "signed_at": {"type": "string"},
                    "ai_summary": {"type": "string"},
                    "signers": {"type": "array"},
                    "document_type": {"type": "object", "description": "name, acronym"},
                    "department_name": {"type": "string"},
                    "recipients": {"type": "object"},
                    "my_access": {"type": "object", "description": "is_sender, recipient_type, is_archived, opened_at"},
                    "openings": {"type": "array", "description": "Solo si el usuario es el remitente"}
                }
            }
        },
        {
            "name": "search_records",
            "description": """📋 BUSCAR LEGAJOS - Busca en el Registro Legajo Multipropósito (RLM).

USA ESTA TOOL PARA:
- "Buscar legajos de arquitectura"
- "Listar legajos activos"
- "Buscar legajo por número"

PARÁMETROS:
- family_code: Código de familia (ARQ, LUM, ORD, etc.) - opcional
- search: Texto de búsqueda (número o datos)
- state: Filtro por estado
- page/page_size: Paginación

RESPUESTA: Lista de legajos con número, estado, registro, creador, resume (resumen IA).""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "family_code": {"type": "string", "description": "Código de familia de registro (ARQ, LUM, ORD)"},
                    "search": {"type": "string", "description": "Texto de búsqueda"},
                    "state": {"type": "string", "description": "Filtro por estado"},
                    "page": {"type": "integer", "default": 1, "description": "Página"},
                    "page_size": {"type": "integer", "default": 20, "description": "Resultados por página (max 100)"},
                    "tenant_id": {"type": "string", "description": "UUID del tenant (opcional si solo tienes uno)"}
                }
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "records": {"type": "array", "description": "Lista de legajos", "items": {"type": "object"}},
                    "total": {"type": "integer"},
                    "page": {"type": "integer"},
                    "page_size": {"type": "integer"},
                    "total_pages": {"type": "integer"}
                }
            }
        },
        {
            "name": "get_record",
            "description": """📋 DETALLE DE LEGAJO - Obtiene información completa de un legajo.

USA ESTA TOOL PARA:
- "Dame detalles del legajo RLM-2026-00000001"
- "Ver campos del legajo X"

RESPUESTA: Detalle con datos enriquecidos, estado, registro, permisos del usuario, resume (resumen IA).""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "UUID del legajo"},
                    "tenant_id": {"type": "string", "description": "UUID del tenant (opcional si solo tienes uno)"}
                },
                "required": ["record_id"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "record_number": {"type": "string"},
                    "display_name": {"type": "string"},
                    "state": {"type": "string"},
                    "data": {"type": "object", "description": "Campos del legajo según data_schema de la familia"},
                    "resume": {"type": "string", "description": "Resumen IA del legajo"},
                    "next_expiration": {"type": "string"},
                    "created_at": {"type": "string"},
                    "updated_at": {"type": "string"},
                    "registry": {"type": "object", "description": "id, code, name, data_schema, allowed_states"},
                    "created_by": {"type": "object", "description": "user_id, name, sector, department"},
                    "permissions": {"type": "object", "description": "can_view, can_edit, can_delete"}
                }
            }
        },
        {
            "name": "get_registry_families",
            "description": """📋 FAMILIAS DE REGISTROS - Lista los tipos de registro disponibles en RLM.

USA ESTA TOOL PARA:
- "Qué tipos de legajos hay?"
- "Ver registros disponibles"

RESPUESTA: Lista de familias con código, nombre, data_schema y permisos del usuario.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID del tenant (opcional si solo tienes uno)"}
                }
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "registries": {"type": "array", "description": "Lista de familias de registro con code, name, data_schema, permissions", "items": {"type": "object"}},
                    "total": {"type": "integer", "description": "Total de familias disponibles"}
                }
            }
        },
        {
            "name": "semantic_search",
            "description": """🔍 BUSQUEDA SEMANTICA - Busca documentos oficiales por significado usando IA.

USA ESTA TOOL PARA:
- "Busca documentos sobre habilitaciones comerciales"
- "Encontrar documentos relacionados con pavimentación"
- "Documentos que hablen de multas o infracciones"

A diferencia de search_documents (busca por número exacto), esta tool busca por SIGNIFICADO:
- Encuentra documentos aunque no uses las palabras exactas
- Busca en el contenido completo de cada documento
- Filtra automáticamente por permisos del usuario

RESPUESTA: Lista de documentos con similarity (0-1), chunk_text (fragmento relevante),
official_number, reference, y vinculaciones a expedientes y legajos.

PARAMETROS:
- query: Texto de búsqueda (3-500 caracteres)
- limit: Cantidad máxima de resultados (default 20, max 50)""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Texto de búsqueda semántica (3-500 caracteres)", "minLength": 3, "maxLength": 500},
                    "limit": {"type": "integer", "description": "Cantidad máxima de resultados (default 20, max 50)", "default": 20, "minimum": 1, "maximum": 50},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["query"]
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "query": {"type": "string", "description": "Query original"},
                    "rewritten_query": {"type": "string", "description": "Query reescrita por IA para mejorar la búsqueda"},
                    "intent": {"type": "string", "description": "Tipo de búsqueda: 'rag' (semántica) o 'lookup' (por número exacto)"},
                    "results": {
                        "type": "array",
                        "description": "Documentos encontrados ordenados por relevancia",
                        "items": {
                            "type": "object",
                            "properties": {
                                "document_id": {"type": "string"},
                                "official_number": {"type": "string"},
                                "document_type": {"type": "string"},
                                "reference": {"type": "string"},
                                "short_resume": {"type": "string"},
                                "similarity": {"type": "number", "description": "Similitud coseno 0-1"},
                                "chunk_text": {"type": "string", "description": "Fragmento relevante del documento"},
                                "linked_cases": {"type": "array"},
                                "linked_records": {"type": "array"}
                            }
                        }
                    },
                    "total": {"type": "integer"}
                }
            }
        },

        {
            "name": "get_case_responsibles",
            "description": """Lista los responsables activos de un expediente.

Devuelve los responsables ADMIN (lista, puede haber más de uno) y los responsables ADDITIONAL asignados.

USAR PARA:
- "¿Quién es responsable del expediente X?"
- "¿Qué usuarios están asignados al expediente Y?"
- Verificar antes de agregar un nuevo responsable

RESPUESTA: { admin: [...], additional: [...] }""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "user_id": {"type": "string", "description": "UUID del usuario que consulta (se inyecta automáticamente)"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tenés acceso a múltiples organizaciones)"},
                },
                "required": ["case_id"],
            },
        },
        {
            "name": "add_case_responsible",
            "description": """Agrega un responsable a un expediente.

Permite asignar usuarios como responsables ADMIN o ADDITIONAL de un expediente.

USAR PARA:
- "Asignar a Juan como responsable del expediente X"
- "Agregar a María como colaboradora del expediente Y"

TIPOS:
- ADMIN: responsable principal (se agrega a los existentes, no reemplaza)
- ADDITIONAL: responsable adicional/colaborador

NOTA: Requiere permiso de edición sobre el expediente.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "responsible_user_id": {"type": "string", "description": "UUID del usuario a agregar como responsable"},
                    "responsible_type": {"type": "string", "enum": ["ADMIN", "ADDITIONAL"], "description": "Tipo de responsabilidad"},
                    "sector_id": {"type": "string", "description": "UUID del sector del usuario responsable"},
                    "reason": {"type": "string", "description": "Motivo de la asignación (opcional)"},
                    "user_id": {"type": "string", "description": "UUID del usuario que realiza la acción (se inyecta automáticamente)"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tenés acceso a múltiples organizaciones)"},
                },
                "required": ["case_id", "responsible_user_id", "responsible_type", "sector_id"],
            },
        },
        {
            "name": "remove_case_responsible",
            "description": """Quita un responsable de un expediente (soft delete).

USAR PARA:
- "Quitar a Juan como responsable del expediente X"
- "Remover al colaborador Y del expediente Z"

NOTA: Requiere el `responsible_id` (ID del registro de responsable, no el user_id).
      Usá `get_case_responsibles` primero para obtener los IDs.
      Requiere permiso de edición sobre el expediente.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "responsible_id": {"type": "string", "description": "UUID del registro de responsable (de get_case_responsibles)"},
                    "reason": {"type": "string", "description": "Motivo de la remoción (opcional)"},
                    "user_id": {"type": "string", "description": "UUID del usuario que realiza la acción (se inyecta automáticamente)"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tenés acceso a múltiples organizaciones)"},
                },
                "required": ["case_id", "responsible_id"],
            },
        },
]
