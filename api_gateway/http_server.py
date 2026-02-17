"""
MCP Server HTTP - Transport Streamable HTTP para Railway.

Este archivo expone el MCP Server via HTTP en lugar de stdio.
Usar para deployments remotos (Railway, etc).

Endpoint: POST/GET /mcp
Health:   GET /health

Run:
    python api_gateway/http_server.py

    O con uvicorn:
    uvicorn api_gateway.http_server:app --host 0.0.0.0 --port 8005
"""
import os
import sys
import json
import logging
import asyncio
import uuid
from typing import Any, Dict

# Agregar path del backend para imports
backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_root)

# Cargar variables de entorno
from dotenv import load_dotenv
load_dotenv(os.path.join(backend_root, ".env"))

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.middleware.cors import CORSMiddleware

# Imports MCP
from api_gateway.auth_mcp import (
    validate_mcp_jwt,
    verify_mcp_token,
    get_email_from_userinfo,
    find_user_all_tenants,
    MultiTenantSelectionRequired
)
from api_gateway.context import create_context, MCPContext
from api_gateway.tools import cases, documents, system, notes

# Imports REST API
from api_gateway.rest_api import (
    # Expedientes (lectura)
    api_search_cases, api_get_case, api_get_case_history,
    api_get_case_documents, api_get_case_permissions,
    # Expedientes (operaciones)
    api_get_case_by_number,
    api_prepare_assignment, api_assign_case, api_close_assignment,
    api_create_case, api_transfer_case, api_link_document,
    api_propose_document, api_prepare_transfer,
    api_accept_proposal, api_reject_proposal,
    # Documentos (lectura)
    api_search_documents, api_get_document, api_get_document_content,
    api_get_pending_signatures, api_get_document_url,
    # Documentos (operaciones)
    api_create_document, api_save_document, api_start_signing,
    api_sign_document, api_reject_document, api_delete_document,
    # Sistema
    api_get_document_types, api_get_user_info, api_get_document_states,
    api_get_sectors, api_get_case_templates, api_search_users,
    # Notas
    api_get_notes,
    # Nuevos handlers
    api_get_sector_users,
    api_search_document_by_number,
    api_get_signature_details,
    api_import_document,
    api_replace_imported_pdf,
    api_check_signer_permissions,
    api_list_all_users,
    api_get_sent_notes,
    api_get_archived_notes,
    api_archive_note,
    api_get_note_detail,
)

# Configurar logging (stderr para no interferir con stdout)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Versión del protocolo MCP
MCP_PROTOCOL_VERSION = "2025-03-26"

# Store de sesiones activas (en producción usar Redis)
sessions: Dict[str, Dict[str, Any]] = {}


# ============================================================================
# HANDLERS JSON-RPC
# ============================================================================

def create_jsonrpc_response(id: Any, result: Any) -> Dict:
    """Crea respuesta JSON-RPC exitosa."""
    return {
        "jsonrpc": "2.0",
        "id": id,
        "result": result
    }


def create_jsonrpc_error(id: Any, code: int, message: str, data: Any = None) -> Dict:
    """Crea respuesta JSON-RPC con error."""
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": id,
        "error": error
    }


async def handle_initialize(request_id: Any, params: Dict) -> Dict:
    """Maneja initialize request."""
    return create_jsonrpc_response(request_id, {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {
            "tools": {}
        },
        "serverInfo": {
            "name": "gdi-backend",
            "version": "1.0.0"
        }
    })


