"""
MCP Server HTTP - Transport Streamable HTTP para Fly.io.

Este archivo expone el MCP Server via HTTP en lugar de stdio.
Usar para deployments remotos (Fly.io, etc).

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

from contextlib import asynccontextmanager
import time as _time

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.middleware.cors import CORSMiddleware

from api_gateway.rate_limiter import rate_limiter, get_client_ip, RateLimitExceeded
from api_gateway.gateway_audit import log_mcp_tool_call
from api_gateway.gateway_middleware import GatewayMiddleware

# Imports MCP
from api_gateway.auth_mcp import (
    validate_mcp_jwt,
    verify_mcp_token,
    extract_email_from_token,
    find_user_all_tenants,
    MultiTenantSelectionRequired
)
from api_gateway.context import create_context, MCPContext
from api_gateway.tools import cases, documents, system, notes, records, memos, search
from shared.exceptions import ValidationError, GDIBaseException

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
    # Memos
    api_get_memos,
    api_get_sent_memos,
    api_get_archived_memos,
    api_get_memo_detail,
    # Backup Sync
    api_sync_schema,
    api_sync_data,
    api_sync_documents,
    # Busqueda semantica
    api_semantic_search,
    # Legajos (RLM)
    api_search_records,
    api_get_record,
    api_create_record,
    api_get_registry_families,
    # RLM - Endpoints adicionales (Fase 2.6)
    api_update_record,
    api_update_record_field,
    api_verify_record_field,
    api_get_record_history,
    api_generate_record_report,
    api_get_record_relations,
    api_create_record_relation,
    api_delete_record_relation,
    api_get_record_cases,
    api_link_record_case,
    api_unlink_record_case,
    api_get_record_documents,
    api_link_record_document,
    api_unlink_record_document,
    # Responsables de expediente
    api_get_case_responsibles,
    api_add_case_responsible,
    api_remove_case_responsible,
    # Subsanacion de expediente (S8-011)
    api_subsanar_document,
    # Movimientos de expediente (S8-014)
    api_get_case_movements,
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

# Rate limit tiers
MCP_IP_LIMIT = 60         # requests/min per IP
MCP_USER_LIMIT = 30       # tool calls/min per user


@asynccontextmanager
async def lifespan(app):
    """Lifespan: init asyncpg pool + cleanup rate limiter every 5 min."""
    from database import init_pool, close_pool

    await init_pool()
    logger.info("Gateway: asyncpg pool inicializado")

    async def _cleanup_loop():
        while True:
            await asyncio.sleep(300)
            rate_limiter.cleanup()

    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()
    await close_pool()
    logger.info("Gateway: asyncpg pool cerrado")

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
        # ===== NUEVOS TOOLS DE OPERACIONES =====
        # ===== DOCUMENTOS (Escritura) =====
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
                    "movement_id": {"type": "string"},
                    "case_number": {"type": "string"},
                    "action_type": {"type": "string", "description": "asignado"},
                    "target_sector": {"type": "string"},
                    "target_department": {"type": "string"},
                    "official_document": {"type": "object", "description": "Info del PV generado (si create_official_doc=true)"}
                }
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
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string", "description": "Mensaje de confirmación"}
                }
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
        # ===== MEMOS =====
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
        # ===== LEGAJOS (RLM) =====
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
        # ===== BUSQUEDA SEMANTICA =====
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

        # ── S8-013: Responsables de expediente ────────────────────────────────
        {
            "name": "get_case_responsibles",
            "description": """Lista los responsables activos de un expediente.

Devuelve el responsable ADMIN (titular) y los responsables ADDITIONAL asignados.

USAR PARA:
- "¿Quién es responsable del expediente X?"
- "¿Qué usuarios están asignados al expediente Y?"
- Verificar antes de agregar un nuevo responsable

