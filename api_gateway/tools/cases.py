"""
Tools MCP para expedientes (lectura y operaciones).
Reutiliza servicios existentes de CaseService.
"""
import logging
from typing import Dict, Any, Optional, List
from api_gateway.context import MCPContext
from services.case_service import CaseService
from database import execute_query
from shared.exceptions import ValidationError, NotFoundError, AuthorizationError
from services.cases.documents import link_official_document, accept_proposed_document, reject_proposed_document
from services.cases.creation import create_case_with_cover_service
from services.cases.transfer import get_available_sectors_for_transfer

logger = logging.getLogger(__name__)


def search_cases(
    ctx: MCPContext,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    date_filter: Optional[str] = None,
    sector_filter: Optional[str] = None,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Buscar expedientes con filtros.

    Args:
        ctx: Contexto MCP con schema_name
        page: Número de página (default 1)
        page_size: Tamaño de página (default 20, max 100)
        search: Texto de búsqueda (case number o referencia)
        status: Filtro de estado (active, inactive, archived)
        date_filter: Filtro de fecha (today, week, month, year)
        sector_filter: Filtro por sector (acronym)
        user_id: ID de usuario para permisos (REQUERIDO para filtrado correcto)

    Returns:
        Dict con cases (lista), total, page, page_size, total_pages

    Raises:
        ValueError: Si page_size > 100 o user_id no proporcionado
        ValidationError: Si date_filter inválido
    """
    logger.info(f"[MCP] search_cases - schema={ctx.schema_name}, page={page}, search={search}")

    # Validaciones
    if page_size > 100:
        raise ValueError("page_size máximo es 100")

    if not user_id:
        raise ValueError("user_id es requerido para search_cases (necesario para permisos)")

    # Reutilizar CaseService.get_cases_by_user
    # Este método ya maneja permisos, filtros y paginación
    try:
        result = CaseService.get_cases_by_user(
            user_id=user_id,
            page=page,
            page_size=page_size,
            status_filter=status,
            search_filter=search,
            date_filter=date_filter,
            sector_filter=sector_filter,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] search_cases - encontrados {result['total']} expedientes")
        return result

    except ValidationError as e:
        logger.error(f"[MCP] search_cases - error de validación: {e}")
        raise
    except Exception as e:
        logger.error(f"[MCP] search_cases - error: {e}")
        raise RuntimeError(f"Error buscando expedientes: {str(e)}")


def get_case(
    ctx: MCPContext,
    case_id: str,
    user_id: Optional[str] = None,
    include_documents: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Obtener detalle de un expediente específico.

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        user_id: ID de usuario (opcional, si se omite se salta validación de permisos)
        include_documents: Si True, incluye documentos vinculados (default False)

    Returns:
        Dict con detalles del expediente o None si no existe/no tiene permisos

    Raises:
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_case - case_id={case_id}, schema={ctx.schema_name}, include_documents={include_documents}")

    try:
        # Si no hay user_id, omitir validación de permisos
        skip_permissions = user_id is None

        if skip_permissions:
            logger.warning(f"[MCP] get_case - sin user_id, omitiendo validación de permisos")

        result = CaseService.get_case_detail(
            case_id=case_id,
            user_id=user_id or "00000000-0000-0000-0000-000000000000",  # Dummy si skip
            skip_permission_check=skip_permissions,
            schema_name=ctx.schema_name
        )

        if not result:
            logger.warning(f"[MCP] get_case - expediente no encontrado o sin permisos")
            return None

        # Incluir documentos si se solicita
        if include_documents:
            result["documents"] = CaseService.get_case_documents(case_id, schema_name=ctx.schema_name)
            logger.info(f"[MCP] get_case - documentos incluidos: {result['documents']['total_official']} oficiales, {result['documents']['total_proposed']} propuestos")

        logger.info(f"[MCP] get_case - expediente encontrado: {result.get('case_number')}")
        return result

    except Exception as e:
        logger.error(f"[MCP] get_case - error: {e}")
        raise RuntimeError(f"Error obteniendo expediente: {str(e)}")


def get_case_history(
    ctx: MCPContext,
    case_id: str
) -> Dict[str, Any]:
    """
    Obtener historial completo de movimientos de un expediente.

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente

    Returns:
        Dict con case_number y movements (lista de movimientos con mensajes formateados)

    Raises:
        NotFoundError: Si el expediente no existe
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_case_history - case_id={case_id}, schema={ctx.schema_name}")

    try:
        result = CaseService.get_case_history(
            case_id=case_id,
            schema_name=ctx.schema_name
        )

        movements_count = len(result.get("movements", []))
        logger.info(f"[MCP] get_case_history - {movements_count} movimientos encontrados")

        return result

    except NotFoundError as e:
        logger.error(f"[MCP] get_case_history - expediente no encontrado: {e}")
        raise
    except Exception as e:
        logger.error(f"[MCP] get_case_history - error: {e}")
        raise RuntimeError(f"Error obteniendo historial: {str(e)}")


def get_case_documents(
    ctx: MCPContext,
    case_id: str,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Obtener documentos vinculados a un expediente (oficiales y propuestos).

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        user_id: ID de usuario (opcional, para validar permisos de vista)

    Returns:
        Dict con:
        - official: Lista de documentos oficiales (firmados) con URLs de PDF
        - proposed: Lista de documentos propuestos (borradores)
        - total_official: Cantidad de documentos oficiales
        - total_proposed: Cantidad de documentos propuestos

    Raises:
        ValueError: Si el usuario no tiene permisos para ver el expediente
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_case_documents - case_id={case_id}, schema={ctx.schema_name}")

    try:
        # Validar permisos si se proporciona user_id
        if user_id:
            can_view = CaseService.can_user_view_case(case_id, user_id, schema_name=ctx.schema_name)
            if not can_view:
                logger.warning(f"[MCP] get_case_documents - usuario {user_id} sin permisos para expediente {case_id}")
                raise ValueError("Usuario no tiene permisos para ver este expediente")

        # Obtener documentos usando servicio existente
        result = CaseService.get_case_documents(case_id, schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_case_documents - encontrados {result['total_official']} oficiales, {result['total_proposed']} propuestos")
        return result

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"[MCP] get_case_documents - error: {e}")
        raise RuntimeError(f"Error obteniendo documentos del expediente: {str(e)}")


def get_case_permissions(
    ctx: MCPContext,
    case_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Obtener permisos específicos del usuario sobre un expediente.

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        user_id: UUID del usuario (REQUERIDO)

    Returns:
        Dict con permisos booleanos:
        - can_view: Puede ver el expediente
        - can_transfer: Puede transferir el expediente
        - can_assign: Puede asignar sectores
        - can_archive: Puede archivar el expediente
        - can_link_documents: Puede vincular documentos
        - can_create_movements: Puede crear movimientos
        - can_subsanar: Puede subsanar documentos
        - ownership_level: Nivel de propiedad (owner, creator, participant)

    Raises:
        ValueError: Si user_id no proporcionado
        NotFoundError: Si el expediente no existe
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_case_permissions - case_id={case_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not user_id:
        raise ValueError("user_id es requerido para get_case_permissions")

    try:
        result = CaseService.get_user_case_permissions(
            case_id=case_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] get_case_permissions - ownership_level={result.get('ownership_level')}")
        return result

    except NotFoundError as e:
        logger.error(f"[MCP] get_case_permissions - expediente no encontrado: {e}")
        raise
    except Exception as e:
        logger.error(f"[MCP] get_case_permissions - error: {e}")
        raise RuntimeError(f"Error obteniendo permisos: {str(e)}")


def get_case_by_number(
    ctx: MCPContext,
    case_number: str,
    user_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Obtener un expediente por su número exacto.

    Args:
        ctx: Contexto MCP con schema_name
        case_number: Número exacto del expediente (ej: "EE-2026-00001234")
        user_id: ID de usuario (opcional, no se usa para filtrar - solo para auditoría)

    Returns:
        Dict con datos del expediente o None si no existe

    Raises:
        ValueError: Si case_number está vacío
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_case_by_number - case_number={case_number}, schema={ctx.schema_name}")

    if not case_number:
        raise ValueError("case_number es requerido")

    try:
        case_service = CaseService()

        # Buscar el expediente por número exacto SIN restricciones de usuario
        result = case_service.get_case_by_exact_number_unrestricted(
            case_number=case_number,
            schema_name=ctx.schema_name
        )

        if not result:
            logger.warning(f"[MCP] get_case_by_number - expediente no encontrado: {case_number}")
            return None

        logger.info(f"[MCP] get_case_by_number - expediente encontrado: {case_number}")
        return result

    except Exception as e:
        logger.error(f"[MCP] get_case_by_number - error: {e}")
        raise RuntimeError(f"Error buscando expediente por número: {str(e)}")


def prepare_assignment(
    ctx: MCPContext,
    case_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Preparar información para asignación de expediente.
    Verifica permisos y retorna sectores donde el usuario puede asignar.

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        user_id: UUID del usuario

    Returns:
        Dict con:
        - success: bool
        - status: "OK" o "NOT_ALLOWED"
        - user_sectors_in_case: sectores del usuario con rol en el expediente
        - available_sectors: sectores destino disponibles
        - total: cantidad de sectores del usuario en el expediente
        - total_available_sectors: cantidad de sectores disponibles

    Raises:
        ValueError: Si case_id o user_id están vacíos
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] prepare_assignment - case_id={case_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not case_id:
        raise ValueError("case_id es requerido")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # 1. Obtener sectores del usuario donde puede EDITAR
        user_sectors_query = """
            -- Sector principal
            SELECT s.id as sector_id
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            WHERE u.id = %s AND s.is_active = true

            UNION

            -- Sectores adicionales con can_edit=true
            SELECT s2.id as sector_id
            FROM users u
            JOIN user_sector_permissions usp ON u.id = usp.user_id
            JOIN sectors s2 ON usp.sector_id = s2.id
            WHERE u.id = %s AND s2.is_active = true AND usp.can_edit = true
        """
        user_sectors_result = execute_query(
            user_sectors_query,
            (user_id, user_id),
            schema_name=ctx.schema_name
        )
        user_sector_ids = [str(row['sector_id']) for row in user_sectors_result if row['sector_id']]

        # 2. Obtener admin_sector del expediente
        admin_sector_query = """
            SELECT
                s.id as sector_id,
                d.acronym || '#' || s.acronym as sector_acronym,
                d.name as department_name
            FROM case_movements cm
            JOIN sectors s ON cm.admin_sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE cm.case_id = %s
              AND cm.is_active = false
              AND cm.type IN ('creation', 'transfer')
            ORDER BY cm.closed_at DESC
            LIMIT 1
        """
        admin_sector_result = execute_query(
            admin_sector_query,
            (case_id,),
            schema_name=ctx.schema_name
        )

        admin_sector_id = None
        admin_sector_data = None
        if admin_sector_result:
            admin_sector_id = str(admin_sector_result[0]['sector_id'])
            admin_sector_data = admin_sector_result[0]

        # 3. Obtener assigned_sectors activos
        assigned_sectors_query = """
            SELECT DISTINCT
                s.id as sector_id,
                d.acronym || '#' || s.acronym as sector_acronym,
                d.name as department_name
            FROM case_movements cm
            JOIN sectors s ON cm.assigned_sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE cm.case_id = %s
              AND cm.is_active = true
              AND cm.assigned_sector_id IS NOT NULL
        """
        assigned_sectors_result = execute_query(
            assigned_sectors_query,
            (case_id,),
            schema_name=ctx.schema_name
        )

        assigned_sector_ids = []
        assigned_sectors_data = {}
        for row in assigned_sectors_result:
            sector_id = str(row['sector_id'])
            assigned_sector_ids.append(sector_id)
            assigned_sectors_data[sector_id] = row

        # 4. Encontrar sectores del usuario que participan en el expediente
        user_sectors_in_case = []

        if admin_sector_id and admin_sector_id in user_sector_ids:
            user_sectors_in_case.append({
                "sector_id": admin_sector_id,
                "acronym": admin_sector_data['sector_acronym'],
                "department": admin_sector_data['department_name'],
                "role": "ADMIN"
            })

        for sector_id in assigned_sector_ids:
            if sector_id in user_sector_ids and sector_id != admin_sector_id:
                user_sectors_in_case.append({
                    "sector_id": sector_id,
                    "acronym": assigned_sectors_data[sector_id]['sector_acronym'],
                    "department": assigned_sectors_data[sector_id]['department_name'],
                    "role": "ASIGNADO"
                })

        # 5. Si no tiene sectores en el expediente, no tiene permisos
        if not user_sectors_in_case:
            logger.warning(f"[MCP] prepare_assignment - usuario {user_id} sin permisos en expediente {case_id}")
            return {
                "success": False,
                "status": "NOT_ALLOWED",
                "message": "Usuario no tiene permisos sobre este expediente"
            }

        # 6. Obtener todos los sectores activos disponibles
        available_sectors_query = """
            SELECT DISTINCT
                s.id as sector_id,
                d.acronym || '#' || s.acronym as sector_acronym,
                d.name as department_name
            FROM sectors s
            JOIN departments d ON s.department_id = d.id
            WHERE s.is_active = true
            ORDER BY sector_acronym
        """
        available_sectors_result = execute_query(
            available_sectors_query,
            schema_name=ctx.schema_name
        )

        available_sectors = [
            {
                "sector_id": str(row['sector_id']),
                "acronym": row['sector_acronym'],
                "department": row['department_name']
            }
            for row in available_sectors_result
        ]

        if not available_sectors:
            return {
                "success": False,
                "status": "NOT_ALLOWED",
                "message": "No hay sectores disponibles en la municipalidad"
            }

        # Ordenar: ADMIN primero
        user_sectors_in_case.sort(key=lambda x: (x['role'] != 'ADMIN', x['acronym']))

        logger.info(f"[MCP] prepare_assignment - usuario tiene {len(user_sectors_in_case)} sector(es) en expediente")

        return {
            "success": True,
            "status": "OK",
            "user_sectors_in_case": user_sectors_in_case,
            "available_sectors": available_sectors,
            "total": len(user_sectors_in_case),
            "total_available_sectors": len(available_sectors)
        }

    except Exception as e:
        logger.error(f"[MCP] prepare_assignment - error: {e}")
        raise RuntimeError(f"Error preparando asignación: {str(e)}")


def assign_case(
    ctx: MCPContext,
    case_id: str,
    target_sector_id: str,
    reason: str,
    user_id: str,
    assigned_user_id: Optional[str] = None,
    create_official_doc: bool = False
) -> Dict[str, Any]:
    """
    Asignar expediente a un sector (sin transferir propiedad).

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        target_sector_id: UUID del sector destino
        reason: Razón de la asignación (5-500 caracteres)
        user_id: UUID del usuario que realiza la asignación
        assigned_user_id: UUID del usuario específico asignado (opcional)
        create_official_doc: Si true, genera documento PV automáticamente

    Returns:
        Dict con:
        - success: bool
        - movement_id: ID del movimiento creado
        - case_number: número del expediente
        - action_type: "asignado"
        - target_sector: acrónimo del sector destino
        - target_department: nombre del departamento
        - official_document: info del documento PV (si se generó)

    Raises:
        ValueError: Si faltan parámetros o reason fuera de rango
        AuthorizationError: Si el usuario no tiene permisos
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] assign_case - case_id={case_id}, target={target_sector_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not target_sector_id:
        raise ValueError("target_sector_id es requerido")
    if not reason:
        raise ValueError("reason es requerido")
    if len(reason) < 5 or len(reason) > 500:
        raise ValueError("reason debe tener entre 5 y 500 caracteres")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # Verificar permisos del usuario
        prep = prepare_assignment(ctx, case_id, user_id)
        if not prep.get("success"):
            raise AuthorizationError(prep.get("message", "Sin permisos para asignar"))

        # Ejecutar asignación usando CaseService
        result = CaseService.transfer_case(
            case_id=case_id,
            target_sector_id=target_sector_id,
            reason=reason,
            user_id=user_id,
            transfer_ownership=False,  # Asignación NO transfiere propiedad
            assigned_user_id=assigned_user_id,
            create_official_doc=create_official_doc,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] assign_case - asignación exitosa: movement_id={result.get('movement_id')}")

        return {
            "success": True,
            "movement_id": result.get("movement_id"),
            "case_number": result.get("case_number"),
            "action_type": "asignado",
            "target_sector": result.get("target_sector"),
            "target_department": result.get("target_department"),
            "transferred_by": result.get("transferred_by"),
            "assigned_user": result.get("assigned_user"),
            "official_document": result.get("official_document")
        }

    except (ValueError, AuthorizationError):
        raise
    except Exception as e:
        logger.error(f"[MCP] assign_case - error: {e}")
        raise RuntimeError(f"Error asignando expediente: {str(e)}")


def close_assignment(
    ctx: MCPContext,
    case_id: str,
    movement_id: str,
    reason: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Cerrar una asignación de expediente.

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        movement_id: UUID del movimiento a cerrar
        reason: Razón del cierre (5-500 caracteres)
        user_id: UUID del usuario que cierra

    Returns:
        Dict con:
        - success: bool
        - movement_id: ID del movimiento cerrado
        - case_id: ID del expediente
        - movement_type: tipo de movimiento
        - closing_reason: razón del cierre

    Raises:
        ValueError: Si faltan parámetros o reason fuera de rango
        AuthorizationError: Si el usuario no tiene permisos
        NotFoundError: Si no existe el movimiento
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] close_assignment - case_id={case_id}, movement_id={movement_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not movement_id:
        raise ValueError("movement_id es requerido")
    if not reason:
        raise ValueError("reason es requerido")
    if len(reason) < 5 or len(reason) > 500:
        raise ValueError("reason debe tener entre 5 y 500 caracteres")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = CaseService.close_assignment(
            case_id=case_id,
            movement_id=movement_id,
            reason=reason,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] close_assignment - asignación cerrada: movement_id={movement_id}")

        return {
            "success": True,
            "movement_id": str(result.get("movement_id")),
            "case_id": str(result.get("case_id")),
            "movement_type": result.get("movement_type"),
            "closing_reason": result.get("closing_reason")
        }

    except (ValueError, AuthorizationError, NotFoundError):
        raise
    except Exception as e:
        logger.error(f"[MCP] close_assignment - error: {e}")
        raise RuntimeError(f"Error cerrando asignación: {str(e)}")


def _get_user_sector_id(user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Helper privado para resolver el sector_id de un usuario.

    Args:
        user_id: UUID del usuario
        schema_name: Nombre del schema (keyword-only)

    Returns:
        Dict con sector_id del usuario

    Raises:
        NotFoundError: Si el usuario no existe
    """
    query = "SELECT sector_id FROM users WHERE id = %s"
    result = execute_query(query, (user_id,), schema_name=schema_name)
    if not result:
        raise NotFoundError(f"Usuario no encontrado: {user_id}")
    return result[0]


async def create_case(
    ctx: MCPContext,
    case_template_id: str,
    reference: str,
    user_id: str,
    owner_sector_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Crear un expediente con carátula automática.

    Args:
        ctx: Contexto MCP con schema_name
        case_template_id: UUID del template de expediente
        reference: Referencia/asunto del expediente
        user_id: UUID del usuario creador
        owner_sector_id: UUID del sector propietario (opcional, default: sector del usuario)

    Returns:
        Dict con:
        - success: bool
        - case_id: UUID del expediente creado
        - case_number: número del expediente
        - message: mensaje de confirmación

    Raises:
        ValueError: Si faltan parámetros requeridos
        ValidationError: Si datos inválidos
        NotFoundError: Si template o usuario no existen
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] create_case - template={case_template_id}, user={user_id}, schema={ctx.schema_name}")

    if not case_template_id:
        raise ValueError("case_template_id es requerido")
    if not reference:
        raise ValueError("reference es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = await create_case_with_cover_service(
            case_template_id=case_template_id,
            reference=reference,
            user_id=user_id,
            owner_sector_id=owner_sector_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] create_case - expediente creado: {result.get('case_id')}")

        return {
            "success": True,
            "case_id": str(result.get("case_id")),
            "case_number": result.get("case_number"),
            "message": "Expediente creado exitosamente"
        }

    except (ValidationError, NotFoundError):
        raise
    except Exception as e:
        logger.error(f"[MCP] create_case - error: {e}")
        raise RuntimeError(f"Error creando expediente: {str(e)}")


def transfer_case(
    ctx: MCPContext,
    case_id: str,
    target_sector_id: str,
    reason: str,
    user_id: str,
    assigned_user_id: Optional[str] = None,
    create_official_doc: bool = False
) -> Dict[str, Any]:
    """
    Transferir expediente a otro sector (transfiere propiedad).

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        target_sector_id: UUID del sector destino
        reason: Razón de la transferencia (5-500 caracteres)
        user_id: UUID del usuario que realiza la transferencia
        assigned_user_id: UUID del usuario específico asignado (opcional)
        create_official_doc: Si true, genera documento PV automáticamente

    Returns:
        Dict con:
        - success: bool
        - movement_id: ID del movimiento creado
        - case_number: número del expediente
        - action_type: "transferido"
        - target_sector: acrónimo del sector destino
        - target_department: nombre del departamento
        - official_document: info del documento PV (si se generó)

    Raises:
        ValueError: Si faltan parámetros o reason fuera de rango
        AuthorizationError: Si el usuario no tiene permisos
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] transfer_case - case_id={case_id}, target={target_sector_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not target_sector_id:
        raise ValueError("target_sector_id es requerido")
    if not reason:
        raise ValueError("reason es requerido")
    if len(reason) < 5 or len(reason) > 500:
        raise ValueError("reason debe tener entre 5 y 500 caracteres")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # Verificar permisos del usuario
        prep = prepare_assignment(ctx, case_id, user_id)
        if not prep.get("success"):
            raise AuthorizationError(prep.get("message", "Sin permisos para transferir"))

        # Ejecutar transferencia usando CaseService
        result = CaseService.transfer_case(
            case_id=case_id,
            target_sector_id=target_sector_id,
            reason=reason,
            user_id=user_id,
            transfer_ownership=True,  # Transferencia SÍ transfiere propiedad
            assigned_user_id=assigned_user_id,
            create_official_doc=create_official_doc,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] transfer_case - transferencia exitosa: movement_id={result.get('movement_id')}")

        return {
            "success": True,
            "movement_id": result.get("movement_id"),
            "case_number": result.get("case_number"),
            "action_type": "transferido",
            "target_sector": result.get("target_sector"),
            "target_department": result.get("target_department"),
            "transferred_by": result.get("transferred_by"),
            "assigned_user": result.get("assigned_user"),
            "official_document": result.get("official_document")
        }

    except (ValueError, AuthorizationError):
        raise
    except Exception as e:
        logger.error(f"[MCP] transfer_case - error: {e}")
        raise RuntimeError(f"Error transfiriendo expediente: {str(e)}")


def link_document_to_case(
    ctx: MCPContext,
    case_id: str,
    official_document_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Vincular un documento oficial a un expediente.

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        official_document_id: UUID del documento oficial
        user_id: UUID del usuario que vincula

    Returns:
        Dict con:
        - success: bool
        - (datos adicionales del resultado de vinculación)

    Raises:
        ValueError: Si faltan parámetros requeridos
        NotFoundError: Si expediente o documento no existen
        AuthorizationError: Si el usuario no tiene permisos
        ValidationError: Si datos inválidos
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] link_document_to_case - case_id={case_id}, doc={official_document_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not official_document_id:
        raise ValueError("official_document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # Resolver sector_id del usuario
        user_data = _get_user_sector_id(user_id, schema_name=ctx.schema_name)

        result = link_official_document(
            case_id=case_id,
            official_document_id=official_document_id,
            linking_user_id=user_id,
            user_sector_id=str(user_data["sector_id"]),
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] link_document_to_case - documento vinculado exitosamente")

        return {
            "success": True,
            **result
        }

    except (NotFoundError, AuthorizationError, ValidationError):
        raise
    except Exception as e:
        logger.error(f"[MCP] link_document_to_case - error: {e}")
        raise RuntimeError(f"Error vinculando documento al expediente: {str(e)}")


def propose_document(
    ctx: MCPContext,
    case_id: str,
    document_draft_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Proponer un documento borrador para vincular a un expediente.

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        document_draft_id: UUID del documento borrador
        user_id: UUID del usuario que propone

    Returns:
        Dict con:
        - success: bool
        - case_id: UUID del expediente
        - document_draft_id: UUID del documento borrador
        - message: mensaje de confirmación

    Raises:
        ValueError: Si faltan parámetros requeridos
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] propose_document - case_id={case_id}, doc={document_draft_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not document_draft_id:
        raise ValueError("document_draft_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        link_query = """
            INSERT INTO case_proposed_documents (case_id, document_draft_id, proposing_date, is_active)
            VALUES (%s, %s, NOW(), true)
        """
        execute_query(
            link_query,
            (case_id, document_draft_id),
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] propose_document - documento propuesto exitosamente")

        return {
            "success": True,
            "case_id": case_id,
            "document_draft_id": document_draft_id,
            "message": "Documento propuesto para vincular al expediente"
        }

    except Exception as e:
        logger.error(f"[MCP] propose_document - error: {e}")
        raise RuntimeError(f"Error proponiendo documento: {str(e)}")


def prepare_transfer(
    ctx: MCPContext,
    case_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Preparar información para transferencia de expediente.
    Verifica permisos y retorna sectores disponibles para transferir.

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        user_id: UUID del usuario

    Returns:
        Dict con:
        - success: bool
        - available_sectors: lista de sectores disponibles
        - total: cantidad de sectores disponibles

    Raises:
        ValueError: Si faltan parámetros requeridos
        AuthorizationError: Si el usuario no tiene permisos
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] prepare_transfer - case_id={case_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        sectors = get_available_sectors_for_transfer(
            case_id=case_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] prepare_transfer - {len(sectors)} sectores disponibles")

        return {
            "success": True,
            "available_sectors": sectors,
            "total": len(sectors)
        }

    except AuthorizationError:
        raise
    except Exception as e:
        logger.error(f"[MCP] prepare_transfer - error: {e}")
        raise RuntimeError(f"Error preparando transferencia: {str(e)}")


def accept_proposal(
    ctx: MCPContext,
    case_id: str,
    proposed_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Aceptar un documento propuesto para vincularlo al expediente.

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        proposed_id: UUID de la propuesta
        user_id: UUID del usuario que acepta

    Returns:
        Dict con:
        - success: bool
        - (datos adicionales del resultado de aceptación)

    Raises:
        ValueError: Si faltan parámetros requeridos
        NotFoundError: Si la propuesta no existe
        ValidationError: Si la propuesta ya fue procesada
        AuthorizationError: Si el usuario no tiene permisos
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] accept_proposal - case_id={case_id}, proposed_id={proposed_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not proposed_id:
        raise ValueError("proposed_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # Resolver sector_id del usuario
        user_data = _get_user_sector_id(user_id, schema_name=ctx.schema_name)

        result = accept_proposed_document(
            case_id=case_id,
            proposed_id=proposed_id,
            user_id=user_id,
            user_sector_id=str(user_data["sector_id"]),
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] accept_proposal - propuesta aceptada exitosamente")

        return {
            "success": True,
            **result
        }

    except (NotFoundError, ValidationError, AuthorizationError):
        raise
    except Exception as e:
        logger.error(f"[MCP] accept_proposal - error: {e}")
        raise RuntimeError(f"Error aceptando propuesta: {str(e)}")


def reject_proposal(
    ctx: MCPContext,
    case_id: str,
    proposed_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Rechazar un documento propuesto (desactivar sin vincular).

    Args:
        ctx: Contexto MCP con schema_name
        case_id: UUID del expediente
        proposed_id: UUID de la propuesta
        user_id: UUID del usuario que rechaza

    Returns:
        Dict con:
        - success: bool
        - (datos adicionales del resultado de rechazo)

    Raises:
        ValueError: Si faltan parámetros requeridos
        NotFoundError: Si la propuesta no existe
        ValidationError: Si la propuesta ya fue procesada
        AuthorizationError: Si el usuario no tiene permisos
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] reject_proposal - case_id={case_id}, proposed_id={proposed_id}, user={user_id}")

    if not case_id:
        raise ValueError("case_id es requerido")
    if not proposed_id:
        raise ValueError("proposed_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # Resolver sector_id del usuario
        user_data = _get_user_sector_id(user_id, schema_name=ctx.schema_name)

        result = reject_proposed_document(
            case_id=case_id,
            proposed_id=proposed_id,
            user_id=user_id,
            user_sector_id=str(user_data.get("sector_id", "")),
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] reject_proposal - propuesta rechazada exitosamente")

        return {
            "success": True,
            **result
        }

    except (NotFoundError, ValidationError, AuthorizationError):
        raise
    except Exception as e:
        logger.error(f"[MCP] reject_proposal - error: {e}")
        raise RuntimeError(f"Error rechazando propuesta: {str(e)}")


def get_sector_users_list(
    ctx: MCPContext,
    sector_id: str
) -> Dict[str, Any]:
    """
    Obtener usuarios activos de un sector especifico.

    Args:
        ctx: Contexto MCP con schema_name
        sector_id: UUID del sector

    Returns:
        Dict con:
        - users: lista de usuarios [{user_id, full_name}]
        - total: cantidad de usuarios

    Raises:
        ValueError: Si sector_id esta vacio
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_sector_users_list - sector_id={sector_id}, schema={ctx.schema_name}")

    if not sector_id:
        raise ValueError("sector_id es requerido")

    try:
        from services.cases.transfer import get_sector_users

        users = get_sector_users(sector_id, schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_sector_users_list - {len(users)} usuarios encontrados en sector {sector_id}")

        return {
            "users": users,
            "total": len(users)
        }

    except Exception as e:
        logger.error(f"[MCP] get_sector_users_list - error: {e}")
        raise RuntimeError(f"Error obteniendo usuarios del sector: {str(e)}")