async def handle_list_tools(request_id: Any) -> Dict:
    """Maneja tools/list request."""
    tools = [
        # ===== CASES (Expedientes) =====
        # Un expediente es un contenedor de documentos que sigue un flujo administrativo.
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

RESPUESTA: Lista con case_number, reference, ai_summary (resumen IA del expediente).
Para ver historial completo → get_case_history con el case_id.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"},
                    "page": {"type": "integer", "description": "Página (default 1)", "default": 1},
                    "page_size": {"type": "integer", "description": "Resultados por página (default 20, max 100)", "default": 20},
                    "search": {"type": "string", "description": "Buscar por número de expediente (ej: EE-2026-000017) o por referencia/asunto"},
                    "status": {"type": "string", "description": "Estado: active (en trámite), inactive, archived (cerrado)", "enum": ["active", "inactive", "archived"]},
                    "date_filter": {"type": "string", "description": "Filtro temporal: today, week, month, year", "enum": ["today", "week", "month", "year"]},
                    "sector_filter": {"type": "string", "description": "Filtrar por sector (usar acronym del sector, ej: HAC, LEGAL)"}
                },
                "required": []
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
                    "include_documents": {"type": "boolean", "description": "true para incluir lista de documentos oficiales y propuestos", "default": False}
                },
                "required": ["case_id"]
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
            }
        },
        # ===== DOCUMENTS (Documentos) =====
        # Un documento es un archivo con contenido (informe, dictamen, certificado, etc.)
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
- status: pending (borrador), sent_to_sign (en firma), signed (oficial), rejected
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
                    "status": {"type": "string", "description": "Filtrar por estado", "enum": ["pending", "sent_to_sign", "signed", "rejected"]},
                    "document_type": {"type": "string", "description": "Filtrar por tipo de documento (acronym, ej: INF, DICT, CAEX). Usa get_document_types para ver tipos disponibles."},
                    "case_id": {"type": "string", "description": "Filtrar documentos vinculados a un expediente específico"}
                },
                "required": []
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
            }
        },
        # ===== SYSTEM (Catálogos del sistema) =====
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
            }
        },
        # ===== GUÍA PARA AGENTES =====
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
            }
        },
        # ===== MULTI-TENANT =====
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
            }
        },
        # ===== NUEVOS TOOLS DE OPERACIONES =====
        # ===== DOCUMENTOS (Escritura) =====
        {
            "name": "create_document",
            "description": """📝 CREAR DOCUMENTO - Crea un nuevo documento en estado borrador.

USA ESTA TOOL PARA:
- "Crea un informe nuevo"
- "Necesito hacer un dictamen"
- "Quiero crear un documento"

PARÁMETROS:
- document_type_acronym: Tipo de documento (INF, DICT, etc.) - usa get_document_types para ver opciones
- reference: Descripción/asunto del documento
- case_id: Expediente a vincular (opcional)

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
                    "document_type_acronym": {"type": "string", "description": "Acrónimo del tipo (INF, DICT, etc.)"},
                    "reference": {"type": "string", "description": "Descripción/asunto del documento"},
                    "case_id": {"type": "string", "description": "UUID del expediente a vincular (opcional)"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["document_type_acronym", "reference"]
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
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a múltiples organizaciones)"}
                },
                "required": ["document_id"]
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
            }
        },
        # ===== EXPEDIENTES (Operaciones) =====
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
            }
        },
        # ===== SISTEMA (Catálogos adicionales) =====
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
            }
        },
        # ===== NUEVOS TOOLS - FASE 2 =====
        # ===== DOCUMENTOS (Firma/Rechazo/Eliminacion) =====
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
            }
        },
        # ===== EXPEDIENTES (Operaciones nuevas) =====
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
            }
        },
        {
            "name": "prepare_transfer",
            "description": """Preparar transferencia - Obtiene sectores disponibles para transferir expediente.

USA ESTA TOOL ANTES DE transfer_case para ver que sectores estan disponibles.

RESPUESTA:
- available_sectors: lista de sectores con sector_id, nombre y departamento
- total: cantidad de sectores disponibles""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a multiples organizaciones)"}
                },
                "required": ["case_id"]
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
            }
        },
        # ===== SISTEMA (Catalogos nuevos) =====
        {
            "name": "get_case_templates",
            "description": """Templates de expedientes - Lista plantillas disponibles para crear expedientes.

USA ESTA TOOL PARA:
- "Que tipos de expedientes puedo crear?"
- Obtener case_template_id para create_case""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "UUID de la municipalidad (requerido si tienes acceso a multiples organizaciones)"}
                },
                "required": []
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
            }
        },
        # ===== NOTAS =====
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
            }
        },
        # ===== NUEVOS TOOLS - FASE 3 =====
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
            }
        },
    ]

    return create_jsonrpc_response(request_id, {"tools": tools})


async def handle_list_my_tenants(request_id: Any, authorization_header: str) -> Dict:
    """
    Lista los tenants (municipalidades) del usuario autenticado.

    Esta tool especial NO requiere tenant_id, permitiendo al usuario
    descubrir sus tenants disponibles antes de seleccionar uno.
    """
    if not authorization_header or not authorization_header.startswith("Bearer "):
        return create_jsonrpc_error(request_id, -32001, "Autenticación requerida. Usa OAuth para conectar.")

    token = authorization_header[7:]

    try:
        payload = verify_mcp_token(token)
    except ValueError as e:
        return create_jsonrpc_error(request_id, -32001, f"Token inválido: {str(e)}")

    email = payload.get("email")
    if not email:
        email = get_email_from_userinfo(token)

    if not email:
        return create_jsonrpc_error(request_id, -32001, "No se pudo obtener email del token")

    tenants = find_user_all_tenants(email)

    result = {
        "tenants": [
            {"tenant_id": str(t["municipality_id"]), "name": t["municipality_name"]}
            for t in tenants
        ],
        "total": len(tenants),
        "hint": "Usa tenant_id en tu próxima llamada para trabajar en esa municipalidad"
    }

    return create_jsonrpc_response(request_id, {
        "content": [{
            "type": "text",
            "text": json.dumps(result, ensure_ascii=False, indent=2)
        }],
        "isError": False
    })


async def handle_call_tool(request_id: Any, params: Dict, authorization_header: str = None) -> Dict:
    """
    Maneja tools/call request.

    Autenticación SOLO via OAuth 2.0:
    - Auth0 JWT: Header "Authorization: Bearer <token>" (auto-resuelve user/municipality)

    Excepciones (sin auth requerida):
    - get_agent_guide: Devuelve la guía del sistema

    Multi-Tenant:
    - list_my_tenants: Lista tenants del usuario (sin requerir tenant_id)
    - Otras tools: Si usuario tiene múltiples tenants, debe especificar tenant_id
    """
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    logger.info(f"Tool llamado: {tool_name}")

    # =====================================================================
    # TOOLS SIN AUTENTICACIÓN
    # =====================================================================
    if tool_name == "get_agent_guide":
        from pathlib import Path
        guide_path = Path(__file__).parent / "GUIA_AGENTE_IA.md"
        if guide_path.exists():
            guide_content = guide_path.read_text(encoding="utf-8")
            result = {
                "guide": guide_content,
                "version": "2.2",
                "tools_count": 32,
                "last_updated": "2026-02-13"
            }
        else:
            result = {"error": "Guía no encontrada", "path": str(guide_path)}

        return create_jsonrpc_response(request_id, {
            "content": [{
                "type": "text",
                "text": json.dumps(result, indent=2, default=str)
            }],
            "isError": False
        })

    # =====================================================================
    # TOOL ESPECIAL: list_my_tenants (no requiere tenant_id)
    # =====================================================================
    if tool_name == "list_my_tenants":
        return await handle_list_my_tenants(request_id, authorization_header)

    # Extraer tenant_id de los argumentos (para multi-tenant)
    tenant_id = arguments.get("tenant_id")

    try:
        ctx = None
        jwt_user_id = None

        # =====================================================================
        # MÉTODO 1: Auth0 JWT (si hay Authorization header)
        # =====================================================================
        if authorization_header and authorization_header.startswith("Bearer "):
            try:
                ctx, jwt_user_id = validate_mcp_jwt(authorization_header, tenant_id=tenant_id)
                logger.info(f"[Auth0] Autenticación exitosa: user_id={jwt_user_id}, schema={ctx.schema_name}")
            except MultiTenantSelectionRequired as e:
                # Usuario tiene múltiples tenants y no especificó tenant_id
                logger.info(f"[Auth0] Usuario multi-tenant sin selección: {len(e.tenants)} tenants")
                return create_jsonrpc_response(request_id, {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "error": "multi_tenant_selection_required",
                            "message": f"Tienes acceso a {len(e.tenants)} municipalidades. Especifica tenant_id en tu próxima llamada.",
                            "available_tenants": [
                                {
                                    "tenant_id": str(t["municipality_id"]),
                                    "name": t["municipality_name"],
                                    "schema": t["schema_name"]
                                }
                                for t in e.tenants
                            ],
                            "example": f'Usa tenant_id="{e.tenants[0]["municipality_id"]}" para {e.tenants[0]["municipality_name"]}',
                            "hint": "Puedes usar la tool list_my_tenants para ver tus municipalidades disponibles"
                        }, ensure_ascii=False, indent=2)
                    }],
                    "isError": True
                })
            except ValueError as e:
                logger.warning(f"[Auth0] Falló autenticación JWT: {e}")
                # JWT inválido - ctx queda None y devolverá error de OAuth requerido
                ctx = None

        # =====================================================================
        # OAuth es REQUERIDO - no hay fallback a API Key
        # =====================================================================
        if ctx is None:
            return create_jsonrpc_error(
                request_id,
                -32001,
                "Autenticación OAuth requerida. Usa Authorization: Bearer <jwt>"
            )

        # Si autenticamos con JWT, SIEMPRE usar el user_id del token
        # (ignorar lo que ChatGPT envíe, puede ser placeholder "USER_UUID")
        if jwt_user_id:
            arguments["user_id"] = jwt_user_id
            arguments["municipality_id"] = ctx.municipality_id  # También inyectar municipality
            logger.info(f"[Auth0] user_id inyectado desde JWT: {jwt_user_id}")

        # 3. EJECUTAR TOOL
        result = None

        if tool_name == "search_cases":
            result = cases.search_cases(
                ctx=ctx,
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search"),
                status=arguments.get("status"),
                date_filter=arguments.get("date_filter"),
                sector_filter=arguments.get("sector_filter"),
                user_id=arguments.get("user_id")
            )

        elif tool_name == "get_case":
            result = cases.get_case(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments.get("user_id"),
                include_documents=arguments.get("include_documents", False)
            )

        elif tool_name == "get_case_history":
            result = cases.get_case_history(
                ctx=ctx,
                case_id=arguments["case_id"]
            )

        elif tool_name == "get_case_documents":
            result = cases.get_case_documents(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments.get("user_id")
            )

        elif tool_name == "get_case_permissions":
            result = cases.get_case_permissions(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"]
            )

        elif tool_name == "search_documents":
            result = documents.search_documents(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search"),
                status=arguments.get("status"),
                document_type=arguments.get("document_type"),
                case_id=arguments.get("case_id")
            )

        elif tool_name == "get_document":
            result = await documents.get_document(
                ctx=ctx,
                document_id=arguments["document_id"],
                user_id=arguments.get("user_id")
            )

        elif tool_name == "get_document_types":
            result = system.get_document_types(ctx=ctx)


        elif tool_name == "get_user_info":
            result = system.get_user_info(
                ctx=ctx,
                user_id=arguments["user_id"]
            )


        elif tool_name == "get_pending_signatures":
            result = documents.get_pending_signatures(
                ctx=ctx,
                user_id=arguments["user_id"]
            )

        elif tool_name == "get_document_content":
            result = documents.get_document_content(
                ctx=ctx,
                document_id=arguments["document_id"]
            )

        # ===== NUEVOS TOOLS DE OPERACIONES =====

        # Documentos (Escritura)
        elif tool_name == "create_document":
            result = documents.create_document(
                ctx=ctx,
                document_type_acronym=arguments["document_type_acronym"],
                reference=arguments["reference"],
                user_id=arguments["user_id"],
                case_id=arguments.get("case_id")
            )

        elif tool_name == "save_document":
            result = documents.save_document(
                ctx=ctx,
                document_id=arguments["document_id"],
                user_id=arguments["user_id"],
                content=arguments.get("content"),
                reference=arguments.get("reference"),
                signers=arguments.get("signers")
            )

        elif tool_name == "start_signing":
            result = await documents.start_signing(
                ctx=ctx,
                document_id=arguments["document_id"],
                user_id=arguments["user_id"]
            )

        # Expedientes (Operaciones)
        elif tool_name == "get_case_by_number":
            result = cases.get_case_by_number(
                ctx=ctx,
                case_number=arguments["case_number"],
                user_id=arguments.get("user_id")
            )


        elif tool_name == "prepare_assignment":
            result = cases.prepare_assignment(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"]
            )

        elif tool_name == "assign_case":
            result = cases.assign_case(
                ctx=ctx,
                case_id=arguments["case_id"],
                target_sector_id=arguments["target_sector_id"],
                reason=arguments["reason"],
                user_id=arguments["user_id"],
                assigned_user_id=arguments.get("assigned_user_id"),
                create_official_doc=arguments.get("create_official_doc", False)
            )

        # Sistema (Catálogos)
        elif tool_name == "get_document_states":
            result = system.get_document_states(ctx=ctx)

        # ===== NUEVOS TOOLS - FASE 2 =====

        # Documentos (Firma/Rechazo/Eliminacion)
        elif tool_name == "reject_document":
            result = documents.reject_document(
                ctx=ctx,
                document_id=arguments["document_id"],
                user_id=arguments["user_id"],
                reason=arguments["reason"]
            )

        # Expedientes (Operaciones nuevas)
        elif tool_name == "propose_document":
            result = cases.propose_document(
                ctx=ctx,
                case_id=arguments["case_id"],
                document_draft_id=arguments["document_draft_id"],
                user_id=arguments["user_id"]
            )

        elif tool_name == "prepare_transfer":
            result = cases.prepare_transfer(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"]
            )

        elif tool_name == "reject_proposal":
            result = cases.reject_proposal(
                ctx=ctx,
                case_id=arguments["case_id"],
                proposed_id=arguments["proposed_id"],
                user_id=arguments["user_id"]
            )

        # Sistema (Catalogos nuevos)
        elif tool_name == "get_case_templates":
            result = system.get_case_templates(
                ctx=ctx,
                user_id=arguments["user_id"]
            )

        elif tool_name == "search_users":
            result = system.search_users(
                ctx=ctx,
                search=arguments["search"],
                limit=int(arguments.get("limit", 10))
            )

        # Notas
        elif tool_name == "get_notes":
            result = notes.get_notes(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                unread_only=arguments.get("unread_only", False),
                search=arguments.get("search")
            )

        # ===== NUEVOS TOOLS - FASE 3 =====

        elif tool_name == "search_document_by_number":
            result = documents.search_document_by_number(
                ctx=ctx,
                doc_number=arguments.get("doc_number", ""),
                user_id=arguments["user_id"]
            )

        elif tool_name == "get_signature_details":
            result = await documents.get_signature_details(
                ctx=ctx,
                document_id=arguments.get("document_id", ""),
                user_id=arguments["user_id"]
            )

        elif tool_name == "get_sent_notes":
            result = notes.get_sent_notes(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search")
            )

        elif tool_name == "get_archived_notes":
            result = notes.get_archived_notes(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search")
            )

        elif tool_name == "get_note_detail":
            result = notes.get_note_detail(
                ctx=ctx,
                note_id=arguments.get("note_id", ""),
                user_id=arguments["user_id"]
            )

        else:
            return create_jsonrpc_error(request_id, -32601, f"Tool desconocido: {tool_name}")

        # 4. RETORNAR RESULTADO
        return create_jsonrpc_response(request_id, {
            "content": [{
                "type": "text",
                "text": json.dumps(result, indent=2, default=str)
            }],
            "isError": False
        })

    except ValueError as e:
        logger.error(f"Error de validación en tool {tool_name}: {e}")
        return create_jsonrpc_response(request_id, {
            "content": [{"type": "text", "text": f"Error de validación: {str(e)}"}],
            "isError": True
        })
    except Exception as e:
        logger.exception(f"Error ejecutando tool {tool_name}")
        return create_jsonrpc_response(request_id, {
            "content": [{"type": "text", "text": f"Error interno: {str(e)}"}],
            "isError": True
        })


async def process_jsonrpc_request(body: Dict, authorization_header: str = None) -> Dict:
    """Procesa una request JSON-RPC y retorna la respuesta."""
    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")

    logger.info(f"JSON-RPC method: {method}")

    # Router de métodos MCP
    if method == "initialize":
        return await handle_initialize(request_id, params)

    elif method == "notifications/initialized":
        # Notification, no response needed
        return None

    elif method == "tools/list":
        return await handle_list_tools(request_id)

    elif method == "tools/call":
        return await handle_call_tool(request_id, params, authorization_header)

    elif method == "ping":
        return create_jsonrpc_response(request_id, {})

    else:
        return create_jsonrpc_error(request_id, -32601, f"Method not found: {method}")


# ============================================================================
# HTTP ENDPOINTS
# ============================================================================

async def openapi_spec(request: Request) -> JSONResponse:
    """
    OpenAPI 3.0 Spec para ChatGPT Actions y otros LLMs.

    Describe la REST API existente para que LLMs puedan:
    - Descubrir endpoints disponibles
    - Entender parámetros y respuestas
    - Configurar OAuth 2.0 automáticamente
    """
    base_url = os.getenv("MCP_RESOURCE_URI", "http://localhost:8005")
    auth0_domain = os.getenv("AUTH0_DOMAIN", "gdilatam.us.auth0.com")

    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "GDI MCP Server API",
            "version": "1.0.0",
            "description": "API de Gestión Documental Inteligente para gobiernos latinoamericanos. Permite consultar expedientes, documentos, firmas pendientes y catálogos del sistema."
        },
        "servers": [{"url": base_url}],
        "paths": {
            # ===== CASES (Expedientes) =====
            "/api/v1/cases/search": {
                "get": {
                    "operationId": "searchCases",
                    "summary": "Buscar expedientes",
                    "description": "Busca expedientes por número, referencia, contenido de documentos, nombres, direcciones, etc.",
                    "parameters": [
                        {"name": "search", "in": "query", "schema": {"type": "string"}, "description": "Texto a buscar (número, referencia, contenido)"},
                        {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["active", "inactive", "archived"]}, "description": "Estado del expediente"},
                        {"name": "date_filter", "in": "query", "schema": {"type": "string", "enum": ["today", "week", "month", "year"]}, "description": "Filtro temporal"},
                        {"name": "sector_filter", "in": "query", "schema": {"type": "string"}, "description": "Filtrar por sector (acronym)"},
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}, "description": "Página"},
                        {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 20}, "description": "Resultados por página (max 100)"}
                    ],
                    "responses": {
                        "200": {"description": "Lista de expedientes con case_number, reference, ai_summary"},
                        "401": {"description": "No autorizado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}": {
                "get": {
                    "operationId": "getCase",
                    "summary": "Detalle de expediente",
                    "description": "Obtiene información completa de un expediente específico",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"},
                        {"name": "include_documents", "in": "query", "schema": {"type": "boolean", "default": False}, "description": "Incluir lista de documentos"}
                    ],
                    "responses": {
                        "200": {"description": "Detalle del expediente"},
                        "404": {"description": "Expediente no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/history": {
                "get": {
                    "operationId": "getCaseHistory",
                    "summary": "Historial de expediente",
                    "description": "Obtiene historial completo con movimientos, documentos y resumen IA",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "responses": {
                        "200": {"description": "Historial con ai_summary, movements, documents"},
                        "404": {"description": "Expediente no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/documents": {
                "get": {
                    "operationId": "getCaseDocuments",
                    "summary": "Documentos del expediente",
                    "description": "Lista documentos oficiales y propuestos vinculados al expediente",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "responses": {
                        "200": {"description": "Lista de documentos oficiales y propuestos"},
                        "404": {"description": "Expediente no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/permissions": {
                "get": {
                    "operationId": "getCasePermissions",
                    "summary": "Permisos sobre expediente",
                    "description": "Consulta qué acciones puede realizar el usuario sobre el expediente",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "responses": {
                        "200": {"description": "Permisos: can_view, can_transfer, can_archive, etc."},
                        "404": {"description": "Expediente no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            # ===== DOCUMENTS (Documentos) =====
            "/api/v1/documents/search": {
                "get": {
                    "operationId": "searchDocuments",
                    "summary": "Buscar documentos",
                    "description": "Busca documentos por número, referencia, contenido completo, nombres, etc.",
                    "parameters": [
                        {"name": "search", "in": "query", "schema": {"type": "string"}, "description": "Texto a buscar"},
                        {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["pending", "sent_to_sign", "signed", "rejected"]}, "description": "Estado del documento"},
                        {"name": "document_type", "in": "query", "schema": {"type": "string"}, "description": "Tipo de documento (INF, DICT, CAEX, etc.)"},
                        {"name": "case_id", "in": "query", "schema": {"type": "string"}, "description": "Filtrar por expediente"},
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}, "description": "Página"},
                        {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 20}, "description": "Resultados por página"}
                    ],
                    "responses": {
                        "200": {"description": "Lista de documentos"},
                        "401": {"description": "No autorizado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/pending-signatures": {
                "get": {
                    "operationId": "getPendingSignatures",
                    "summary": "Firmas pendientes",
                    "description": "Lista documentos que esperan la firma del usuario actual",
                    "parameters": [],
                    "responses": {
                        "200": {"description": "Lista de documentos pendientes de firma"},
                        "401": {"description": "No autorizado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/{document_id}": {
                "get": {
                    "operationId": "getDocument",
                    "summary": "Detalle de documento",
                    "description": "Obtiene información completa del documento incluyendo resumen IA",
                    "parameters": [
                        {"name": "document_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del documento"}
                    ],
                    "responses": {
                        "200": {"description": "Detalle del documento con ai_summary"},
                        "404": {"description": "Documento no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/{document_id}/content": {
                "get": {
                    "operationId": "getDocumentContent",
                    "summary": "Contenido completo del documento",
                    "description": "Obtiene el contenido HTML completo del documento (solo oficiales)",
                    "parameters": [
                        {"name": "document_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del documento oficial"}
                    ],
                    "responses": {
                        "200": {"description": "Contenido HTML del documento"},
                        "404": {"description": "Documento no encontrado"},
                        "400": {"description": "Solo disponible para documentos oficiales"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/{document_id}/url": {
                "get": {
                    "operationId": "getDocumentUrl",
                    "summary": "URL de descarga del documento",
                    "description": "Obtiene URL firmada temporal para descargar el PDF del documento oficial",
                    "parameters": [
                        {"name": "document_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del documento oficial"}
                    ],
                    "responses": {
                        "200": {"description": "URL del documento con expiración"},
                        "404": {"description": "Documento no encontrado o no es oficial"},
                        "401": {"description": "API Key inválida"}
                    },
                    "security": [{"ApiKeyAuth": []}]
                }
            },
            # ===== SYSTEM (Catálogos) =====
            "/api/v1/system/document-types": {
                "get": {
                    "operationId": "getDocumentTypes",
                    "summary": "Tipos de documentos",
                    "description": "Lista todos los tipos de documentos disponibles (INF, DICT, CAEX, etc.)",
                    "parameters": [],
                    "responses": {
                        "200": {"description": "Lista de tipos con name y acronym"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/system/users/{user_id}": {
                "get": {
                    "operationId": "getUserInfo",
                    "summary": "Información del usuario",
                    "description": "Obtiene información del usuario: sector, roles, permisos adicionales",
                    "parameters": [
                        {"name": "user_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del usuario"}
                    ],
                    "responses": {
                        "200": {"description": "Info del usuario con sector y roles"},
                        "404": {"description": "Usuario no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/system/document-states": {
                "get": {
                    "operationId": "getDocumentStates",
                    "summary": "Estados de documentos",
                    "description": "Catálogo de estados posibles para documentos",
                    "parameters": [],
                    "responses": {
                        "200": {"description": "Lista de estados con nombre y código"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            # ===== NUEVOS ENDPOINTS DE OPERACIONES =====
            "/api/v1/cases/number/{case_number}": {
                "get": {
                    "operationId": "getCaseByNumber",
                    "summary": "Buscar expediente por número",
                    "description": "Obtiene un expediente por su número exacto",
                    "parameters": [
                        {"name": "case_number", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Número exacto del expediente (ej: EE-2026-00001234)"}
                    ],
                    "responses": {
                        "200": {"description": "Expediente encontrado"},
                        "404": {"description": "Expediente no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/prepare-assignment": {
                "get": {
                    "operationId": "prepareAssignment",
                    "summary": "Preparar asignación",
                    "description": "Verifica permisos y obtiene información para asignar expediente",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "responses": {
                        "200": {"description": "Información de permisos y sectores disponibles"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/assign": {
                "post": {
                    "operationId": "assignCase",
                    "summary": "Asignar expediente",
                    "description": "Asigna un expediente a otro sector sin transferir propiedad",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["target_sector_id", "reason"],
                                    "properties": {
                                        "target_sector_id": {"type": "string", "description": "UUID del sector destino"},
                                        "reason": {"type": "string", "description": "Motivo de la asignación (5-500 chars)"},
                                        "assigned_user_id": {"type": "string", "description": "UUID del usuario asignado (opcional)"},
                                        "create_official_doc": {"type": "boolean", "description": "Generar documento PV automático"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Asignación exitosa"},
                        "403": {"description": "Sin permisos para asignar"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/close-assign": {
                "post": {
                    "operationId": "closeAssignment",
                    "summary": "Cerrar asignación",
                    "description": "Cierra una asignación de expediente",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["movement_id", "reason"],
                                    "properties": {
                                        "movement_id": {"type": "string", "description": "UUID del movimiento a cerrar"},
                                        "reason": {"type": "string", "description": "Razón del cierre (5-500 chars)"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Asignación cerrada"},
                        "404": {"description": "Movimiento no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/": {
                "post": {
                    "operationId": "createDocument",
                    "summary": "Crear documento",
                    "description": "Crea un nuevo documento en estado borrador",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["document_type_acronym", "reference"],
                                    "properties": {
                                        "document_type_acronym": {"type": "string", "description": "Acrónimo del tipo (INF, DICT, etc.)"},
                                        "reference": {"type": "string", "description": "Descripción del documento"},
                                        "case_id": {"type": "string", "description": "UUID del expediente a vincular (opcional)"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {"description": "Documento creado"},
                        "400": {"description": "Datos inválidos"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/{document_id}/start-signing": {
                "post": {
                    "operationId": "startSigning",
                    "summary": "Iniciar proceso de firma",
                    "description": "Envía un documento al proceso de firma",
                    "parameters": [
                        {"name": "document_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del documento"}
                    ],
                    "responses": {
                        "200": {"description": "Proceso de firma iniciado"},
                        "403": {"description": "No autorizado (debe ser el creador)"},
                        "409": {"description": "Documento no puede firmarse en su estado actual"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            # ===== NUEVOS ENDPOINTS - FASE 2 =====
            "/api/v1/documents/{document_id}/sign": {
                "post": {
                    "operationId": "signDocument",
                    "summary": "Firmar documento",
                    "description": "Firma un documento como el usuario actual",
                    "parameters": [
                        {"name": "document_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del documento"}
                    ],
                    "responses": {
                        "200": {"description": "Documento firmado"},
                        "403": {"description": "No autorizado"},
                        "409": {"description": "Estado invalido"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/{document_id}/reject": {
                "post": {
                    "operationId": "rejectDocument",
                    "summary": "Rechazar documento",
                    "description": "Rechaza un documento en proceso de firma",
                    "parameters": [
                        {"name": "document_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del documento"}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["reason"],
                                    "properties": {
                                        "reason": {"type": "string", "description": "Motivo del rechazo"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Documento rechazado"},
                        "403": {"description": "No autorizado"},
                        "409": {"description": "Estado invalido"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/": {
                "post": {
                    "operationId": "createCase",
                    "summary": "Crear expediente",
                    "description": "Crea un nuevo expediente con caratula automatica",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["case_template_id", "reference"],
                                    "properties": {
                                        "case_template_id": {"type": "string", "description": "UUID del template"},
                                        "reference": {"type": "string", "description": "Asunto del expediente"},
                                        "owner_sector_id": {"type": "string", "description": "UUID del sector propietario (opcional)"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {"description": "Expediente creado"},
                        "400": {"description": "Datos invalidos"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/transfer": {
                "post": {
                    "operationId": "transferCase",
                    "summary": "Transferir expediente",
                    "description": "Transfiere propiedad del expediente a otro sector",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["target_sector_id", "reason"],
                                    "properties": {
                                        "target_sector_id": {"type": "string", "description": "UUID del sector destino"},
                                        "reason": {"type": "string", "description": "Motivo (5-500 chars)"},
                                        "assigned_user_id": {"type": "string", "description": "UUID usuario asignado (opcional)"},
                                        "create_official_doc": {"type": "boolean", "description": "Generar PV automatico"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Transferencia exitosa"},
                        "403": {"description": "Sin permisos"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/documents/link": {
                "post": {
                    "operationId": "linkDocumentToCase",
                    "summary": "Vincular documento oficial",
                    "description": "Vincula un documento oficial firmado a un expediente",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["official_document_id"],
                                    "properties": {
                                        "official_document_id": {"type": "string", "description": "UUID del documento oficial"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Documento vinculado"},
                        "403": {"description": "Sin permisos"},
                        "404": {"description": "No encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/documents/propose": {
                "post": {
                    "operationId": "proposeDocument",
                    "summary": "Proponer documento",
                    "description": "Propone un borrador para vincular al expediente",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["document_draft_id"],
                                    "properties": {
                                        "document_draft_id": {"type": "string", "description": "UUID del borrador"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Propuesta creada"},
                        "400": {"description": "Datos invalidos"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/prepare-transfer": {
                "get": {
                    "operationId": "prepareTransfer",
                    "summary": "Preparar transferencia",
                    "description": "Obtiene sectores disponibles para transferir expediente",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "responses": {
                        "200": {"description": "Sectores disponibles"},
                        "403": {"description": "Sin permisos"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/documents/accept-proposal": {
                "post": {
                    "operationId": "acceptProposal",
                    "summary": "Aceptar propuesta",
                    "description": "Acepta documento propuesto y lo vincula",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["proposed_id"],
                                    "properties": {
                                        "proposed_id": {"type": "string", "description": "UUID de la propuesta"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Propuesta aceptada"},
                        "404": {"description": "Propuesta no encontrada"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/{case_id}/documents/reject-proposal": {
                "post": {
                    "operationId": "rejectProposal",
                    "summary": "Rechazar propuesta",
                    "description": "Rechaza documento propuesto sin vincularlo",
                    "parameters": [
                        {"name": "case_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del expediente"}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["proposed_id"],
                                    "properties": {
                                        "proposed_id": {"type": "string", "description": "UUID de la propuesta"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Propuesta rechazada"},
                        "404": {"description": "Propuesta no encontrada"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/system/sectors": {
                "get": {
                    "operationId": "getSectors",
                    "summary": "Sectores y departamentos",
                    "description": "Lista todos los sectores activos con sus departamentos",
                    "parameters": [],
                    "responses": {
                        "200": {"description": "Lista de sectores"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/system/case-templates": {
                "get": {
                    "operationId": "getCaseTemplates",
                    "summary": "Templates de expedientes",
                    "description": "Lista plantillas disponibles para crear expedientes",
                    "parameters": [],
                    "responses": {
                        "200": {"description": "Lista de templates"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/system/users/search": {
                "get": {
                    "operationId": "searchUsers",
                    "summary": "Buscar usuarios",
                    "description": "Busca usuarios por nombre para autocompletado",
                    "parameters": [
                        {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Texto a buscar (min 2 chars)"},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 10}, "description": "Cantidad maxima"}
                    ],
                    "responses": {
                        "200": {"description": "Lista de usuarios"},
                        "400": {"description": "Query muy corta"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/cases/sectors/{sector_id}/users": {
                "get": {
                    "operationId": "getSectorUsers",
                    "summary": "Usuarios de un sector",
                    "description": "Obtiene los usuarios que pertenecen a un sector especifico",
                    "parameters": [
                        {"name": "sector_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del sector"}
                    ],
                    "responses": {
                        "200": {"description": "Lista de usuarios del sector"},
                        "404": {"description": "Sector no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/search-official/{doc_number}": {
                "get": {
                    "operationId": "searchDocumentByNumber",
                    "summary": "Buscar documento por numero oficial",
                    "description": "Busca un documento oficial por su numero exacto (ej: INF-2026-00000060-TXST-TESO)",
                    "parameters": [
                        {"name": "doc_number", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Numero oficial del documento"}
                    ],
                    "responses": {
                        "200": {"description": "Documento encontrado"},
                        "404": {"description": "Documento no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/{document_id}/signature-details": {
                "get": {
                    "operationId": "getSignatureDetails",
                    "summary": "Detalles de firma de un documento",
                    "description": "Obtiene lista de firmantes, estado de cada uno y orden de firma",
                    "parameters": [
                        {"name": "document_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del documento"}
                    ],
                    "responses": {
                        "200": {"description": "Detalles de firma con firmantes y estados"},
                        "404": {"description": "Documento no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/import": {
                "post": {
                    "operationId": "importDocument",
                    "summary": "Importar documento PDF externo",
                    "description": "Importa un documento PDF externo al sistema",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {"type": "string", "format": "binary", "description": "Archivo PDF a importar"},
                                        "document_type_acronym": {"type": "string", "description": "Tipo de documento"},
                                        "reference": {"type": "string", "description": "Descripcion/asunto"},
                                        "case_id": {"type": "string", "description": "UUID del expediente (opcional)"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {"description": "Documento importado"},
                        "400": {"description": "Datos invalidos"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/{document_id}/imported-pdf": {
                "put": {
                    "operationId": "replaceImportedPdf",
                    "summary": "Reemplazar PDF de documento importado",
                    "description": "Reemplaza el archivo PDF de un documento previamente importado",
                    "parameters": [
                        {"name": "document_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID del documento importado"}
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {"type": "string", "format": "binary", "description": "Nuevo archivo PDF"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "PDF reemplazado"},
                        "404": {"description": "Documento no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/documents/check-signer-permissions": {
                "get": {
                    "operationId": "checkSignerPermissions",
                    "summary": "Verificar permisos de firmante",
                    "description": "Verifica si un usuario tiene permisos para firmar un documento",
                    "parameters": [
                        {"name": "document_id", "in": "query", "required": True, "schema": {"type": "string"}, "description": "UUID del documento"},
                        {"name": "signer_id", "in": "query", "schema": {"type": "string"}, "description": "UUID del firmante (opcional, usa el usuario actual)"}
                    ],
                    "responses": {
                        "200": {"description": "Permisos del firmante"},
                        "404": {"description": "Documento no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/system/users/list": {
                "get": {
                    "operationId": "listAllUsers",
                    "summary": "Listar todos los usuarios",
                    "description": "Obtiene lista paginada de todos los usuarios del tenant",
                    "parameters": [
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}, "description": "Pagina"},
                        {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 50}, "description": "Resultados por pagina"},
                        {"name": "search", "in": "query", "schema": {"type": "string"}, "description": "Buscar por nombre o email"}
                    ],
                    "responses": {
                        "200": {"description": "Lista de usuarios"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/notes/received": {
                "get": {
                    "operationId": "getNotesReceived",
                    "summary": "Notas recibidas",
                    "description": "Lista notas oficiales recibidas",
                    "parameters": [
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}, "description": "Pagina"},
                        {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 20}, "description": "Resultados por pagina"},
                        {"name": "unread_only", "in": "query", "schema": {"type": "boolean", "default": False}, "description": "Solo no leidas"},
                        {"name": "search", "in": "query", "schema": {"type": "string"}, "description": "Buscar en contenido"}
                    ],
                    "responses": {
                        "200": {"description": "Lista de notas"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/notes/sent": {
                "get": {
                    "operationId": "getSentNotes",
                    "summary": "Obtener notas enviadas",
                    "description": "Lista notas/comunicaciones enviadas por el usuario",
                    "parameters": [
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}, "description": "Pagina"},
                        {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 20}, "description": "Resultados por pagina"},
                        {"name": "search", "in": "query", "schema": {"type": "string"}, "description": "Buscar en contenido de notas"}
                    ],
                    "responses": {
                        "200": {"description": "Lista de notas enviadas"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/notes/archived": {
                "get": {
                    "operationId": "getArchivedNotes",
                    "summary": "Obtener notas archivadas",
                    "description": "Lista notas archivadas por el usuario",
                    "parameters": [
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}, "description": "Pagina"},
                        {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 20}, "description": "Resultados por pagina"},
                        {"name": "search", "in": "query", "schema": {"type": "string"}, "description": "Buscar en contenido de notas"}
                    ],
                    "responses": {
                        "200": {"description": "Lista de notas archivadas"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/notes/{note_id}/archive": {
                "patch": {
                    "operationId": "archiveNote",
                    "summary": "Archivar nota",
                    "description": "Archiva una nota especifica",
                    "parameters": [
                        {"name": "note_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID de la nota"}
                    ],
                    "responses": {
                        "200": {"description": "Nota archivada"},
                        "404": {"description": "Nota no encontrada"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/notes/{note_id}": {
                "get": {
                    "operationId": "getNoteDetail",
                    "summary": "Detalle de nota",
                    "description": "Obtiene detalle completo de una nota incluyendo contenido, remitente y destinatarios",
                    "parameters": [
                        {"name": "note_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "UUID de la nota"}
                    ],
                    "responses": {
                        "200": {"description": "Detalle de la nota"},
                        "404": {"description": "Nota no encontrada"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            }
        },
        "components": {
            "securitySchemes": {
                "OAuth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": f"https://{auth0_domain}/authorize",
                            "tokenUrl": f"https://{auth0_domain}/oauth/token",
                            "scopes": {
                                "openid": "OpenID Connect",
                                "profile": "User profile",
                                "email": "User email"
                            }
                        }
                    }
                },
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                    "description": "API Key para autenticación (fallback)"
                }
            }
        }
    }

    return JSONResponse(spec)


async def mcp_manifest(request: Request) -> JSONResponse:
    """
    MCP Manifest - /.well-known/mcp.json

    Describe las capacidades del servidor MCP.
    ChatGPT y otros clientes usan esto para descubrir el servidor.
    """
    resource_uri = os.getenv("MCP_RESOURCE_URI", "http://localhost:8005")

    return JSONResponse({
        "name": "gdi-mcp-server",
        "version": "1.0.0",
        "description": "GDI Backend MCP Server - Sistema de Gestión Documental Inteligente para gobiernos latinoamericanos",
        "protocol_version": MCP_PROTOCOL_VERSION,
        "transport": {
            "type": "http",
            "url": f"{resource_uri}/mcp"
        },
        "capabilities": {
            "tools": True,
            "resources": False,
            "prompts": False
        },
        "authentication": {
            "type": "oauth2",
            "oauth_protected_resource": f"{resource_uri}/.well-known/oauth-protected-resource",
            "oauth_authorization_server": f"{resource_uri}/.well-known/oauth-authorization-server"
        }
    })


async def root_endpoint(request: Request) -> JSONResponse:
    """Root endpoint para health checks de Railway y browsers."""
    return JSONResponse({
        "service": "gdi-mcp-server",
        "status": "ok",
        "transport": "streamable-http",
        "mcp_endpoint": "/mcp",
        "health": "/health",
        "docs": "/.well-known/mcp.json"
    })


async def health(request: Request) -> JSONResponse:
    """Health check para Railway."""
    return JSONResponse({
        "status": "ok",
        "service": "gdi-mcp-server",
        "transport": "streamable-http",
        "protocol_version": MCP_PROTOCOL_VERSION,
        "rest_api_version": "v1",
        "endpoints": {
            "mcp": "/mcp",
            "rest_api": "/api/v1/",
            "openapi_spec": "/.well-known/openapi.json",
            "mcp_manifest": "/.well-known/mcp.json",
            "oauth_protected_resource": "/.well-known/oauth-protected-resource",
            "oauth_authorization_server": "/.well-known/oauth-authorization-server"
        }
    })


async def oauth_protected_resource_metadata(request: Request) -> JSONResponse:
    """
    RFC 9728: OAuth 2.0 Protected Resource Metadata.

    Claude Code usa este endpoint para descubrir el Authorization Server (Auth0).
    Auth0 tiene DCR habilitado, así que apuntamos directo.
    """
    auth0_domain = os.getenv("AUTH0_DOMAIN", "gdilatam.us.auth0.com")
    resource_uri = os.getenv("MCP_RESOURCE_URI", "http://localhost:8005")

    return JSONResponse({
        "resource": resource_uri,
        # Apuntamos directo a Auth0 (tiene DCR habilitado)
        "authorization_servers": [f"https://{auth0_domain}"],
        "scopes_supported": ["openid", "profile", "email", "offline_access"],
        "bearer_methods_supported": ["header"]
    })


# DCR Proxy ELIMINADO - Ahora todos usan DCR nativo de Auth0
# Auth0 tiene DCR habilitado en Settings > Advanced > "OIDC Dynamic Application Registration"


async def oauth_authorization_server_metadata(request: Request) -> JSONResponse:
    """
    OAuth 2.0 Authorization Server Metadata (RFC 8414).

    ChatGPT busca este endpoint para obtener el metadata del Authorization Server.
    Retornamos el metadata de Auth0 directamente (proxy).

    ChatGPT específicamente busca:
    - /.well-known/oauth-authorization-server
    - /.well-known/oauth-authorization-server/mcp
    """
    import httpx

    auth0_domain = os.getenv("AUTH0_DOMAIN", "gdilatam.us.auth0.com")
    auth0_metadata_url = f"https://{auth0_domain}/.well-known/openid-configuration"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(auth0_metadata_url, timeout=10.0)
            response.raise_for_status()
            auth0_metadata = response.json()

            logger.info(f"[OAuth] Proxeando metadata de Auth0: {auth0_metadata_url}")
            return JSONResponse(auth0_metadata)

    except Exception as e:
        logger.error(f"[OAuth] Error obteniendo metadata de Auth0: {e}")
        # Fallback: retornar metadata mínimo construido manualmente
        return JSONResponse({
            "issuer": f"https://{auth0_domain}/",
            "authorization_endpoint": f"https://{auth0_domain}/authorize",
            "token_endpoint": f"https://{auth0_domain}/oauth/token",
            "userinfo_endpoint": f"https://{auth0_domain}/userinfo",
            "jwks_uri": f"https://{auth0_domain}/.well-known/jwks.json",
            "registration_endpoint": f"https://{auth0_domain}/oidc/register",
            "scopes_supported": ["openid", "profile", "email", "offline_access"],
            "response_types_supported": ["code", "token", "id_token", "code token", "code id_token", "token id_token", "code token id_token"],
            "grant_types_supported": ["authorization_code", "implicit", "refresh_token", "client_credentials"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "code_challenge_methods_supported": ["S256", "plain"]
        })


async def mcp_endpoint(request: Request) -> JSONResponse:
    """
    Endpoint principal MCP (Streamable HTTP).

    Soporta:
    - POST: Enviar JSON-RPC requests
    - GET: Abrir SSE stream (no implementado en esta versión básica)
    - DELETE: Terminar sesión

    Autenticación OAuth (RFC 9728):
    - Si tools/call sin Authorization ni API Key → 401 con WWW-Authenticate
    - Claude Code lee resource_metadata URL del header
    - Descubre Auth0 como Authorization Server
    - Abre navegador para login → token automático
    """
    # Obtener session ID del header
    session_id = request.headers.get("mcp-session-id")

    if request.method == "POST":
        try:
            body = await request.json()
        except Exception as e:
            return JSONResponse(
                create_jsonrpc_error(None, -32700, f"Parse error: {str(e)}"),
                status_code=400
            )

        # Obtener Authorization header para Auth0 JWT
        authorization_header = request.headers.get("Authorization")
        method = body.get("method")

        # =====================================================================
        # RFC 9728: Si es tools/call SIN auth, devolver 401 con OAuth metadata
        # Esto permite que Claude Code/ChatGPT descubran Auth0 y hagan login
        # EXCEPCIÓN: get_agent_guide no requiere autenticación
        # =====================================================================
        tool_name_in_params = body.get("params", {}).get("name", "")
        tools_without_auth = ["get_agent_guide"]

        if method == "tools/call" and not authorization_header and tool_name_in_params not in tools_without_auth:
            resource_uri = os.getenv("MCP_RESOURCE_URI", "http://localhost:8005")
            return JSONResponse(
                create_jsonrpc_error(body.get("id"), -32002, "Authorization required. Use OAuth to authenticate."),
                status_code=401,
                headers={
                    # RFC 9728 sección 5: resource_metadata apunta al endpoint de discovery
                    # Agregamos scope= para que ChatGPT sepa qué scopes solicitar
                    "WWW-Authenticate": f'Bearer scope="openid profile email offline_access" resource_metadata="{resource_uri}/.well-known/oauth-protected-resource"'
                }
            )

        # Procesar request (pasar Authorization para tools/call)
        response = await process_jsonrpc_request(body, authorization_header)

        # Si es notification, retornar 202 Accepted
        if response is None:
            return JSONResponse({}, status_code=202)

        # Headers de respuesta
        headers = {
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION
        }

        # Si es initialize, crear session ID
        if body.get("method") == "initialize":
            new_session_id = str(uuid.uuid4())
            sessions[new_session_id] = {"created": True}
            headers["Mcp-Session-Id"] = new_session_id

        return JSONResponse(response, headers=headers)

    elif request.method == "GET":
        # Clientes que buscan SSE/streaming - este server usa Streamable HTTP (POST)
        return JSONResponse(
            {
                "error": "Este servidor usa transporte Streamable HTTP. Usa POST /mcp para enviar JSON-RPC requests.",
                "transport": "streamable-http",
                "method": "POST",
                "endpoint": "/mcp"
            },
            status_code=405
        )

    elif request.method == "DELETE":
        if session_id and session_id in sessions:
            del sessions[session_id]
            return JSONResponse({"status": "terminated"})
        return JSONResponse({"error": "Session not found"}, status_code=404)

    else:
        return JSONResponse(
            {"error": f"Method {request.method} not allowed"},
            status_code=405
        )


# ============================================================================
# APPLICATION
# ============================================================================

# Rutas
routes = [
    # Root y Health check
    Route("/", root_endpoint, methods=["GET"]),
    Route("/health", health, methods=["GET"]),

    # OpenAPI Spec para ChatGPT Actions y otros LLMs
    Route("/.well-known/openapi.json", openapi_spec, methods=["GET"]),
    Route("/openapi.json", openapi_spec, methods=["GET"]),

    # MCP Manifest - Discovery del servidor
    Route("/.well-known/mcp.json", mcp_manifest, methods=["GET"]),

    # OAuth 2.0 Protected Resource Metadata (RFC 9728)
    # Apunta directo a Auth0 (tiene DCR nativo habilitado)
    Route("/.well-known/oauth-protected-resource", oauth_protected_resource_metadata, methods=["GET"]),

    # OAuth 2.0 Authorization Server Metadata (RFC 8414)
    # ChatGPT busca estos endpoints para discovery del Authorization Server
    Route("/.well-known/oauth-authorization-server", oauth_authorization_server_metadata, methods=["GET"]),
    Route("/.well-known/oauth-authorization-server/mcp", oauth_authorization_server_metadata, methods=["GET"]),

    # MCP Endpoint (JSON-RPC)
    Route("/mcp", mcp_endpoint, methods=["GET", "POST", "DELETE"]),

    # =========================================================================
    # REST API v1 (API Key por Schema)
    # =========================================================================

    # Cases (Expedientes - Lectura)
    Route("/api/v1/cases/search", api_search_cases, methods=["GET"]),
    Route("/api/v1/cases/number/{case_number:path}", api_get_case_by_number, methods=["GET"]),
    Route("/api/v1/cases/{case_id}", api_get_case, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/history", api_get_case_history, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/documents", api_get_case_documents, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/permissions", api_get_case_permissions, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/prepare-assignment", api_prepare_assignment, methods=["GET"]),
    Route("/api/v1/cases/sectors/{sector_id}/users", api_get_sector_users, methods=["GET"]),

    # Cases (Expedientes - Operaciones)
    Route("/api/v1/cases/{case_id}/assign", api_assign_case, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/close-assign", api_close_assignment, methods=["POST"]),

    # Cases (Expedientes - Operaciones nuevas)
    Route("/api/v1/cases/", api_create_case, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/transfer", api_transfer_case, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/documents/link", api_link_document, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/documents/propose", api_propose_document, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/prepare-transfer", api_prepare_transfer, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/documents/accept-proposal", api_accept_proposal, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/documents/reject-proposal", api_reject_proposal, methods=["POST"]),

    # Documents (Documentos - Lectura)
    Route("/api/v1/documents/search", api_search_documents, methods=["GET"]),
    Route("/api/v1/documents/pending-signatures", api_get_pending_signatures, methods=["GET"]),
    Route("/api/v1/documents/search-official/{doc_number:path}", api_search_document_by_number, methods=["GET"]),
    Route("/api/v1/documents/check-signer-permissions", api_check_signer_permissions, methods=["GET"]),
    Route("/api/v1/documents/{document_id}", api_get_document, methods=["GET"]),
    Route("/api/v1/documents/{document_id}/content", api_get_document_content, methods=["GET"]),
    Route("/api/v1/documents/{document_id}/url", api_get_document_url, methods=["GET"]),
    Route("/api/v1/documents/{document_id}/signature-details", api_get_signature_details, methods=["GET"]),

    # Documents (Documentos - Operaciones)
    Route("/api/v1/documents/", api_create_document, methods=["POST"]),
    Route("/api/v1/documents/import", api_import_document, methods=["POST"]),
    Route("/api/v1/documents/{document_id}", api_save_document, methods=["PATCH"]),
    Route("/api/v1/documents/{document_id}", api_delete_document, methods=["DELETE"]),
    Route("/api/v1/documents/{document_id}/imported-pdf", api_replace_imported_pdf, methods=["PUT"]),
    Route("/api/v1/documents/{document_id}/start-signing", api_start_signing, methods=["POST"]),
    Route("/api/v1/documents/{document_id}/sign", api_sign_document, methods=["POST"]),
    Route("/api/v1/documents/{document_id}/reject", api_reject_document, methods=["POST"]),

    # System (Catálogos)
    Route("/api/v1/system/document-types", api_get_document_types, methods=["GET"]),
    Route("/api/v1/system/document-states", api_get_document_states, methods=["GET"]),
    Route("/api/v1/system/sectors", api_get_sectors, methods=["GET"]),
    Route("/api/v1/system/case-templates", api_get_case_templates, methods=["GET"]),
    Route("/api/v1/system/users/list", api_list_all_users, methods=["GET"]),
    Route("/api/v1/system/users/search", api_search_users, methods=["GET"]),
    Route("/api/v1/system/users/{user_id}", api_get_user_info, methods=["GET"]),

    # Notas
    Route("/api/v1/notes/received", api_get_notes, methods=["GET"]),
    Route("/api/v1/notes/sent", api_get_sent_notes, methods=["GET"]),
    Route("/api/v1/notes/archived", api_get_archived_notes, methods=["GET"]),
    Route("/api/v1/notes/{note_id}/archive", api_archive_note, methods=["PATCH"]),
    Route("/api/v1/notes/{note_id}", api_get_note_detail, methods=["GET"]),
]

# Crear aplicación Starlette
app = Starlette(routes=routes)

# Agregar CORS para clientes browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, restringir
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id", "MCP-Protocol-Version"]
)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8005))

    logger.info("=" * 60)
    logger.info("GDI-Backend MCP Server (HTTP + REST API)")
    logger.info("=" * 60)
    logger.info(f"Transport: Streamable HTTP")
    logger.info(f"Protocol Version: {MCP_PROTOCOL_VERSION}")
    logger.info(f"Port: {port}")
    logger.info(f"")
    logger.info(f"Autenticación MCP (RFC 9728):")
    logger.info(f"  - OAuth 2.0: Claude Code/ChatGPT/Gemini hacen login automático via Auth0")
    logger.info(f"  - Authorization: Bearer <jwt>")
    logger.info(f"")
    logger.info(f"MCP Endpoints:")
    logger.info(f"  - GET  /health  (health check)")
    logger.info(f"  - POST /mcp     (JSON-RPC, OAuth/JWT)")
    logger.info(f"")
    logger.info(f"OAuth 2.0 (DCR nativo en Auth0):")
    logger.info(f"  - GET  /.well-known/oauth-protected-resource    (RFC 9728)")
    logger.info(f"  - GET  /.well-known/oauth-authorization-server  (RFC 8414 - para ChatGPT)")
    logger.info(f"  - GET  /.well-known/oauth-authorization-server/mcp")
    logger.info(f"")
    logger.info(f"OpenAPI Spec (para ChatGPT Actions):")
    logger.info(f"  - GET  /.well-known/openapi.json")
    logger.info(f"  - GET  /openapi.json")
    logger.info(f"")
    logger.info(f"REST API v1 (X-API-Key header):")
    logger.info(f"  - GET /api/v1/cases/search")
    logger.info(f"  - GET /api/v1/cases/{{case_id}}")
    logger.info(f"  - GET /api/v1/cases/{{case_id}}/history")
    logger.info(f"  - GET /api/v1/cases/{{case_id}}/documents")
    logger.info(f"  - GET /api/v1/cases/{{case_id}}/permissions")
    logger.info(f"  - GET /api/v1/documents/search")
    logger.info(f"  - GET /api/v1/documents/pending-signatures")
    logger.info(f"  - GET /api/v1/documents/{{document_id}}")
    logger.info(f"  - GET /api/v1/documents/{{document_id}}/content")
    logger.info(f"  - GET /api/v1/documents/{{document_id}}/url")
    logger.info(f"  - GET /api/v1/system/document-types")
    logger.info(f"  - GET /api/v1/system/sectors")
    logger.info(f"  - GET /api/v1/system/users/{{user_id}}")
    logger.info(f"  - GET /api/v1/system/case-templates")
    logger.info("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=port)
