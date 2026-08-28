
from shared.logging import get_logger
from typing import Dict, Any, Optional
from database import transaction, execute
from config.constants import CASE_STATUS_ACTIVE
from shared.exceptions import (
    ValidationError,
    NotFoundError,
    BusinessLogicError,
    DatabaseBusyError,
    TransientLookupError,
)
from services.cases.cover_creator import create_case_cover
from services.cases.citizen_shares import share_case_with_citizen
from services.cases.validation import (
    validate_and_get_user,
    validate_and_get_template,
    validate_and_get_citizen,
    validate_creation_channel_for_citizen,
    validate_owner_sector_belongs_to_department,
)

logger = get_logger(__name__)


async def create_case_with_cover_service(
    case_template_id: str,
    reference: str,
    user_id: Optional[str] = None,
    citizen_id: Optional[str] = None,
    owner_sector_id: Optional[str] = None,
    initiator_citizen_id: Optional[str] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    if bool(user_id) == bool(citizen_id):
        raise ValidationError("Debe proveerse exactamente uno de user_id o citizen_id")
    is_citizen_actor = citizen_id is not None

    if initiator_citizen_id and is_citizen_actor:
        raise ValidationError(
            "initiator_citizen_id solo aplica cuando el expediente lo crea un usuario interno"
        )

    logger.info(
        f"Creating case - Template: {case_template_id[:8]}, "
        f"{'Citizen' if is_citizen_actor else 'User'}: {(citizen_id or user_id)[:8]}"
    )

    from services.case_service import CaseService

    logger.info("MOMENTO 1: Validando y creando expediente como inactive...")

    try:
        async with transaction(
            schema_name=schema_name,
            user_id=citizen_id if is_citizen_actor else user_id,
            auth_source="tad" if is_citizen_actor else "jwt",
        ) as conn:
            logger.info("Validating template...")
            template_data = await validate_and_get_template(conn, case_template_id)
            logger.info(f"Template validated: {template_data['type_name']}")

            if is_citizen_actor:
                validate_creation_channel_for_citizen(template_data)

                logger.info("Validating citizen...")
                actor_data = await validate_and_get_citizen(conn, citizen_id)
                logger.info(f"Citizen validated: {actor_data['full_name']}")

                creator_sector_id = None
            else:
                logger.info("Validating user...")
                actor_data = await validate_and_get_user(conn, user_id)
                logger.info(f"User validated: {actor_data['full_name']}")

                creator_sector_id = actor_data['sector_id']

            if initiator_citizen_id:
                logger.info("Validating initiator citizen...")
                initiator_data = await validate_and_get_citizen(conn, initiator_citizen_id)
                if initiator_data['estado'] == 'bloqueado':
                    raise ValidationError(
                        "El ciudadano iniciador está bloqueado y no puede iniciar expedientes"
                    )
                logger.info(f"Initiator citizen validated: {initiator_data['full_name']}")

            if owner_sector_id is not None:
                final_owner_sector_id = owner_sector_id
                sector_from_template_default = False
            elif is_citizen_actor:
                final_owner_sector_id = template_data['filing_sector_id']
                sector_from_template_default = True
            else:
                final_owner_sector_id = creator_sector_id or template_data['filing_sector_id']
                sector_from_template_default = creator_sector_id is None

            if sector_from_template_default:
                await validate_owner_sector_belongs_to_department(
                    conn, final_owner_sector_id, template_data['filing_department_id']
                )

            logger.info("Creating case (inactive)...")
            case_result = await CaseService.create_case(
                conn,
                case_template_id=case_template_id,
                reference=reference.strip(),
                filing_department_id=template_data['filing_department_id'],
                created_by_user_id=user_id,
                created_by_citizen=citizen_id,
                initiator_citizen_id=initiator_citizen_id,
                creator_sector_id=creator_sector_id,
                owner_sector_id=final_owner_sector_id,
                schema_name=schema_name
            )
            logger.info(f"Case created (inactive): {case_result['case_number']}")

            share_with_citizen_id = citizen_id if is_citizen_actor else initiator_citizen_id
            if share_with_citizen_id:
                await share_case_with_citizen(
                    case_id=case_result['case_id'],
                    citizen_id=share_with_citizen_id,
                    shared_by=None,
                    schema_name=schema_name,
                    conn=conn,
                )
                logger.info(f"Case auto-shared with citizen {share_with_citizen_id[:8]}")


    except (
        ValidationError,
        NotFoundError,
        BusinessLogicError,
        DatabaseBusyError,
        TransientLookupError,
        ValueError,
    ):
        raise
    except Exception as e:
        logger.critical(
            f"Error inesperado en MOMENTO 1 (validacion/creacion): "
            f"schema={schema_name}, actor={(citizen_id or user_id)[:8]}, template={case_template_id[:8]}, "
            f"error={e}",
            exc_info=True
        )
        try:
            from shared.alerts import send_alert_mail
            await send_alert_mail(
                subject=f"[GDI ALERTA] Error inesperado en MOMENTO 1 - creacion de expediente",
                body=(
                    f"Error inesperado durante validacion o creacion del expediente.\n"
                    f"Schema: {schema_name}\n"
                    f"{'Citizen ID' if is_citizen_actor else 'User ID'}: {citizen_id or user_id}\n"
                    f"Template ID: {case_template_id}\n"
                    f"Error: {e}\n"
                ),
                schema_name=schema_name
            )
        except Exception:
            logger.warning("shared.alerts no disponible - alerta solo en logs")
        raise

    logger.info("MOMENTO 2: Creando CAEX (1 intento)...")

    try:
        cover_result = await create_case_cover(
            case_id=case_result['case_id'],
            case_number=case_result['case_number'],
            case_reference=reference.strip(),
            case_template_acronym=template_data['acronym'],
            case_template_name=template_data['type_name'],
            filing_department_id=template_data['filing_department_id'],
            user_id=user_id,
            citizen_id=citizen_id,
            schema_name=schema_name
        )
    except Exception as e:
        logger.critical(
            f"CAEX FALLIDO: case={case_result['case_number']}, "
            f"case_id={case_result['case_id']}, schema={schema_name}, "
            f"error={e}"
        )
        logger.critical(
            f"Expediente {case_result['case_id']} queda inactive en BD. "
            f"El número {case_result['case_number']} está reservado."
        )

        try:
            from shared.alerts import send_alert_mail
            await send_alert_mail(
                subject=f"[GDI ALERTA] CAEX fallido - {case_result['case_number']}",
                body=(
                    f"Expediente {case_result['case_id']} quedó inactive.\n"
                    f"Número de case: {case_result['case_number']}\n"
                    f"Error: {e}\n"
                    f"Schema: {schema_name}\n"
                    f"El expediente queda inactive en BD (no se hizo rollback)."
                ),
                schema_name=schema_name
            )
        except ImportError:
            logger.warning("shared.alerts no disponible aún - alerta solo en logs")

        raise Exception("Error al crear expediente, intente mas tarde")

    logger.info(f"CAEX creado: {cover_result['official_number']} - activando expediente...")
    await execute(
        "UPDATE cases SET status = $1 WHERE id = $2",
        CASE_STATUS_ACTIVE, case_result['case_id'],
        schema_name=schema_name
    )
    logger.info(f"Expediente activado: {case_result['case_number']}")

    return {
        "case": case_result,
        "cover": cover_result,
        "template": {
            "id": template_data['id'],
            "name": template_data['type_name'],
            "acronym": template_data['acronym']
        },
        "created_by": actor_data['full_name']
    }
