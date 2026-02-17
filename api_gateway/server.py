"""
MCP Server para GDI-Backend.
Transport: stdio (estándar MCP).
Auth: API Key simple (X-API-Key header).
Tools: Solo lectura (12 tools).
"""
import asyncio
import logging
from typing import Any, Dict, Optional

# MCP SDK imports (paquete externo)
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolResult,
    ErrorData,
)

# Imports locales (nuestro módulo api_gateway)
from api_gateway.auth import validate_api_key
from api_gateway.context import create_context, MCPContext
from api_gateway.tools import cases, documents, system

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Crear servidor MCP
app = Server("gdi-backend")


# ============================================================================
# HELPERS
# ============================================================================

def _extract_api_key(arguments: Dict[str, Any]) -> Optional[str]:
    """Extrae API Key de los argumentos del tool."""
    return arguments.get("api_key")


def _extract_municipality_id(arguments: Dict[str, Any]) -> Optional[str]:
    """Extrae municipality_id de los argumentos del tool."""
    return arguments.get("municipality_id")


def _create_error_result(error_msg: str, code: str = "INTERNAL_ERROR") -> CallToolResult:
    """Crea un resultado de error estándar."""
    logger.error(f"Error en tool: {error_msg}")
    return CallToolResult(
        content=[TextContent(type="text", text=f"Error: {error_msg}")],
        isError=True
    )


def _create_success_result(data: Any) -> CallToolResult:
    """Crea un resultado exitoso con datos JSON."""
    import json
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data, indent=2, default=str))],
        isError=False
    )


# ============================================================================
# TOOL DEFINITIONS
# ============================================================================

