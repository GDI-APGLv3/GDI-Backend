"""Servicio de creación de expedientes con carátulas"""

from shared.logging import get_logger
from typing import Dict, Any, Optional
from database import get_db_connection
from config.constants import CASE_STATUS_ACTIVE
from shared.exceptions import ValidationError, NotFoundError
from services.cases.cover_creator import create_case_cover
from services.cases.validation import (
    validate_and_get_user,
    validate_and_get_template
)

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
    Crea expediente con carátula automática en dos momentos desacoplados.

    MOMENTO 1: Crear expediente como inactive (lock corto de numeración de case).
               Commit inmediato → el número de expediente queda asegurado.

    MOMENTO 2: Crear CAEX (sin lock, 1 intento).
               Si CAEX OK → activar expediente.
               Si CAEX falla → expediente queda inactive (número NO se pierde).
               El usuario recibe error; reintenta manualmente creando otro expediente.

    NOTA: No hay retry a este nivel. El retry interno de Notary (FULLPAGE) ya
    cubre fallos intermitentes de servicios externos. Un retry acá multiplicaría
    huérfanos en official_documents (cada intento fallido que llegó a
    generate_official_number deja una fila con signed_at=NULL).
    """
    logger.info(f"Creating case - Template: {case_template_id[:8]}, User: {user_id[:8]}")

    # Lazy import para evitar importación circular
    from services.case_service import CaseService

    # ================================================================
    # MOMENTO 1: Crear expediente como inactive (lock corto)
    # ================================================================
    logger.info("MOMENTO 1: Validando y creando expediente como inactive...")

    try:
        with get_db_connection(schema_name) as connection:
            logger.info("Validating user...")
            user_data = validate_and_get_user(connection, user_id)
            logger.info(f"User validated: {user_data['full_name']}")

            logger.info("Validating template...")
            template_data = validate_and_get_template(connection, case_template_id)
            logger.info(f"Template validated: {template_data['type_name']}")

        final_owner_sector_id = owner_sector_id or user_data['sector_id']

        with get_db_connection(schema_name) as connection:
            logger.info("Creating case (inactive)...")
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
            connection.commit()  # Expediente existe como inactive, número seguro
            logger.info(f"Case created (inactive): {case_result['case_number']}")

    except (ValidationError, NotFoundError):
        raise
    except Exception as e:
        logger.critical(
            f"Error inesperado en MOMENTO 1 (validacion/creacion): "
            f"schema={schema_name}, user={user_id[:8]}, template={case_template_id[:8]}, "
            f"error={e}",
            exc_info=True
        )
        # Mail de alerta (soft-fail)
        try:
            from shared.alerts import send_alert_mail
            await send_alert_mail(
                subject=f"[GDI ALERTA] Error inesperado en MOMENTO 1 - creacion de expediente",
                body=(
                    f"Error inesperado durante validacion o creacion del expediente.\n"
                    f"Schema: {schema_name}\n"
                    f"User ID: {user_id}\n"
                    f"Template ID: {case_template_id}\n"
                    f"Error: {e}\n"
                ),
                schema_name=schema_name
            )
        except Exception:
            logger.warning("shared.alerts no disponible - alerta solo en logs")
        raise

    # ================================================================
    # MOMENTO 2: Crear CAEX (sin lock, 1 intento)
    # ================================================================
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
            schema_name=schema_name
        )
    except Exception as e:
        # CAEX falló: expediente queda inactive, número NO se pierde
        logger.critical(
            f"CAEX FALLIDO: case={case_result['case_number']}, "
            f"case_id={case_result['case_id']}, schema={schema_name}, "
            f"error={e}"
        )
        logger.critical(
            f"Expediente {case_result['case_id']} queda inactive en BD. "
            f"El número {case_result['case_number']} está reservado."
        )

        # Mail de alerta (soft-fail)
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

    # CAEX OK: activar expediente
    logger.info(f"CAEX creado: {cover_result['official_number']} - activando expediente...")
    with get_db_connection(schema_name) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE cases SET status = %s WHERE id = %s",
            (CASE_STATUS_ACTIVE, case_result['case_id'])
        )
        conn.commit()
    logger.info(f"Expediente activado: {case_result['case_number']}")

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
