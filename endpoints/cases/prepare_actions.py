"""
Endpoints para preparar acciones sobre expedientes (asignaciones y transferencias)
GET /cases/{case_id}/prepare-assignment - Obtener sectores donde el usuario puede asignar
GET /cases/{case_id}/prepare-transfer - Obtener sectores donde el usuario puede transferir
"""

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from typing import List, Literal
from auth import get_current_user
from models.schemas import AuthenticatedUser
from database import execute_query
from shared.exceptions import exception_to_http_exception, NotFoundError, AuthorizationError
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from config.constants import (
    PREPARE_ASSIGNMENT_SUCCESS,
    PREPARE_ASSIGNMENT_ERROR,
    PREPARE_ASSIGNMENT_NO_PERMISSION,
    PREPARE_ASSIGNMENT_NO_SECTORS,
    PREPARE_TRANSFER_SUCCESS,
    PREPARE_TRANSFER_ERROR,
    PREPARE_TRANSFER_NO_PERMISSION,
    PREPARE_TRANSFER_NO_SECTORS,
    PREPARE_TRANSFER_NOT_ADMIN
)
from shared.logging import get_logger
logger = get_logger(__name__)

# Inicializar router
router = APIRouter(tags=["expedientes"])


# ============================================================================
# SHARED MODELS
# ============================================================================

class SectorInfo(BaseModel):
    """Información de un sector"""
    sector_id: str = Field(..., example="sec-123")
    acronym: str = Field(..., example="SMG#SEC")
    department: str = Field(..., example="Secretaría General")


class UserSectorInCase(BaseModel):
    """Sector del usuario con rol en el expediente"""
    sector_id: str = Field(..., example="sec-123")
    acronym: str = Field(..., example="SMG#SEC")
    department: str = Field(..., example="Secretaría General")
    role: Literal["ADMIN", "ASIGNADO"] = Field(..., example="ADMIN")


# ============================================================================
# PREPARE ASSIGNMENT MODELS
# ============================================================================

class PrepareAssignmentData(BaseModel):
    """Datos para preparar asignación"""
    user_sectors_in_case: List[UserSectorInCase] = Field(default_factory=list)
    available_sectors: List[SectorInfo] = Field(default_factory=list)
    total: int = Field(..., example=2)
    total_available_sectors: int = Field(..., example=10)


class PrepareAssignmentResponse(BaseModel):
    """Respuesta de preparar asignación"""
    success: bool = Field(True, example=True)
    data: PrepareAssignmentData = Field(default=None)
    status: str = Field(default=None, example="NOT_ALLOWED")
    message: str = Field(..., example="Tienes acceso a 2 sector(es) en este expediente")


# ============================================================================
# PREPARE TRANSFER MODELS
# ============================================================================

class PrepareTransferData(BaseModel):
    """Datos para preparar transferencia"""
    admin_sector: SectorInfo
    available_sectors: List[SectorInfo] = Field(default_factory=list)
    total_available_sectors: int = Field(..., example=10)


class PrepareTransferResponse(BaseModel):
    """Respuesta de preparar transferencia"""
    success: bool = Field(True, example=True)
    data: PrepareTransferData = Field(default=None)
    status: str = Field(default=None, example="NOT_ALLOWED")
    message: str = Field(..., example="OK")