RESPUESTA: { admin: {...}, additional: [...] }""",
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
- ADMIN: responsable principal (reemplaza al actual)
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
    email = await extract_email_from_token(token)

    if not email:
        return create_jsonrpc_error(request_id, -32001, "No se pudo obtener email del token")

    tenants = await find_user_all_tenants(email)

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


async def handle_call_tool(request_id: Any, params: Dict, authorization_header: str = None, correlation_id: str = None) -> Dict:
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
                "version": "3.0",
                "tools_count": 31,
                "last_updated": "2026-03-03"
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
                ctx, jwt_user_id = await validate_mcp_jwt(authorization_header, tenant_id=tenant_id)
                logger.info(f"[Auth0] Autenticación exitosa: user_id={jwt_user_id[:8]}..., schema={ctx.schema_name}")
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
                                    # SEC-31: "schema" removido — el schema_name es un detalle
                                    # interno de BD que no debe exponerse al cliente MCP.
                                    # El tenant_id (UUID) es el identificador público.
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
                logger.warning(f"[Auth0] Falló autenticación: {e}")
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
            ctx.user_id = jwt_user_id  # Inyectar en contexto (usado por tools RLM)
            logger.info(f"[Auth0] user_id inyectado desde JWT: {jwt_user_id[:8]}...")

            # MIT-1: Validar que el usuario existe y está activo en el tenant
            try:
                from shared.utils import get_authenticated_user
                await get_authenticated_user(jwt_user_id, schema_name=ctx.schema_name)
            except ValidationError as e:
                logger.warning(f"[Auth0] user_id {jwt_user_id[:8]}... inválido o inactivo: {e}")
                return create_jsonrpc_error(
                    request_id,
                    -32001,
                    "Usuario inválido o inactivo."
                )
            except Exception as e:
                logger.error(f"[Auth0] Error de infraestructura validando usuario {jwt_user_id[:8]}...: {e}")
                return create_jsonrpc_error(
                    request_id,
                    -32603,
                    "Error interno validando usuario. Intente nuevamente."
                )
        else:
            logger.warning("[Auth0] JWT valido pero sin user_id extraible")
            return create_jsonrpc_error(request_id, -32001, "Token sin user_id válido.")

        # Rate limit per-user para MCP tool calls
        try:
            rate_limiter.check(f"mcp_user:{jwt_user_id}:{ctx.schema_name}", MCP_USER_LIMIT)
        except RateLimitExceeded as e:
            log_mcp_tool_call(
                cid=correlation_id or "", user_id=jwt_user_id, schema=ctx.schema_name,
                tool=tool_name, status="rate_limited", duration_ms=0,
            )
            return create_jsonrpc_error(request_id, -32029, f"Rate limit exceeded. Retry after {e.retry_after}s")

        # 3. EJECUTAR TOOL
        _tool_start = _time.time()
        result = None

        if tool_name == "search_cases":
            result = await cases.search_cases(
                ctx=ctx,
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search"),
                status=arguments.get("status"),
                date_filter=arguments.get("date_filter"),
                sector_filter=arguments.get("sector_filter"),
                user_id=arguments["user_id"]
            )

        elif tool_name == "get_case":
            result = await cases.get_case(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"],
                include_documents=arguments.get("include_documents", False),
                include_movements=arguments.get("include_movements", False),
            )

        elif tool_name == "get_case_history":
            result = await cases.get_case_history(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"]
            )

        elif tool_name == "get_case_documents":
            result = await cases.get_case_documents(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"]
            )

        elif tool_name == "get_case_permissions":
            result = await cases.get_case_permissions(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"]
            )

        elif tool_name == "search_documents":
            _min_signers_raw = arguments.get("min_signers")
            result = await documents.search_documents(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search"),
                status=arguments.get("status"),
                document_type=arguments.get("document_type"),
                case_id=arguments.get("case_id"),
                min_signers=int(_min_signers_raw) if _min_signers_raw is not None else None,
            )

        elif tool_name == "get_document":
            result = await documents.get_document(
                ctx=ctx,
                document_id=arguments["document_id"],
                user_id=arguments["user_id"]
            )

        elif tool_name == "get_document_types":
            result = await system.get_document_types(ctx=ctx)


        elif tool_name == "get_user_info":
            result = await system.get_user_info(
                ctx=ctx,
                user_id=arguments["user_id"]
            )


        elif tool_name == "get_pending_signatures":
            result = await documents.get_pending_signatures(
                ctx=ctx,
                user_id=arguments["user_id"]
            )

        elif tool_name == "get_document_content":
            result = await documents.get_document_content(
                ctx=ctx,
                document_id=arguments["document_id"],
                user_id=arguments["user_id"]
            )

        # ===== NUEVOS TOOLS DE OPERACIONES =====

        # Documentos (Escritura)
        elif tool_name == "create_document":
            result = await documents.create_document(
                ctx=ctx,
                document_type_acronym=arguments["document_type_acronym"],
                reference=arguments["reference"],
                user_id=arguments["user_id"],
                case_id=arguments.get("case_id"),
                recipients=arguments.get("recipients")
            )

        elif tool_name == "save_document":
            result = await documents.save_document(
                ctx=ctx,
                document_id=arguments["document_id"],
                user_id=arguments["user_id"],
                content=arguments.get("content"),
                reference=arguments.get("reference"),
                signers=arguments.get("signers"),
                recipients=arguments.get("recipients"),
                proposed_case_ids=arguments.get("proposed_case_ids")
            )

        elif tool_name == "start_signing":
            result = await documents.start_signing(
                ctx=ctx,
                document_id=arguments["document_id"],
                user_id=arguments["user_id"]
            )

        # Expedientes (Operaciones)
        elif tool_name == "get_case_by_number":
            result = await cases.get_case_by_number(
                ctx=ctx,
                case_number=arguments["case_number"],
                user_id=arguments["user_id"]
            )


        elif tool_name == "prepare_assignment":
            result = await cases.prepare_assignment(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"]
            )

        elif tool_name == "assign_case":
            result = await cases.assign_case(
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
            result = await system.get_document_states(ctx=ctx)

        # ===== NUEVOS TOOLS - FASE 2 =====

        # Documentos (Firma/Rechazo/Eliminacion)
        elif tool_name == "reject_document":
            result = await documents.reject_document(
                ctx=ctx,
                document_id=arguments["document_id"],
                user_id=arguments["user_id"],
                reason=arguments["reason"]
            )

        # Expedientes (Operaciones nuevas)
        elif tool_name == "propose_document":
            result = await cases.propose_document(
                ctx=ctx,
                case_id=arguments["case_id"],
                document_draft_id=arguments["document_draft_id"],
                user_id=arguments["user_id"]
            )

        elif tool_name == "reject_proposal":
            result = await cases.reject_proposal(
                ctx=ctx,
                case_id=arguments["case_id"],
                proposed_id=arguments["proposed_id"],
                user_id=arguments["user_id"]
            )

        # Sistema (Catalogos nuevos)
        elif tool_name == "get_case_templates":
            result = await system.get_case_templates(
                ctx=ctx,
                user_id=arguments["user_id"]
            )

        elif tool_name == "search_users":
            result = await system.search_users(
                ctx=ctx,
                search=arguments["search"],
                limit=int(arguments.get("limit", 10))
            )

        # Notas
        elif tool_name == "get_notes":
            result = await notes.get_notes(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                unread_only=arguments.get("unread_only", False),
                search=arguments.get("search")
            )

        # ===== NUEVOS TOOLS - FASE 3 =====

        elif tool_name == "search_document_by_number":
            result = await documents.search_document_by_number(
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
            result = await notes.get_sent_notes(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search")
            )

        elif tool_name == "get_archived_notes":
            result = await notes.get_archived_notes(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search")
            )

        elif tool_name == "get_note_detail":
            result = await notes.get_note_detail(
                ctx=ctx,
                note_id=arguments.get("note_id", ""),
                user_id=arguments["user_id"]
            )

        # ===== MEMOS =====
        elif tool_name == "get_memos":
            result = await memos.get_memos(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search")
            )

        elif tool_name == "get_sent_memos":
            result = await memos.get_sent_memos_tool(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search")
            )

        elif tool_name == "get_archived_memos":
            result = await memos.get_archived_memos_tool(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search")
            )

        elif tool_name == "get_memo_detail":
            result = await memos.get_memo_detail(
                ctx=ctx,
                memo_id=arguments.get("memo_id", ""),
                user_id=arguments["user_id"]
            )

        # ===== LEGAJOS (RLM) =====
        elif tool_name == "search_records":
            result = await records.search_records(
                ctx=ctx,
                family_code=arguments.get("family_code"),
                search=arguments.get("search"),
                state=arguments.get("state"),
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
            )

        elif tool_name == "get_record":
            result = await records.get_record_detail(
                ctx=ctx,
                record_id=arguments.get("record_id", ""),
            )

        elif tool_name == "get_registry_families":
            result = await records.get_registry_families(ctx=ctx)

        elif tool_name == "semantic_search":
            result = await search.semantic_search_tool(
                ctx=ctx,
                query=arguments["query"],
                limit=int(arguments.get("limit", 20)),
                source="mcp",
            )

        # ── S8-013: Responsables ──────────────────────────────────────────────
        elif tool_name == "get_case_responsibles":
            result = await cases.get_case_responsibles_list(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"],
            )

        elif tool_name == "add_case_responsible":
            result = await cases.add_case_responsible(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"],
                responsible_user_id=arguments["responsible_user_id"],
                responsible_type=arguments["responsible_type"],
                sector_id=arguments["sector_id"],
                reason=arguments.get("reason", "Asignación de responsable"),
            )

        elif tool_name == "remove_case_responsible":
            result = await cases.remove_case_responsible(
                ctx=ctx,
                case_id=arguments["case_id"],
                responsible_id=arguments["responsible_id"],
                user_id=arguments["user_id"],
                reason=arguments.get("reason", "Remoción de responsable"),
            )

        else:
            _tool_ms = int((_time.time() - _tool_start) * 1000)
            log_mcp_tool_call(cid=correlation_id or "", user_id=jwt_user_id, schema=ctx.schema_name,
                              tool=tool_name, status="unknown_tool", duration_ms=_tool_ms)
            return create_jsonrpc_error(request_id, -32601, f"Tool desconocido: {tool_name}")

        # 4. RETORNAR RESULTADO
        _tool_ms = int((_time.time() - _tool_start) * 1000)
        log_mcp_tool_call(cid=correlation_id or "", user_id=jwt_user_id, schema=ctx.schema_name,
                          tool=tool_name, status="ok", duration_ms=_tool_ms)
        return create_jsonrpc_response(request_id, {
            "content": [{
                "type": "text",
                "text": json.dumps(result, indent=2, default=str)
            }],
            "isError": False
        })

    except ValueError as e:
        _tool_ms = int((_time.time() - _tool_start) * 1000) if '_tool_start' in locals() else 0
        log_mcp_tool_call(cid=correlation_id or "", user_id=locals().get('jwt_user_id'),
                          schema=ctx.schema_name if 'ctx' in dir() and ctx else None,
                          tool=tool_name, status="validation_error", duration_ms=_tool_ms, error=str(e))
        logger.error(f"Error de validación en tool {tool_name}: {e}")
        return create_jsonrpc_response(request_id, {
            "content": [{"type": "text", "text": f"Error de validación: {str(e)}"}],
            "isError": True
        })
    except GDIBaseException as e:
        _tool_ms = int((_time.time() - _tool_start) * 1000) if '_tool_start' in locals() else 0
        log_mcp_tool_call(cid=correlation_id or "", user_id=locals().get('jwt_user_id'),
                          schema=ctx.schema_name if 'ctx' in dir() and ctx else None,
                          tool=tool_name, status="business_error", duration_ms=_tool_ms, error=str(e))
        logger.error(f"Error de negocio en tool {tool_name}: {e}")
        error_data = {
            "error_type": type(e).__name__,
            "message": e.message,
        }
        if e.details:
            error_data["details"] = e.details
        if hasattr(e, "current_state") and e.current_state:
            error_data["current_state"] = e.current_state
        if hasattr(e, "required_state") and e.required_state:
            error_data["required_state"] = e.required_state
        if hasattr(e, "document_id"):
            error_data["document_id"] = e.document_id
        return create_jsonrpc_response(request_id, {
            "content": [{"type": "text", "text": json.dumps(error_data, ensure_ascii=False)}],
            "isError": True
        })
    except Exception as e:
        _tool_ms = int((_time.time() - _tool_start) * 1000) if '_tool_start' in locals() else 0
        log_mcp_tool_call(cid=correlation_id or "", user_id=locals().get('jwt_user_id'),
                          schema=ctx.schema_name if 'ctx' in dir() and ctx else None,
                          tool=tool_name, status="error", duration_ms=_tool_ms, error=str(e))
        logger.exception(f"Error ejecutando tool {tool_name}")
        return create_jsonrpc_response(request_id, {
            "content": [{"type": "text", "text": f"Error interno: {str(e)}"}],
            "isError": True
        })


async def process_jsonrpc_request(body: Dict, authorization_header: str = None, correlation_id: str = None) -> Dict:
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
        return await handle_call_tool(request_id, params, authorization_header, correlation_id)

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
    auth0_domain = os.getenv("AUTH0_DOMAIN", "")

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
                        {"name": "date_filter", "in": "query", "schema": {"type": "string", "enum": ["hoy", "ayer", "ultimos_7_dias", "ultimos_30_dias"]}, "description": "Filtro temporal"},
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
                    "description": "Crea un nuevo documento en estado borrador. Soporta todos los tipos: INF, DICT, NOTA, MEMO, etc. Para NOTA/MEMO incluir recipients con to/cc/bcc.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["document_type_acronym", "reference"],
                                    "properties": {
                                        "document_type_acronym": {"type": "string", "description": "Acrónimo del tipo (INF, DICT, NOTA, MEMO, etc.)"},
                                        "reference": {"type": "string", "description": "Descripción del documento"},
                                        "case_id": {"type": "string", "description": "UUID del expediente a vincular (opcional)"},
                                        "recipients": {
                                            "type": "object",
                                            "description": "Destinatarios para NOTA (sector UUIDs) o MEMO (user UUIDs).",
                                            "properties": {
                                                "to":  {"type": "array", "items": {"type": "string", "format": "uuid"}, "description": "Destinatarios principales"},
                                                "cc":  {"type": "array", "items": {"type": "string", "format": "uuid"}, "description": "Copia"},
                                                "bcc": {"type": "array", "items": {"type": "string", "format": "uuid"}, "description": "Copia oculta"}
                                            }
                                        }
                                    }
                                },
                                "examples": {
                                    "informe": {
                                        "summary": "Crear informe simple",
                                        "value": {
                                            "document_type_acronym": "INF",
                                            "reference": "Informe de actividades mensuales"
                                        }
                                    },
                                    "nota": {
                                        "summary": "Crear NOTA entre sectores",
                                        "value": {
                                            "document_type_acronym": "NOTA",
                                            "reference": "Solicitud de insumos de oficina",
                                            "recipients": {
                                                "to": ["<sector-uuid>"],
                                                "cc": [],
                                                "bcc": []
                                            }
                                        }
                                    },
                                    "memo": {
                                        "summary": "Crear MEMO entre personas",
                                        "value": {
                                            "document_type_acronym": "MEMO",
                                            "reference": "Convocatoria reunión de equipo",
                                            "recipients": {
                                                "to": ["<user-uuid>"],
                                                "cc": [],
                                                "bcc": []
                                            }
                                        }
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
            },
            "/api/v1/sync/schema": {
                "get": {
                    "operationId": "syncSchema",
                    "summary": "Catálogo de tablas sincronizables (Backup)",
                    "description": "Requiere X-API-Key backup. Sin rate limit. Informativo.",
                    "parameters": [],
                    "responses": {
                        "200": {"description": "Catálogo de tablas con counts"},
                        "401": {"description": "Acceso denegado"},
                        "403": {"description": "Origen no autorizado"}
                    },
                    "security": [{"ApiKeyAuth": []}]
                }
            },
            "/api/v1/sync/data": {
                "get": {
                    "operationId": "syncData",
                    "summary": "Datos incrementales de una tabla (Backup)",
                    "description": "Requiere X-API-Key backup. Rate limit: usa rate_limit_per_minute de la key. since siempre obligatorio.",
                    "parameters": [
                        {"name": "table", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "since", "in": "query", "required": True, "schema": {"type": "string", "format": "date-time"}},
                        {"name": "page", "in": "query", "required": False, "schema": {"type": "integer", "default": 1}},
                        {"name": "page_size", "in": "query", "required": False, "schema": {"type": "integer", "default": 100, "maximum": 100}}
                    ],
                    "responses": {
                        "200": {"description": "Filas de la tabla"},
                        "400": {"description": "Tabla no válida o parámetros faltantes"},
                        "401": {"description": "Acceso denegado"},
                        "403": {"description": "Origen no autorizado"},
                        "429": {"description": "Rate limit (Retry-After header)"}
                    },
                    "security": [{"ApiKeyAuth": []}]
                }
            },
            "/api/v1/sync/documents": {
                "get": {
                    "operationId": "syncDocuments",
                    "summary": "PDFs de documentos oficiales firmados con presigned URLs (Backup)",
                    "description": "Requiere X-API-Key backup. Devuelve presigned URLs de R2 (TTL 600s). Solo documentos firmados (signed_at IS NOT NULL). since siempre obligatorio.",
                    "parameters": [
                        {"name": "since", "in": "query", "required": True, "schema": {"type": "string", "format": "date-time"}},
                        {"name": "page", "in": "query", "required": False, "schema": {"type": "integer", "default": 1}},
                        {"name": "page_size", "in": "query", "required": False, "schema": {"type": "integer", "default": 100, "maximum": 100}}
                    ],
                    "responses": {
                        "200": {"description": "Lista de documentos con presigned URLs"},
                        "400": {"description": "Parámetro since faltante o inválido"},
                        "401": {"description": "Acceso denegado"},
                        "403": {"description": "Origen no autorizado"},
                        "429": {"description": "Rate limit (Retry-After header)"}
                    },
                    "security": [{"ApiKeyAuth": []}]
                }
            },
            # ===== RLM (Registro Legajo Multiproposito) =====
            "/api/v1/registries": {
                "get": {
                    "operationId": "getRegistryFamilies",
                    "summary": "Listar familias de registro (tipos de legajo)",
                    "description": "Devuelve las familias de legajo configuradas en el tenant (ARQ, LUM, ORD, etc.) con sus data_schema, estados y permisos.",
                    "responses": {
                        "200": {"description": "Lista de familias de registro"},
                        "401": {"description": "No autorizado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/search": {
                "get": {
                    "operationId": "searchRecords",
                    "summary": "Buscar legajos",
                    "description": "Busca legajos por numero, display_name, campos data (JSONB), estado, familia. Soporta paginacion.",
                    "parameters": [
                        {"name": "search", "in": "query", "schema": {"type": "string"}, "description": "Texto a buscar"},
                        {"name": "registry_family_id", "in": "query", "schema": {"type": "string"}, "description": "UUID de familia de registro"},
                        {"name": "state", "in": "query", "schema": {"type": "string"}, "description": "Estado del legajo"},
                        {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
                        {"name": "page_size", "in": "query", "schema": {"type": "integer", "default": 20}}
                    ],
                    "responses": {
                        "200": {"description": "Lista de legajos"},
                        "401": {"description": "No autorizado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records": {
                "post": {
                    "operationId": "createRecord",
                    "summary": "Crear legajo",
                    "description": "Crea un nuevo legajo en una familia. Requiere display_name y registry_family_id. Dispara generacion asincrona de resumen IA.",
                    "responses": {
                        "201": {"description": "Legajo creado"},
                        "400": {"description": "Datos invalidos"},
                        "401": {"description": "No autorizado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}": {
                "get": {
                    "operationId": "getRecord",
                    "summary": "Detalle de legajo",
                    "description": "Obtiene datos completos del legajo incluyendo data (JSONB), historial basico, resumen IA.",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {"description": "Detalle del legajo"},
                        "404": {"description": "Legajo no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                },
                "patch": {
                    "operationId": "updateRecord",
                    "summary": "Actualizar legajo",
                    "description": "Actualiza display_name, state, next_expiration o data completo del legajo.",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {"description": "Legajo actualizado"},
                        "404": {"description": "Legajo no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}/fields/{field_name}": {
                "patch": {
                    "operationId": "updateRecordField",
                    "summary": "Actualizar un campo individual del legajo",
                    "description": "Actualiza un solo campo dentro del data JSONB del legajo.",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "field_name", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {"description": "Campo actualizado"},
                        "404": {"description": "Legajo o campo no encontrado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}/fields/{field_name}/verify": {
                "post": {
                    "operationId": "verifyRecordField",
                    "summary": "Marcar campo como verificado",
                    "description": "Marca un campo del legajo como verificado con timestamp y usuario.",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "field_name", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {"description": "Campo verificado"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}/history": {
                "get": {
                    "operationId": "getRecordHistory",
                    "summary": "Historial del legajo",
                    "description": "Devuelve el historial de cambios del legajo enriquecido con contexto de documentos y expedientes vinculados.",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {"description": "Historial del legajo"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}/report": {
                "post": {
                    "operationId": "generateRecordReport",
                    "summary": "Generar informe IFRLM del legajo",
                    "description": "Genera on-demand el Informe de Registro Legajo Multiproposito (IFRLM): PDF via PDFComposer, firma con Notary, sube a R2 y lo registra como documento oficial.",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "201": {"description": "IFRLM generado y firmado"},
                        "500": {"description": "Error en pipeline externo (PDFComposer/Notary/R2)"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}/relations": {
                "get": {
                    "operationId": "getRecordRelations",
                    "summary": "Relaciones del legajo",
                    "description": "Lista relaciones del legajo con otros legajos (parent, child, related, replaces, sibling, cousin).",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {"description": "Lista de relaciones"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                },
                "post": {
                    "operationId": "createRecordRelation",
                    "summary": "Crear relacion entre legajos",
                    "description": "Vincula dos legajos con un tipo de relacion.",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "201": {"description": "Relacion creada"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}/relations/{relation_id}": {
                "delete": {
                    "operationId": "deleteRecordRelation",
                    "summary": "Eliminar relacion entre legajos",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "relation_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "204": {"description": "Relacion eliminada"}
                    },
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}/cases": {
                "get": {
                    "operationId": "getRecordCases",
                    "summary": "Expedientes vinculados al legajo",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "Lista de expedientes vinculados"}},
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                },
                "post": {
                    "operationId": "linkRecordCase",
                    "summary": "Vincular expediente al legajo",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"201": {"description": "Expediente vinculado"}},
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}/cases/{link_id}": {
                "delete": {
                    "operationId": "unlinkRecordCase",
                    "summary": "Desvincular expediente del legajo",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "link_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"204": {"description": "Expediente desvinculado"}},
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}/documents": {
                "get": {
                    "operationId": "getRecordDocuments",
                    "summary": "Documentos vinculados al legajo",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "Lista de documentos vinculados"}},
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                },
                "post": {
                    "operationId": "linkRecordDocument",
                    "summary": "Vincular documento al legajo",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"201": {"description": "Documento vinculado"}},
                    "security": [{"OAuth2": ["openid", "profile", "email"]}, {"ApiKeyAuth": []}]
                }
            },
            "/api/v1/records/{record_id}/documents/{link_id}": {
                "delete": {
                    "operationId": "unlinkRecordDocument",
                    "summary": "Desvincular documento del legajo",
                    "parameters": [
                        {"name": "record_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "link_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"204": {"description": "Documento desvinculado"}},
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
    """Root endpoint para health checks de Fly.io y browsers."""
    return JSONResponse({
        "service": "gdi-mcp-server",
        "status": "ok",
        "transport": "streamable-http",
        "mcp_endpoint": "/mcp",
        "health": "/health",
        "docs": "/.well-known/mcp.json"
    })


async def health(request: Request) -> JSONResponse:
    """Health check para Fly.io."""
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

    Claude Code y otros clientes MCP usan este endpoint para descubrir:
    1. El Authorization Server (Auth0)
    2. El audience que deben incluir al solicitar el token

    El campo "resource" es el audience que el cliente DEBE enviar a Auth0
    al solicitar el token (audience param en /authorize). Auth0 emite entonces
    un JWT real con ese audience, que luego verify_mcp_token puede validar.

    Si MCP_RESOURCE_URI no está configurado en Fly, el endpoint retorna
    un resource vacío que causará que los tokens sean rechazados — esto es
    intencional para forzar la configuración correcta en producción.
    """
    auth0_domain = os.getenv("AUTH0_DOMAIN", "")
    resource_uri = os.getenv("MCP_RESOURCE_URI", "")

    if not resource_uri:
        logger.warning(
            "[OAuth] MCP_RESOURCE_URI no configurado. "
            "Los clientes MCP no podrán obtener un token con audience correcto. "
            "Setear MCP_RESOURCE_URI en los secrets de Fly.io."
        )

    return JSONResponse({
        # "resource" es el audience que el cliente debe solicitar a Auth0.
        # Claude Code y ChatGPT leen este campo (RFC 9728 §2) y lo incluyen
        # como audience param en el flujo OAuth, garantizando que Auth0 emita
        # un JWT con ese audience que el servidor puede validar.
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

    auth0_domain = os.getenv("AUTH0_DOMAIN", "")
    auth0_metadata_url = f"https://{auth0_domain}/.well-known/openid-configuration"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(auth0_metadata_url, timeout=10.0)
            response.raise_for_status()
            auth0_metadata = response.json()

            # Inyectar PKCE si Auth0 no lo incluye (ChatGPT lo requiere)
            if "code_challenge_methods_supported" not in auth0_metadata:
                auth0_metadata["code_challenge_methods_supported"] = ["S256", "plain"]

            # Filtrar scopes a solo los que soportamos (ChatGPT pide todos los listados)
            auth0_metadata["scopes_supported"] = ["openid", "profile", "email", "offline_access"]

            # Asegurar registration_endpoint para DCR
            if "registration_endpoint" not in auth0_metadata:
                auth0_metadata["registration_endpoint"] = f"https://{auth0_domain}/oidc/register"

            logger.info(f"[OAuth] Proxeando metadata de Auth0 (enriched): {auth0_metadata_url}")
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
        # Rate limit per-IP para MCP
        client_ip = get_client_ip(request)
        try:
            rate_limiter.check(f"mcp_ip:{client_ip}", MCP_IP_LIMIT)
        except RateLimitExceeded as e:
            return JSONResponse(
                create_jsonrpc_error(None, -32029, f"Rate limit exceeded. Retry after {e.retry_after}s"),
                status_code=429,
                headers={"Retry-After": str(e.retry_after)}
            )

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

        # Procesar request (pasar Authorization y correlation_id para tools/call)
        cid = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
        response = await process_jsonrpc_request(body, authorization_header, correlation_id=cid)

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
    Route("/.well-known/oauth-protected-resource/mcp", oauth_protected_resource_metadata, methods=["GET"]),
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

    # Cases (Expedientes - Responsables)
    Route("/api/v1/cases/{case_id}/responsibles", api_get_case_responsibles, methods=["GET"]),
    Route("/api/v1/cases/{case_id}/responsibles", api_add_case_responsible, methods=["POST"]),
    Route("/api/v1/cases/{case_id}/responsibles/{responsible_id}", api_remove_case_responsible, methods=["DELETE"]),

    # Cases (Expedientes - Subsanacion S8-011)
    Route("/api/v1/cases/{case_id}/subsanar", api_subsanar_document, methods=["POST"]),

    # Cases (Expedientes - Movimientos S8-014)
    Route("/api/v1/cases/{case_id}/movements", api_get_case_movements, methods=["GET"]),

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

    # === Memos ===
    Route("/api/v1/memos/received", api_get_memos, methods=["GET"]),
    Route("/api/v1/memos/sent", api_get_sent_memos, methods=["GET"]),
    Route("/api/v1/memos/archived", api_get_archived_memos, methods=["GET"]),
    Route("/api/v1/memos/{memo_id}", api_get_memo_detail, methods=["GET"]),

    # === Backup Sync (X-API-Key con key_type='backup', sin X-User-ID) ===
    Route("/api/v1/sync/schema", api_sync_schema, methods=["GET"]),
    Route("/api/v1/sync/data", api_sync_data, methods=["GET"]),
    Route("/api/v1/sync/documents", api_sync_documents, methods=["GET"]),
    # Busqueda semantica
    Route("/api/v1/search/semantic", api_semantic_search, methods=["GET"]),
    # Legajos (RLM)
    # IMPORTANTE: rutas literales van ANTES que los patrones {record_id} para que
    # Starlette las resuelva correctamente y no caigan en el handler de detalle.
    Route("/api/v1/records/search", api_search_records, methods=["GET"]),
    Route("/api/v1/records/families", api_get_registry_families, methods=["GET"]),
    Route("/api/v1/records/{record_id}/fields/{field_name}/verify", api_verify_record_field, methods=["POST"]),
    Route("/api/v1/records/{record_id}/fields/{field_name}", api_update_record_field, methods=["PATCH"]),
    Route("/api/v1/records/{record_id}/history", api_get_record_history, methods=["GET"]),
    Route("/api/v1/records/{record_id}/report", api_generate_record_report, methods=["POST"]),
    Route("/api/v1/records/{record_id}/relations/{relation_id}", api_delete_record_relation, methods=["DELETE"]),
    Route("/api/v1/records/{record_id}/relations", api_get_record_relations, methods=["GET"]),
    Route("/api/v1/records/{record_id}/relations", api_create_record_relation, methods=["POST"]),
    Route("/api/v1/records/{record_id}/cases/{link_id}", api_unlink_record_case, methods=["DELETE"]),
    Route("/api/v1/records/{record_id}/cases", api_get_record_cases, methods=["GET"]),
    Route("/api/v1/records/{record_id}/cases", api_link_record_case, methods=["POST"]),
    Route("/api/v1/records/{record_id}/documents/{link_id}", api_unlink_record_document, methods=["DELETE"]),
    Route("/api/v1/records/{record_id}/documents", api_get_record_documents, methods=["GET"]),
    Route("/api/v1/records/{record_id}/documents", api_link_record_document, methods=["POST"]),
    Route("/api/v1/records/{record_id}", api_get_record, methods=["GET"]),
    Route("/api/v1/records/{record_id}", api_update_record, methods=["PATCH"]),
    Route("/api/v1/records", api_create_record, methods=["POST"]),
    Route("/api/v1/registries", api_get_registry_families, methods=["GET"]),
]

# Construir lista de origenes CORS permitidos
_allowed_origins = (
    [f"http://localhost:{port}" for port in range(3000, 3051)] +
    [f"http://127.0.0.1:{port}" for port in range(3000, 3051)] +
    [f"http://localhost:{port}" for port in range(8000, 8051)] +
    [f"http://127.0.0.1:{port}" for port in range(8000, 8051)]
)
_frontend_urls = os.getenv("FRONTEND_URL", "")
for _url in _frontend_urls.split(","):
    _url = _url.strip()
    if _url:
        _allowed_origins.append(_url)

# Crear aplicación Starlette con lifespan
app = Starlette(routes=routes, lifespan=lifespan)

# Agregar middlewares (orden: CORS primero, luego Gateway)
app.add_middleware(GatewayMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id", "MCP-Protocol-Version", "X-Correlation-ID"]
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
    logger.info(f"")
    logger.info(f"Backup Sync API (X-API-Key backup, sin X-User-ID):")
    logger.info(f"  - GET /api/v1/sync/schema")
    logger.info(f"  - GET /api/v1/sync/data")
    logger.info(f"  - GET /api/v1/sync/documents")
    logger.info("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=port)
