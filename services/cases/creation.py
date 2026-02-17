"""Servicio de creación de expedientes con carátulas"""

from shared.logging import get_logger
from typing import Dict, Any, Optional
from database import get_db_connection
from shared.exceptions import ValidationError, NotFoundError
from services.cases.cover_creator import create_case_cover
from services.cases.validation import (
    validate_and_get_user,
    validate_and_get_template
)
from config.constants import CASE_CREATION_ERROR

logger = get_logger(__name__)


async def create_case_with_cover_service(
    case_template_id: str,
    reference: str,
    user_id: str,
    owner_sector_id: Optional[str] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """
    Crea expediente con carátula automática en transacción atómica.
    Valida usuario y template, crea expediente, genera carátula y vincula.
    Si falla cualquier paso, hace rollback automático.
    """
    logger.info(f"Creating case - Template: {case_template_id[:8]}, User: {user_id[:8]}")

    # Lazy import para evitar importación circular
    from services.case_service import CaseService

    with get_db_connection(schema_name) as connection:
        try:
            logger.info("Validating user...")
            user_data = validate_and_get_user(connection, user_id)
            logger.info(f"User validated: {user_data['full_name']}")

            logger.info("Validating template...")
            template_data = validate_and_get_template(connection, case_template_id)
            logger.info(f"Template validated: {template_data['type_name']}")

            final_owner_sector_id = owner_sector_id or user_data['sector_id']

            logger.info("Creating case...")
            case_result = CaseService.create_case(
                connection=connection,
                case_template_id=case_template_id,
                reference=reference.strip(),
                created_by_user_id=user_id,
                filing_department_id=template_data['filing_department_id'],
                creator_sector_id=user_data['sector_id'],
                owner_sector_id=final_owner_sector_id,
                schema_name=schema_name
            )

            logger.info(f"Case created: {case_result['case_number']}")

            logger.info("Creating case cover...")
            cover_result = await create_case_cover(
                case_id=case_result['case_id'],
                case_number=case_result['case_number'],
                case_reference=reference.strip(),
                case_template_acronym=template_data['acronym'],
                case_template_name=template_data['type_name'],
                filing_department_id=template_data['filing_department_id'],
                user_id=user_id,
                schema_name=schema_name,
                connection=connection
            )

            logger.info(f"Cover created: {cover_result['official_number']}")

            connection.commit()
            logger.info(f"Transaction committed - Case: {case_result['case_number']}, Cover: {cover_result['official_number']}")

            # Retornar resultado completo
            return {
                "case": case_result,
                "cover": cover_result,
                "template": {
                    "id": template_data['id'],
                    "name": template_data['type_name'],
                    "acronym": template_data['acronym']
                },
                "created_by": user_data['full_name']
                }

        except (ValidationError, NotFoundError) as e:
            connection.rollback()
            logger.error(f"Validation error creating case: {str(e)}")
            raise

        except Exception as e:
            connection.rollback()
            logger.error(f"Unexpected error creating case: {str(e)}", exc_info=True)
            raise Exception(CASE_CREATION_ERROR.format(error=str(e)))