@router.get("/{case_id}/prepare-assignment", response_model=PrepareAssignmentResponse)
async def prepare_assignment(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> PrepareAssignmentResponse:
    """
    Obtener sectores donde el usuario puede realizar asignaciones

    Retorna los sectores del expediente donde el usuario tiene acceso,
    indicando si tiene rol de ADMIN o ASIGNADO en cada uno.

    Solo usuarios con rol ADMIN o ASIGNADO pueden usar este endpoint.

    Roles:
    - ADMIN: Sector administrador actual del expediente
    - ASIGNADO: Sector con asignación activa

    Si el usuario no tiene permisos, retorna status: "NOT_ALLOWED"
    """
    try:
        logger.info(f"Preparing assignment for case {case_id}")

        db_user_id = get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        # 1. Obtener sectores del usuario donde puede EDITAR (principal + permissions con can_edit=true)
        user_sectors_query = """
            -- Sector principal (siempre puede editar)
            SELECT s.id as sector_id
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            WHERE u.id = %s AND s.is_active = true

            UNION

            -- Sectores adicionales (solo si can_edit=true)
            SELECT s2.id as sector_id
            FROM users u
            JOIN user_sector_permissions usp ON u.id = usp.user_id
            JOIN sectors s2 ON usp.sector_id = s2.id
            WHERE u.id = %s AND s2.is_active = true AND usp.can_edit = true
        """
        user_sectors_result = execute_query(user_sectors_query, (db_user_id, db_user_id), schema_name=schema_name)
        user_sector_ids = [str(row['sector_id']) for row in user_sectors_result if row['sector_id']]

        # Continuar para obtener available_sectors aunque user_sector_ids esté vacío
        # El frontend necesita ver las opciones disponibles

        # 2. Obtener admin_sector del expediente (último movimiento cerrado creation/transfer)
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
        admin_sector_result = execute_query(admin_sector_query, (case_id,), schema_name=schema_name)

        admin_sector_id = None
        admin_sector_data = None
        if admin_sector_result:
            admin_sector_id = str(admin_sector_result[0]['sector_id'])
            admin_sector_data = admin_sector_result[0]

        # 3. Obtener assigned_sectors del expediente (movimientos activos)
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
        assigned_sectors_result = execute_query(assigned_sectors_query, (case_id,), schema_name=schema_name)

        assigned_sector_ids = []
        assigned_sectors_data = {}
        for row in assigned_sectors_result:
            sector_id = str(row['sector_id'])
            assigned_sector_ids.append(sector_id)
            assigned_sectors_data[sector_id] = row

        # 4. Cruzar sectores: encontrar sectores del usuario que participan en el expediente
        user_sectors_in_case = []

        # 4a. Verificar si el usuario tiene acceso al admin_sector
        if admin_sector_id and admin_sector_id in user_sector_ids:
            user_sectors_in_case.append({
                "sector_id": admin_sector_id,
                "acronym": admin_sector_data['sector_acronym'],
                "department": admin_sector_data['department_name'],
                "role": "ADMIN"
            })

        # 4b. Verificar sectores asignados (excluyendo el admin_sector si ya se agregó)
        for sector_id in assigned_sector_ids:
            if sector_id in user_sector_ids and sector_id != admin_sector_id:
                user_sectors_in_case.append({
                    "sector_id": sector_id,
                    "acronym": assigned_sectors_data[sector_id]['sector_acronym'],
                    "department": assigned_sectors_data[sector_id]['department_name'],
                    "role": "ASIGNADO"
                })

        # 5. Verificar si el usuario tiene algún sector en el expediente
        if not user_sectors_in_case:
            logger.warning(f"User {db_user_id} has no permission on case {case_id}")
            return PrepareAssignmentResponse(
                success=False,
                status="NOT_ALLOWED",
                message=PREPARE_ASSIGNMENT_NO_PERMISSION
            )

        # 6. Ordenar: ADMIN primero, luego ASIGNADOS
        user_sectors_in_case.sort(key=lambda x: (x['role'] != 'ADMIN', x['acronym']))

        # 7. Obtener todos los sectores activos del mismo municipio
        # En multi-tenant, el schema actual YA filtra por municipio
        # No necesitamos filtro adicional por municipality_id
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
        available_sectors_result = execute_query(available_sectors_query, schema_name=schema_name)

        available_sectors = []
        if available_sectors_result:

            available_sectors = [
                {
                    "sector_id": str(row['sector_id']),
                    "acronym": row['sector_acronym'],
                    "department": row['department_name']
                }
                for row in available_sectors_result
            ]

        # Verificar si hay sectores disponibles
        if len(available_sectors) == 0:
            logger.warning(f"No available sectors in municipality for case {case_id}")
            return PrepareAssignmentResponse(
                success=False,
                status="NOT_ALLOWED",
                message=PREPARE_ASSIGNMENT_NO_SECTORS
            )

        logger.info(f"User {db_user_id} has access to {len(user_sectors_in_case)} sector(s) in case {case_id}")
        
        return PrepareAssignmentResponse(
            success=True,
            data=PrepareAssignmentData(
                user_sectors_in_case=user_sectors_in_case,
                available_sectors=available_sectors,
                total=len(user_sectors_in_case),
                total_available_sectors=len(available_sectors)
            ),
            message=f"{PREPARE_ASSIGNMENT_SUCCESS}: {len(user_sectors_in_case)} sector(es)"
        )

    except Exception as e:
        logger.error(f"Error in prepare_assignment endpoint: {str(e)}")
        raise exception_to_http_exception(e)

@router.get("/{case_id}/prepare-transfer", response_model=PrepareTransferResponse)
async def prepare_transfer(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> PrepareTransferResponse:
    """
    Obtener sectores disponibles para transferir el expediente

    Solo usuarios con rol ADMIN sobre el expediente pueden usar este endpoint.

    Retorna:
    - admin_sector: Sector administrador actual (del cual se hará la transferencia)
    - available_sectors: Todos los sectores activos del mismo municipio

    Si el usuario no es ADMIN, retorna status: "NOT_ALLOWED"
    """
    try:
        logger.info(f"Preparing transfer for case {case_id}")

        db_user_id = get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        # 1. Obtener sectores del usuario donde puede EDITAR (principal + permissions con can_edit=true)
        user_sectors_query = """
            -- Sector principal (siempre puede editar)
            SELECT s.id as sector_id
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            WHERE u.id = %s AND s.is_active = true

            UNION

            -- Sectores adicionales (solo si can_edit=true)
            SELECT s2.id as sector_id
            FROM users u
            JOIN user_sector_permissions usp ON u.id = usp.user_id
            JOIN sectors s2 ON usp.sector_id = s2.id
            WHERE u.id = %s AND s2.is_active = true AND usp.can_edit = true
        """
        user_sectors_result = execute_query(user_sectors_query, (db_user_id, db_user_id), schema_name=schema_name)
        user_sector_ids = [str(row['sector_id']) for row in user_sectors_result if row['sector_id']]

        # Continuar para obtener available_sectors aunque user_sector_ids esté vacío
        # El frontend necesita ver las opciones disponibles

        # 2. Obtener admin_sector del expediente (último movimiento cerrado creation/transfer)
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
        admin_sector_result = execute_query(admin_sector_query, (case_id,), schema_name=schema_name)

        if not admin_sector_result:
            logger.error(f"Case {case_id} not found or has no admin sector")
            raise NotFoundError("Expediente no encontrado o sin sector administrador")

        admin_sector_id = str(admin_sector_result[0]['sector_id'])
        admin_sector_data = admin_sector_result[0]

        # 3. Verificar si el usuario tiene acceso al admin_sector (SOLO ADMIN puede transferir)
        if admin_sector_id not in user_sector_ids:
            logger.warning(f"User {db_user_id} is not admin of case {case_id}")
            return PrepareTransferResponse(
                success=False,
                status="NOT_ALLOWED",
                message=PREPARE_TRANSFER_NOT_ADMIN
            )

        # 4. Construir admin_sector para retornar
        admin_sector = {
            "sector_id": admin_sector_id,
            "acronym": admin_sector_data['sector_acronym'],
            "department": admin_sector_data['department_name']
        }

        # 5. Obtener todos los sectores activos del mismo municipio
        # En multi-tenant, el schema actual YA filtra por municipio
        # No necesitamos filtro adicional por municipality_id
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
        available_sectors_result = execute_query(available_sectors_query, schema_name=schema_name)

        available_sectors = []
        if available_sectors_result:

            available_sectors = [
                {
                    "sector_id": str(row['sector_id']),
                    "acronym": row['sector_acronym'],
                    "department": row['department_name']
                }
                for row in available_sectors_result
            ]

        # Verificar si hay sectores disponibles
        if len(available_sectors) == 0:
            logger.warning(f"No available sectors in municipality for case {case_id}")
            return PrepareTransferResponse(
                success=False,
                status="NOT_ALLOWED",
                message=PREPARE_TRANSFER_NO_SECTORS
            )

        logger.info(f"User {db_user_id} can transfer case {case_id}. {len(available_sectors)} sectors available")
        
        return PrepareTransferResponse(
            success=True,
            data=PrepareTransferData(
                admin_sector=admin_sector,
                available_sectors=available_sectors,
                total_available_sectors=len(available_sectors)
            ),
            message=PREPARE_TRANSFER_SUCCESS
        )

    except Exception as e:
        logger.error(f"Error in prepare_transfer endpoint: {str(e)}")
        raise exception_to_http_exception(e)
