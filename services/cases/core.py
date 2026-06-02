"""
Funciones core de expedientes (base).
Contiene create_case async para uso interno en transacciones.
"""

from typing import Dict, Any, Optional
from datetime import datetime
import uuid
from shared.logging import get_logger
from database import (
    fetch_all,
    get_case_number_format,
)
from services.cases.queries import (
    get_department_and_municipality_query,
    get_advisory_lock_query,
    get_next_case_sequence_query,
)
from config.constants import MOVEMENT_TYPES, CASE_STATUS_INACTIVE

logger = get_logger(__name__)


async def create_case(
    connection,
    case_template_id: str,
    reference: str,
    created_by_user_id: str,
    filing_department_id: str,
    creator_sector_id: str,
    owner_sector_id: Optional[str] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """
    Crear nuevo expediente (función base async para uso en transacciones).

    Args:
        connection: Conexión asyncpg activa (debe estar en transacción)
        case_template_id: ID de la plantilla de expediente
        reference: Descripción/referencia del expediente
        created_by_user_id: ID del usuario que crea el expediente
        filing_department_id: ID del departamento que archiva
        creator_sector_id: ID del sector del usuario creador
        owner_sector_id: ID del sector propietario (opcional)
        schema_name: Nombre del schema (obligatorio para multi-tenant)

    Returns:
        Dict con información del expediente creado

    Raises:
        ValueError: Si el departamento no existe
        Exception: Si hay error en la creación
    """
    try:
        # Obtener información del departamento y municipio
        # La query necesita: (department_id, schema_name) para JOIN con public.municipalities
        dept_results = await connection.fetch(
            get_department_and_municipality_query(),
            filing_department_id, schema_name
        )

        if not dept_results:
            raise ValueError(f"Departamento no encontrado: {filing_department_id}")

        dept_acronym = dept_results[0]['dept_acronym']
        municipality_acronym = dept_results[0]['municipality_acronym']

        # Adquirir Advisory Lock ANTES de calcular secuencia
        await connection.execute("SET LOCAL lock_timeout = '10s'")
        await connection.execute(get_advisory_lock_query())

        # Generar número secuencial (dentro del lock)
        year = datetime.now().year
        result = await connection.fetchrow(get_next_case_sequence_query(), year)
        sequence = result['next_sequence'] if result else 1

        # Generar case_number
        case_number = get_case_number_format(dept_acronym, municipality_acronym).format(sequence=sequence)

        # Crear expediente
        case_id = str(uuid.uuid4())

        case_insert = """
            INSERT INTO cases (
                id, case_number, reference, status,
                case_template_id, created_by_user_id,
                owner_department_id, owner_sector_id
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8
            )
        """

        await connection.execute(
            case_insert,
            case_id, case_number, reference, CASE_STATUS_INACTIVE,
            case_template_id, created_by_user_id,
            filing_department_id, owner_sector_id
        )

        # Crear movimiento inicial de creación
        movement_id = str(uuid.uuid4())

        movement_insert = """
            INSERT INTO case_movements (
                id, case_id, type, user_id,
                creator_sector_id, admin_sector_id,
                assigned_sector_id, assigned_user_id,
                reason, is_active
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
            )
        """

        await connection.execute(
            movement_insert,
            movement_id, case_id, MOVEMENT_TYPES["CREATION"], created_by_user_id,
            creator_sector_id, owner_sector_id,
            None, None,
            f"Creación del expediente: {reference}", True
        )

        # Cerrar el movimiento de creación inmediatamente
        close_creation_query = """
            UPDATE case_movements
            SET closed_at = NOW(), closing_reason = $1, is_active = false
            WHERE id = $2
        """
        await connection.execute(close_creation_query, "Expediente creado", movement_id)

        # NO hacer commit aquí - lo maneja el servicio externo
        # Commit/Rollback es responsabilidad del llamador

        return {
            "case_id": case_id,
            "case_number": case_number,
            "reference": reference,
            "status": CASE_STATUS_INACTIVE,
            "created_at": datetime.now()
        }

    except Exception as e:
        logger.error(f"Error creando expediente: {str(e)}")
        raise Exception(f"Error creando expediente: {str(e)}")