@app.list_tools()
async def list_tools() -> list[Tool]:
    """
    Lista todas las herramientas disponibles en el MCP Server.
    """
    return [
        # ===== CASES (Expedientes) =====
        # Un expediente es un contenedor de documentos que sigue un flujo administrativo.
        # Tiene un número único (ej: EE-2026-000017-TXST-HAC), pertenece a un sector,
        # y puede ser transferido entre departamentos.
        Tool(
            name="search_cases",
            description="""Busca expedientes del usuario. Usa esta tool para responder:
- "¿Cuáles son mis expedientes?"
- "¿Qué expedientes tengo pendientes?"
- "Busca el expediente de la panadería"
- "¿Cuántos expedientes hay en Hacienda?"

BÚSQUEDA INTELIGENTE (parámetro 'search'):
- Case-insensitive: "PANADERIA" = "panadería" = "Panadería"
- Ignora acentos: "comercial" encuentra "habilitación comercial"
- Búsqueda parcial: "mitre" encuentra "Av. Mitre 123"
- Busca en EXPEDIENTE: case_number, reference (asunto)
- Busca en DOCUMENTOS VINCULADOS: official_number, reference, contenido HTML
- Ejemplo: buscar "mitre" encuentra expedientes cuyo asunto menciona "Mitre" O que tienen documentos que mencionan "Mitre" en su contenido.

RESPUESTA: Lista de expedientes con case_number, reference (asunto), tipo, sector actual.
access_reason indica por qué el usuario puede ver el expediente (OWNER=es dueño, ADMINSECTOR=su sector lo administra, ASSIGNED=está asignado a su sector).""",
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"},
                    "user_id": {"type": "string", "description": "UUID del usuario que consulta"},
                    "page": {"type": "integer", "description": "Página (default 1)", "default": 1},
                    "page_size": {"type": "integer", "description": "Resultados por página (default 20, max 100)", "default": 20},
                    "search": {"type": "string", "description": "Buscar por número de expediente (ej: EE-2026-000017) o por referencia/asunto"},
                    "status": {"type": "string", "description": "Estado: active (en trámite), inactive, archived (cerrado)", "enum": ["active", "inactive", "archived"]},
                    "date_filter": {"type": "string", "description": "Filtro temporal: today, week, month, year", "enum": ["today", "week", "month", "year"]},
                    "sector_filter": {"type": "string", "description": "Filtrar por sector (usar acronym del sector, ej: HAC, LEGAL)"}
                },
                "required": ["api_key", "municipality_id", "user_id"]
            }
        ),
        Tool(
            name="get_case",
            description="""Obtiene el detalle completo de UN expediente específico. Usa esta tool para responder:
- "Dame los detalles del expediente EE-2026-000017"
- "¿Quién administra este expediente?"
- "¿Qué documentos tiene el expediente X?"
- "¿En qué sector está el expediente?"

USA include_documents=true si necesitas ver los documentos vinculados (oficiales firmados y propuestos en borrador).

RESPUESTA: case_number, reference (asunto), template (tipo de expediente), admin_sector (sector que lo administra actualmente), assigned_sectors (sectores con acceso), y opcionalmente documents.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"},
                    "case_id": {"type": "string", "description": "UUID del expediente (obtenido de search_cases)"},
                    "user_id": {"type": "string", "description": "UUID del usuario (omitir para consultas sin validar permisos)"},
                    "include_documents": {"type": "boolean", "description": "true para incluir lista de documentos oficiales y propuestos", "default": False}
                },
                "required": ["api_key", "municipality_id", "case_id"]
            }
        ),
        Tool(
            name="get_case_history",
            description="""Obtiene el historial cronológico de movimientos de un expediente. Usa esta tool para responder:
- "¿Qué pasó con este expediente?"
- "¿Quién creó el expediente?"
- "¿Por qué sectores pasó este expediente?"
- "¿Cuándo fue transferido?"
- "Dame la trazabilidad del expediente"

RESPUESTA: Lista de movements ordenados cronológicamente. Cada movimiento tiene:
- user (quién hizo la acción, con nombre y sector)
- message (descripción legible de la acción)
- type (creation, transfer, assignment, archive, etc.)
- created_at (fecha/hora)""",
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"},
                    "case_id": {"type": "string", "description": "UUID del expediente"}
                },
                "required": ["api_key", "municipality_id", "case_id"]
            }
        ),
        Tool(
            name="get_case_documents",
            description="""Lista los documentos vinculados a un expediente, separados en oficiales (firmados) y propuestos (borradores). Usa esta tool para:
- "¿Qué documentos tiene el expediente X?"
- "¿Cuántos documentos firmados hay?"
- "¿Hay documentos pendientes de firma en este expediente?"

RESPUESTA:
- official: documentos ya firmados con número oficial (ej: INF-2026-00001234-TXST-LEGAL)
- proposed: borradores vinculados pendientes de firma
- total_official y total_proposed: conteos

NOTA: Para ver el contenido de un documento específico, usa get_document con el document_id.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"},
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "user_id": {"type": "string", "description": "UUID del usuario (para validar permisos de vista)"}
                },
                "required": ["api_key", "municipality_id", "case_id"]
            }
        ),
        Tool(
            name="get_case_permissions",
            description="""Consulta qué acciones puede realizar un usuario sobre un expediente específico. Usa esta tool para:
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
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"},
                    "case_id": {"type": "string", "description": "UUID del expediente"},
                    "user_id": {"type": "string", "description": "UUID del usuario a consultar permisos"}
                },
                "required": ["api_key", "municipality_id", "case_id", "user_id"]
            }
        ),

        # ===== DOCUMENTS (Documentos) =====
        # Un documento es un archivo con contenido (informe, dictamen, certificado, etc.)
        # Puede estar en borrador, en proceso de firma, firmado (oficial) o rechazado.
        # Los documentos firmados tienen número oficial (ej: INF-2026-00001234-TXST-LEGAL).
        Tool(
            name="search_documents",
            description="""Busca documentos del usuario. Usa esta tool para responder:
- "¿Cuáles son mis documentos?"
- "¿Qué documentos tengo pendientes de firma?"
- "Busca el informe INF-2026-00001234"
- "¿Cuántos dictámenes he creado?"
- "¿Qué documentos tiene el expediente X?" (usar case_id)
- "Busca documentos que mencionen 'presupuesto'"

BÚSQUEDA INTELIGENTE (parámetro 'search'):
- Case-insensitive: "INFORME" = "informe" = "Informe"
- Ignora acentos: "tecnico" encuentra "informe técnico"
- Búsqueda parcial: "presu" encuentra "presupuesto anual"
- Busca en: reference (asunto), official_number, contenido HTML del documento
- Ejemplo: buscar "licitación" encuentra documentos cuyo contenido menciona "licitación" aunque no esté en el título.
- Mínimo 2 caracteres para activar búsqueda.

ESTADOS (status):
- pending: borrador, no enviado a firma
- sent_to_sign: en proceso de firma (esperando firmantes)
- signed: firmado por todos, es documento oficial
- rejected: rechazado por algún firmante

RESPUESTA: Lista de documentos con id, reference (asunto), display_status, document_type, official_number (si firmado).""",
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"},
                    "user_id": {"type": "string", "description": "UUID del usuario que consulta"},
                    "page": {"type": "integer", "description": "Página (default 1)", "default": 1},
                    "page_size": {"type": "integer", "description": "Resultados por página (default 20, max 100)", "default": 20},
                    "search": {"type": "string", "description": "Buscar por número de documento (ej: INF-2026-00001234)"},
                    "status": {"type": "string", "description": "Filtrar por estado", "enum": ["pending", "sent_to_sign", "signed", "rejected"]},
                    "document_type": {"type": "string", "description": "Filtrar por tipo de documento (acronym, ej: INF, DICT, CAEX). Usa get_document_types para ver tipos disponibles."},
                    "case_id": {"type": "string", "description": "Filtrar documentos vinculados a un expediente específico"},
                    "min_signers": {"type": "integer", "description": "Filtrar documentos con mínimo N firmantes (ej: 2 para docs con 2+ firmas)"}
                },
                "required": ["api_key", "municipality_id", "user_id"]
            }
        ),
        Tool(
            name="get_document",
            description="""Obtiene el detalle completo de UN documento específico. Funciona con cualquier estado (borrador, en firma, firmado). Usa esta tool para:
- "Dame los detalles del documento INF-2026-00001234"
- "¿Quién debe firmar este documento?"
- "¿Cuál es el contenido del informe X?"
- "¿Por qué fue rechazado este documento?"
- "¿Este documento está vinculado a algún expediente?"
- "¿A qué expediente pertenece este documento?"

RESPUESTA:
- state_category: 'editing' (borrador) o 'signing' (en proceso/firmado)
- status: estado actual
- details: contenido específico según estado (firmantes, fechas, contenido HTML, etc.)
- linked_case: expediente vinculado (si existe) con case_number, case_reference, link_type (official/proposed)
- Para documentos firmados: incluye número oficial y URL del PDF

NOTA: Para documentos en proceso de firma, user_id es necesario para ver detalles de firmantes.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"},
                    "document_id": {"type": "string", "description": "UUID del documento (obtenido de search_documents o get_case_documents)"},
                    "user_id": {"type": "string", "description": "UUID del usuario (necesario para docs en proceso de firma)"}
                },
                "required": ["api_key", "municipality_id", "document_id"]
            }
        ),

        # ===== SYSTEM (Catálogos del sistema) =====
        # Estas tools devuelven catálogos de configuración del sistema.
        # Útiles para entender qué tipos de documentos y sectores existen.
        Tool(
            name="get_document_types",
            description="""Lista todos los tipos de documentos disponibles en el sistema. Usa esta tool para:
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
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"}
                },
                "required": ["api_key", "municipality_id"]
            }
        ),
        Tool(
            name="get_user_info",
            description="""Obtiene información del usuario actual. Usa esta tool para:
- "¿En qué sector estoy?"
- "¿Cuál es mi departamento?"
- "¿Qué roles tengo?"
- "¿A qué otros sectores tengo acceso?"

RESPUESTA:
- full_name, email: datos básicos del usuario
- sector: sector actual del usuario con department_name y department_acronym
- roles: lista de roles asignados (ej: admin, user, viewer)
- additional_sectors: otros sectores donde tiene permisos (can_view, can_edit)""",
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"},
                    "user_id": {"type": "string", "description": "UUID del usuario"}
                },
                "required": ["api_key", "municipality_id", "user_id"]
            }
        ),
        Tool(
            name="get_pending_signatures",
            description="""Lista documentos pendientes de firma donde ES EL TURNO del usuario. Usa esta tool para:
- "¿Qué documentos tengo para firmar?"
- "¿Tengo firmas pendientes?"
- "¿Cuántos documentos esperan mi firma?"

Solo muestra documentos donde el usuario es el PRÓXIMO en la cola de firma.
No muestra documentos donde otros deben firmar antes.

RESPUESTA: Lista con:
- document_id, reference: identificación del documento
- document_type: tipo (INF, DICT, etc.)
- signer_role: "signer" (firmante) o "numerator" (numerador)
- creator: quién creó el documento
- sent_to_sign_at: cuándo se envió a firma""",
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"},
                    "user_id": {"type": "string", "description": "UUID del usuario"}
                },
                "required": ["api_key", "municipality_id", "user_id"]
            }
        ),
        Tool(
            name="get_document_content",
            description="""Obtiene el contenido HTML COMPLETO de un documento oficial (firmado).

KEYWORDS DE DETECCIÓN - USA ESTA TOOL SI EL USUARIO DICE:
- "léeme", "lee el documento", "leer el documento"
- "contenido completo", "texto completo"
- "qué dice exactamente", "qué dice el documento"
- "mostrame el contenido", "dame el texto"

Ejemplos de uso:
- "Léeme el documento INF-2026-00001234"
- "Dame el contenido completo del informe"
- "¿Qué dice exactamente el dictamen?"
- "Mostrame el texto del documento X"

IMPORTANTE: Solo funciona con documentos OFICIALES (firmados).
No funciona con borradores ni documentos en proceso de firma.

FLUJO:
1. Si no tienes document_id → search_documents(search="número")
2. Luego → get_document_content(document_id)

RESPUESTA:
- document_id: UUID del documento
- official_number: número oficial (ej: INF-2026-00001234-TXST-LEGAL)
- reference: asunto/título del documento
- content.html: contenido HTML completo del documento
- document_type: tipo con name y acronym
- signed_at: fecha de firma""",
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {"type": "string", "description": "API Key de autenticación"},
                    "municipality_id": {"type": "string", "description": "UUID de la municipalidad"},
                    "document_id": {"type": "string", "description": "UUID del documento oficial"}
                },
                "required": ["api_key", "municipality_id", "document_id"]
            }
        ),
    ]


# ============================================================================
# TOOL CALL HANDLER
# ============================================================================

@app.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> CallToolResult:
    """
    Ejecuta una herramienta MCP.

    Flujo:
    1. Validar API Key
    2. Extraer municipality_id y crear contexto (con schema_name)
    3. Ejecutar tool correspondiente
    4. Retornar resultado o error
    """
    logger.info(f"Tool llamado: {name}")

    try:
        # 1. VALIDAR API KEY
        api_key = _extract_api_key(arguments)
        if not api_key:
            return _create_error_result("api_key es requerido", "AUTH_ERROR")

        if not validate_api_key(api_key):
            return _create_error_result("API Key inválida", "AUTH_ERROR")

        # 2. CREAR CONTEXTO (municipality_id -> schema_name)
        municipality_id = _extract_municipality_id(arguments)
        if not municipality_id:
            return _create_error_result("municipality_id es requerido", "VALIDATION_ERROR")

        try:
            ctx = create_context(api_key, municipality_id)
        except ValueError as e:
            return _create_error_result(str(e), "VALIDATION_ERROR")

        logger.info(f"Contexto creado: municipality={municipality_id}, schema={ctx.schema_name}")

        # 3. EJECUTAR TOOL
        if name == "search_cases":
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
            return _create_success_result(result)

        elif name == "get_case":
            result = cases.get_case(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments.get("user_id"),
                include_documents=arguments.get("include_documents", False)
            )
            return _create_success_result(result)

        elif name == "get_case_history":
            result = cases.get_case_history(
                ctx=ctx,
                case_id=arguments["case_id"]
            )
            return _create_success_result(result)

        elif name == "get_case_documents":
            result = cases.get_case_documents(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments.get("user_id")
            )
            return _create_success_result(result)

        elif name == "get_case_permissions":
            result = cases.get_case_permissions(
                ctx=ctx,
                case_id=arguments["case_id"],
                user_id=arguments["user_id"]
            )
            return _create_success_result(result)

        elif name == "search_documents":
            result = documents.search_documents(
                ctx=ctx,
                user_id=arguments["user_id"],
                page=int(arguments.get("page", 1)),
                page_size=int(arguments.get("page_size", 20)),
                search=arguments.get("search"),
                status=arguments.get("status"),
                document_type=arguments.get("document_type"),
                case_id=arguments.get("case_id"),
                min_signers=int(arguments.get("min_signers")) if arguments.get("min_signers") else None
            )
            return _create_success_result(result)

        elif name == "get_document":
            result = await documents.get_document(
                ctx=ctx,
                document_id=arguments["document_id"],
                user_id=arguments.get("user_id")
            )
            return _create_success_result(result)

        elif name == "get_document_types":
            result = system.get_document_types(ctx=ctx)
            return _create_success_result(result)

        elif name == "get_user_info":
            result = system.get_user_info(
                ctx=ctx,
                user_id=arguments["user_id"]
            )
            return _create_success_result(result)

        elif name == "get_pending_signatures":
            result = documents.get_pending_signatures(
                ctx=ctx,
                user_id=arguments["user_id"]
            )
            return _create_success_result(result)

        elif name == "get_document_content":
            result = documents.get_document_content(
                ctx=ctx,
                document_id=arguments["document_id"]
            )
            return _create_success_result(result)

        else:
            return _create_error_result(f"Tool desconocido: {name}", "UNKNOWN_TOOL")

    except ValueError as e:
        return _create_error_result(f"Error de validación: {str(e)}", "VALIDATION_ERROR")
    except Exception as e:
        logger.exception(f"Error ejecutando tool {name}")
        return _create_error_result(f"Error interno: {str(e)}", "INTERNAL_ERROR")


# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Entry point del MCP Server."""
    logger.info("Iniciando GDI-Backend MCP Server...")
    logger.info("Transport: stdio")
    logger.info("Auth: API Key simple")
    logger.info("Tools: 12 herramientas de solo lectura")

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
