
from typing import Dict, Any, Optional
from datetime import datetime
import uuid
from shared.logging import get_logger
from shared.exceptions import (
    ValidationError,
    TransientLookupError,
    DatabaseBusyError,
    BusinessLogicError,
)
from database import (
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
    filing_department_id: str,
    *,
    created_by_user_id: Optional[str] = None,
    created_by_citizen: Optional[str] = None,
    initiator_citizen_id: Optional[str] = None,
    creator_sector_id: Optional[str] = None,
    owner_sector_id: Optional[str] = None,
    schema_name: str
) -> Dict[str, Any]:
    if bool(created_by_user_id) == bool(created_by_citizen):
        raise ValueError(
            "Debe proveerse exactamente uno de created_by_user_id o created_by_citizen"
        )
    is_citizen_actor = created_by_citizen is not None

    try:
        if owner_sector_id is None:
            owner_sector_id = creator_sector_id

        if is_citizen_actor:
            creator_sector_id = owner_sector_id

        dept_results = await connection.fetch(
            get_department_and_municipality_query(),
            filing_department_id, schema_name
        )

        if not dept_results:
            raise ValueError(f"Departamento no encontrado: {filing_department_id}")

        dept_acronym = dept_results[0]['dept_acronym']
        municipality_acronym = dept_results[0]['municipality_acronym']

        template_result = await connection.fetch(
            "SELECT is_reserved FROM case_templates WHERE id = $1",
            case_template_id
        )
        if not template_result:
            raise ValueError(f"Template de expediente no encontrado: {case_template_id}")
        if template_result[0]['is_reserved'] is None:
            raise ValueError(
                f"Template de expediente '{case_template_id}' tiene is_reserved NULL — "
                "no se puede determinar si el expediente debe crearse como reservado."
            )
        template_is_reserved = bool(template_result[0]['is_reserved'])

        await connection.execute("SET LOCAL lock_timeout = '10s'")
        await connection.execute(get_advisory_lock_query(), schema_name)

        year = datetime.now().year
        result = await connection.fetchrow(get_next_case_sequence_query(), year)
        sequence = result['next_sequence'] if result else 1

        case_number = get_case_number_format(dept_acronym, municipality_acronym).format(sequence=sequence)

        case_id = str(uuid.uuid4())

        case_insert = """
            INSERT INTO cases (
                id, case_number, reference, status,
                case_template_id, created_by_user_id, created_by_citizen,
                initiator_citizen_id,
                owner_department_id, owner_sector_id
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
            )
        """

        await connection.execute(
            case_insert,
            case_id, case_number, reference, CASE_STATUS_INACTIVE,
            case_template_id, created_by_user_id, created_by_citizen,
            initiator_citizen_id,
            filing_department_id, owner_sector_id
        )

        movement_id = str(uuid.uuid4())

        movement_insert = """
            INSERT INTO case_movements (
                id, case_id, type, user_id, citizen_id,
                creator_sector_id, admin_sector_id,
                assigned_sector_id, assigned_user_id,
                reason, is_active
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
            )
        """

        await connection.execute(
            movement_insert,
            movement_id, case_id, MOVEMENT_TYPES["CREATION"], created_by_user_id, created_by_citizen,
            creator_sector_id, owner_sector_id,
            None, None,
            f"Creación del expediente: {reference}", True
        )

        close_creation_query = """
            UPDATE case_movements
            SET closed_at = NOW(), closing_reason = $1, is_active = false
            WHERE id = $2
        """
        await connection.execute(close_creation_query, "Expediente creado", movement_id)

        if template_is_reserved and not is_citizen_actor:
            from services.cases.responsibles import add_responsible
            await add_responsible(
                case_id=case_id,
                user_id=created_by_user_id,
                responsible_type="ADMIN",
                sector_id=owner_sector_id,
                added_by=created_by_user_id,
                movement_reason="Auto-asignacion del creador como responsable administrador al crear expediente reservado",
                schema_name=schema_name,
                conn=connection,
            )


        return {
            "case_id": case_id,
            "case_number": case_number,
            "reference": reference,
            "status": CASE_STATUS_INACTIVE,
            "created_at": datetime.now()
        }

    except (ValueError, ValidationError, TransientLookupError, DatabaseBusyError):
        raise
    except Exception as e:
        logger.error(f"Error creando expediente: {str(e)}")
        raise BusinessLogicError(f"Error creando expediente: {str(e)}")
