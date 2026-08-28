
from typing import Dict, Any
from datetime import datetime

from database import get_conn
from shared.exceptions import ValidationError
from shared.logging import get_logger
from services.cases._document_creator_base import create_and_sign_case_document
from services.shared.signer_data import get_signer_data
from services.shared.settings_utils import get_city_from_settings
from config.constants import DEFAULT_LOGO_URL

logger = get_logger(__name__)


async def create_transfer_document(
    case_id: str,
    case_number: str,
    movement_type: str,
    movement_reason: str,
    requesting_sector_id: str,
    receiving_sector_id: str,
    user_id: str,
    *,
    schema_name: str,
    connection=None
) -> Dict[str, Any]:
    logger.info(f"Iniciando creación de pase {movement_type}")
    logger.info(f"Case: {case_number}")
    logger.info(f"Motivo: {movement_reason[:50]}...")

    transfer_data = await _fetch_transfer_data(
        case_id=case_id,
        case_number=case_number,
        movement_reason=movement_reason,
        requesting_sector_id=requesting_sector_id,
        receiving_sector_id=receiving_sector_id,
        user_id=user_id,
        schema_name=schema_name
    )

    def html_builder():
        return _build_transfer_html(
            case_number=case_number,
            movement_type=movement_type,
            movement_reason=movement_reason,
            requesting_area=transfer_data['requesting_area'],
            receiving_area=transfer_data['receiving_area']
        )

    def payload_builder(document_id, document_type_name, official_number, user_id):
        return _build_transfer_payload(
            case_number=case_number,
            document_type_name=document_type_name,
            official_number=official_number,
            movement_type=movement_type,
            movement_reason=movement_reason,
            transfer_data=transfer_data
        )

    result = await create_and_sign_case_document(
        document_type_acronym="PV",
        reference=f"{movement_type} {case_number}",
        html_builder=html_builder,
        payload_builder=payload_builder,
        orchestrator_endpoint="/create-transfer-document",
        case_id=case_id,
        user_id=user_id,
        schema_name=schema_name,
        connection=connection
    )

    logger.info(f"Pase creado: {result['official_number']}")

    return result


async def _fetch_transfer_data(
    case_id: str,
    case_number: str,
    movement_reason: str,
    requesting_sector_id: str,
    receiving_sector_id: str,
    user_id: str,
    *,
    schema_name: str
) -> Dict[str, Any]:
    logger.info("Obteniendo datos de BD...")

    signer_data = await get_signer_data(user_id, schema_name=schema_name)

    async with get_conn(schema_name=schema_name) as conn:
        requesting_query = """
            SELECT
                s.acronym as sector_acronym,
                dep.name as department_name,
                dep.acronym as department_acronym
            FROM sectors s
            JOIN departments dep ON s.department_id = dep.id
            WHERE s.id = $1
        """
        requesting_result = await conn.fetchrow(requesting_query, requesting_sector_id)

        if not requesting_result:
            raise ValidationError(f"Sector solicitante {requesting_sector_id} no encontrado")

        receiving_query = """
            SELECT
                s.acronym as sector_acronym,
                dep.name as department_name,
                dep.acronym as department_acronym
            FROM sectors s
            JOIN departments dep ON s.department_id = dep.id
            WHERE s.id = $1
        """
        receiving_result = await conn.fetchrow(receiving_query, receiving_sector_id)

        if not receiving_result:
            raise ValidationError(f"Sector receptor {receiving_sector_id} no encontrado")

        requesting_area = f"{requesting_result['sector_acronym']} - {requesting_result['department_name']}"
        receiving_area = f"{receiving_result['sector_acronym']} - {receiving_result['department_name']}"

        settings_result = await conn.fetchrow("SELECT logo_url FROM settings LIMIT 1")
        logo_url = settings_result['logo_url'] if settings_result and settings_result['logo_url'] else DEFAULT_LOGO_URL

        city = await get_city_from_settings(conn=conn, schema_name=schema_name)

    transfer_data = {
        "requesting_area": requesting_area,
        "receiving_area": receiving_area,

        "signer_full_name": signer_data['full_name'],
        "signer_seal": signer_data['seal'],
        "signer_department": signer_data['department_name'],
        "signer_municipality": signer_data['municipality_name'],

        "municipality_logo_url": logo_url,

        "city_name": city,

        "requesting_dept_acronym": requesting_result['department_acronym'],
        "receiving_dept_acronym": receiving_result['department_acronym']
    }

    logger.info("Datos obtenidos:")
    logger.info(f"  - Solicitante: {requesting_area}")
    logger.info(f"  - Receptor: {receiving_area}")
    logger.info(f"  - Firmante: {transfer_data['signer_full_name']}")

    return transfer_data


def _build_transfer_html(
    case_number: str,
    movement_type: str,
    movement_reason: str,
    requesting_area: str,
    receiving_area: str
) -> str:
    html = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px;">
        <h1 style="text-align: center; color: #333;">PASE ADMINISTRATIVO - {movement_type.upper()}</h1>
        <hr style="border: 2px solid #333; margin: 20px 0;">

        <div style="margin: 20px 0;">
            <p><strong>Expediente N°:</strong> {case_number}</p>
            <p><strong>Tipo de Operación:</strong> {movement_type}</p>
        </div>

        <div style="margin: 20px 0;">
            <p><strong>Área Solicitante:</strong> {requesting_area}</p>
            <p><strong>Área Receptora:</strong> {receiving_area}</p>
        </div>

        <div style="margin: 20px 0;">
            <p><strong>Motivo:</strong></p>
            <p style="margin-left: 20px; text-align: justify;">{movement_reason}</p>
        </div>

        <div style="margin: 20px 0;">
            <p><strong>Fecha:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
        </div>
    </div>
    """
    return html


def _build_transfer_payload(
    case_number: str,
    document_type_name: str,
    official_number: str,
    movement_type: str,
    movement_reason: str,
    transfer_data: Dict[str, Any]
) -> Dict[str, Any]:
    city = transfer_data.get('city_name', 'LATAM')

    payload = {
        "city_name": city,
        "document_reference": case_number,
        "document_type_acronym": "PV",
        "document_type_name": document_type_name,
        "movement_reason": movement_reason,
        "movement_type": movement_type,
        "municipality_logo_url": transfer_data.get('municipality_logo_url', DEFAULT_LOGO_URL),
        "receiving_area": transfer_data['receiving_area'],
        "requesting_area": transfer_data['requesting_area'],
        "signer_department": transfer_data['signer_department'],
        "signer_full_name": transfer_data['signer_full_name'],
        "signer_municipality": transfer_data['signer_municipality'],
        "signer_seal": transfer_data['signer_seal']
    }

    logger.info("Payload para PDFComposer/Notary:")
    logger.info(f"  - Case: {payload['document_reference']}")
    logger.info(f"  - Movement type: {payload['movement_type']}")
    logger.info(f"  - From: {payload['requesting_area']}")
    logger.info(f"  - To: {payload['receiving_area']}")

    return payload
