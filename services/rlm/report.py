"""
Servicio para generacion de informes IFRLM (Informe de Legajo).

El IFRLM es un documento oficial que contiene un snapshot del legajo
en un momento dado. Similar al CAEX para expedientes.

- Se genera automaticamente al crear un legajo (informe inicial)
- Se puede generar on-demand cuando el usuario necesita un snapshot actualizado

NOTA: El document_type "IFRLM" debe existir en la BD con is_active=true.
Si no existe, la generacion fallara con un error claro indicando que se necesita
crear el tipo de documento en la BD.

Pipeline: create_and_sign_case_document() -> PDFComposer /ifrlm/ -> Notary -> R2
"""

import html
from datetime import datetime
from typing import Dict, Any, Optional, List

from shared.logging import get_logger
from database import fetch_all, fetch_one, transaction
from shared.exceptions import ValidationError, NotFoundError, ExternalServiceError, AuthorizationError
from services.rlm.permissions import check_permission
from services.rlm.queries import (
    get_record_detail_query,
    get_record_documents_query,
    get_record_cases_query,
    get_user_sector_info_query,
)

logger = get_logger(__name__)

# Acronimo del tipo de documento IFRLM (debe existir en BD con is_active=true)
IFRLM_DOCUMENT_TYPE = "IFRLM"


async def generate_ifrlm(
    record_id: str,
    user_id: str,
    *,
    schema_name: str,
    notes: str = None,
    is_initial: bool = False,
) -> Dict[str, Any]:
    """
    Genera un informe IFRLM para un legajo.

    Flujo:
    1. Obtener datos actuales del legajo (campos JSONB, docs vinculados, cases vinculados)
    2. Construir snapshot HTML
    3. Llamar a create_and_sign_case_document() que ejecuta el pipeline completo:
       - Crear documento draft
       - Numerar con advisory lock
       - PDFComposer /ifrlm/ -> Notary -> R2
       - Actualizar a 'signed'
    4. Vincular IFRLM al legajo (record_document_links)
    5. Registrar en historial: action=ifrlm_generated

    Args:
        record_id: UUID del legajo
        user_id: UUID del usuario que genera el informe
        schema_name: Schema del tenant
        notes: Notas opcionales para la vinculacion
        is_initial: True si es el informe inicial (generado al crear el legajo)

    Returns:
        Dict con document_id, official_number y datos del informe creado

    Raises:
        NotFoundError: Si el legajo no existe
        ValidationError: Si el tipo IFRLM no existe en BD
        ExternalServiceError: Si falla la creacion del documento o el pipeline PDF
        AuthorizationError: Si el usuario no tiene permisos
    """
    logger.info(f"Generando IFRLM para record {record_id[:8]} por user {user_id[:8]}")

    # 1. Obtener datos completos del legajo
    record = await fetch_one(
        get_record_detail_query(),
        record_id,
        schema_name=schema_name,
    )

    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    # 1b. Verificar permisos del usuario sobre este legajo
    if not await check_permission(record["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para generar informes de este legajo")

    # 2. Obtener documentos vinculados (hasta 100 para el snapshot)
    linked_docs = await fetch_all(
        get_record_documents_query(),
        record_id, 100, 0,
        schema_name=schema_name,
    ) or []

    # 3. Obtener expedientes vinculados (hasta 100 para el snapshot)
    linked_cases = await fetch_all(
        get_record_cases_query(),
        record_id, 100, 0,
        schema_name=schema_name,
    ) or []

    # 4. Obtener info del usuario generador
    user_info = await fetch_one(
        get_user_sector_info_query(),
        user_id,
        schema_name=schema_name,
    )

    # 5. Construir snapshot
    snapshot_data = _build_snapshot_data(
        record=record,
        linked_docs=linked_docs,
        linked_cases=linked_cases,
        user_info=user_info,
    )

    link_notes = notes or ("Informe inicial" if is_initial else "Informe de legajo")
    reference = f"Informe de legajo {record['record_number']}"

    # 6. Obtener datos del firmante y settings del tenant
    from services.shared.signer_data import get_signer_data
    from services.shared.settings_utils import get_city_from_settings, get_logo_url
    from config.constants import DEFAULT_LOGO_URL

    signer_data = await get_signer_data(user_id, schema_name=schema_name)
    city = await get_city_from_settings(schema_name=schema_name)
    logo_url = await get_logo_url(schema_name=schema_name)

    # 7. Definir builders para create_and_sign_case_document
    def html_builder():
        return _build_ifrlm_html(snapshot_data)

    def payload_builder(document_id, document_type_name, official_number, user_id):
        return {
            "municipality_logo_url": logo_url,
            "document_type_acronym": IFRLM_DOCUMENT_TYPE,
            "document_type_name": document_type_name,
            "document_reference": reference,
            "record_number": record["record_number"],
            "registry_name": record.get("registry_name", ""),
            "state": record["state"],
            "snapshot_html": _build_ifrlm_html(snapshot_data),
            "signer_full_name": signer_data["full_name"],
            "signer_seal": signer_data["seal"],
            "signer_department": signer_data["department_name"],
            "signer_municipality": signer_data["municipality_name"],
            "official_document_number": official_number,
            "city_name": city,
        }

    # 8. Ejecutar pipeline completo via create_and_sign_case_document
    from services.cases._document_creator_base import create_and_sign_case_document

    result = await create_and_sign_case_document(
        document_type_acronym=IFRLM_DOCUMENT_TYPE,
        reference=reference,
        html_builder=html_builder,
        payload_builder=payload_builder,
        orchestrator_endpoint="/create-ifrlm",
        case_id=record_id,
        user_id=user_id,
        schema_name=schema_name,
    )

    document_id = result["document_id"]

    # 9-10. Vincular IFRLM + registrar historial (atómico)
    from services.rlm.queries import insert_document_link_query, insert_history_query

    after_data = {
        "document_id": document_id,
        "official_number": result.get("official_number"),
        "is_initial": is_initial,
        "notes": link_notes,
    }
    sector_id = user_info.get("sector_id") if user_info else None

    async with transaction(schema_name=schema_name, user_id=user_id) as conn:
        await conn.execute(
            insert_document_link_query(),
            record_id, document_id, None, link_notes, user_id,
        )
        await conn.execute(
            insert_history_query(),
            record_id, "ifrlm_generated", None, None, after_data, user_id, sector_id,
        )

    logger.info(
        f"IFRLM generado exitosamente: document_id={document_id[:8]}, "
        f"official_number={result.get('official_number')}, "
        f"record={record['record_number']}, initial={is_initial}"
    )

    return {
        "document_id": document_id,
        "official_number": result.get("official_number"),
        "record_id": record_id,
        "record_number": record["record_number"],
        "report_type": IFRLM_DOCUMENT_TYPE,
        "is_initial": is_initial,
        "notes": link_notes,
        "generated_at": datetime.now().isoformat(),
        "generated_by": user_info.get("full_name") if user_info else None,
    }


# ============================================================================
# FUNCIONES AUXILIARES
# ============================================================================

def _build_snapshot_data(
    record: dict,
    linked_docs: list,
    linked_cases: list,
    user_info: Optional[dict],
) -> Dict[str, Any]:
    """
    Construye el snapshot de datos del legajo para el informe.

    Args:
        record: Datos del legajo (de get_record_detail_query)
        linked_docs: Lista de documentos vinculados
        linked_cases: Lista de expedientes vinculados
        user_info: Info del usuario generador

    Returns:
        Dict con datos estructurados para el informe
    """
    now = datetime.now()

    # Datos del legajo
    record_data = record.get("data") or {}

    # Preparar lista de documentos vinculados
    docs_list = []
    for doc in linked_docs:
        docs_list.append({
            "document_id": doc.get("document_id"),
            "official_number": doc.get("official_number"),
            "reference": doc.get("reference"),
            "document_type": doc.get("document_type"),
            "field_name": doc.get("field_name"),
            "notes": doc.get("notes"),
        })

    # Preparar lista de expedientes vinculados
    cases_list = []
    for case in linked_cases:
        cases_list.append({
            "case_id": case.get("case_id"),
            "case_number": case.get("case_number"),
            "case_reference": case.get("case_reference"),
            "notes": case.get("notes"),
        })

    return {
        "record_number": record["record_number"],
        "display_name": record.get("display_name", ""),
        "state": record["state"],
        "registry_code": record.get("registry_code"),
        "registry_name": record.get("registry_name"),
        "data": record_data,
        "data_schema": record.get("data_schema"),
        "created_at": str(record.get("created_at")),
        "created_by_name": record.get("created_by_name"),
        "created_by_sector": record.get("created_by_sector"),
        "created_by_department": record.get("created_by_department"),
        "linked_documents": docs_list,
        "linked_cases": cases_list,
        "generated_at": now.strftime("%d/%m/%Y %H:%M"),
        "generated_at_iso": now.isoformat(),
        "generated_by_name": user_info.get("full_name") if user_info else "Sistema",
        "generated_by_sector": user_info.get("sector_acronym") if user_info else None,
    }


def _build_ifrlm_html(snapshot: Dict[str, Any]) -> str:
    """
    Construye el HTML del informe IFRLM.

    Args:
        snapshot: Datos del snapshot del legajo

    Returns:
        String HTML del informe
    """
    # Construir seccion de campos del legajo
    fields_html = ""
    data = snapshot.get("data") or {}
    data_schema = snapshot.get("data_schema") or {}
    field_definitions = data_schema or {}

    for field_name, field_value in data.items():
        # Intentar obtener el label del campo desde el schema
        field_def = field_definitions.get(field_name, {})
        label = html.escape(str(field_def.get("label", field_name)))

        # Formatear el valor (ya escapado internamente)
        display_value = _format_field_value(field_value)

        fields_html += f"""
            <tr>
                <td style="padding: 6px 12px; font-weight: bold; border-bottom: 1px solid #eee; width: 40%;">{label}</td>
                <td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{display_value}</td>
            </tr>
        """

    if not fields_html:
        fields_html = """
            <tr>
                <td colspan="2" style="padding: 6px 12px; color: #999;">Sin datos registrados</td>
            </tr>
        """

    # Construir seccion de documentos vinculados
    docs_html = ""
    for doc in snapshot.get("linked_documents", []):
        official_num = html.escape(str(doc.get("official_number") or "Sin numerar"))
        doc_type = html.escape(str(doc.get("document_type") or ""))
        reference = html.escape(str(doc.get("reference") or ""))
        doc_display = f"{doc_type}-{official_num}" if doc_type else official_num
        if reference:
            doc_display += f" ({reference})"
        docs_html += f"<li>{doc_display}</li>"

    if not docs_html:
        docs_html = "<li style='color: #999;'>Sin documentos vinculados</li>"

    # Construir seccion de expedientes vinculados
    cases_html = ""
    for case in snapshot.get("linked_cases", []):
        case_num = html.escape(str(case.get("case_number") or "Sin numero"))
        case_ref = html.escape(str(case.get("case_reference") or ""))
        case_display = case_num
        if case_ref:
            case_display += f" ({case_ref})"
        cases_html += f"<li>{case_display}</li>"

    if not cases_html:
        cases_html = "<li style='color: #999;'>Sin expedientes vinculados</li>"

    record_number_esc = html.escape(str(snapshot['record_number']))
    display_name_esc = html.escape(str(snapshot.get('display_name', '')))
    registry_name_esc = html.escape(str(snapshot.get('registry_name', '')))
    registry_code_esc = html.escape(str(snapshot.get('registry_code', '')))
    state_esc = html.escape(str(snapshot['state']))
    generated_at_esc = html.escape(str(snapshot['generated_at']))
    generated_by_esc = html.escape(str(snapshot['generated_by_name']))
    generated_by_sector_esc = html.escape(str(snapshot.get('generated_by_sector') or ''))

    html_content = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; max-width: 800px;">
        <h1 style="text-align: center; color: #333; margin-bottom: 5px;">INFORME DE LEGAJO</h1>
        <p style="text-align: center; color: #666; margin-top: 0;">{record_number_esc}</p>
        <hr style="border: 2px solid #333; margin: 15px 0;">

        <div style="margin: 15px 0; background: #f8f8f8; padding: 12px; border-radius: 4px;">
            <p style="margin: 4px 0;"><strong>Legajo:</strong> {display_name_esc} ({record_number_esc})</p>
            <p style="margin: 4px 0;"><strong>Familia:</strong> {registry_name_esc} ({registry_code_esc})</p>
            <p style="margin: 4px 0;"><strong>Estado:</strong> {state_esc}</p>
            <p style="margin: 4px 0;"><strong>Fecha informe:</strong> {generated_at_esc}</p>
        </div>

        <h2 style="color: #333; border-bottom: 1px solid #ccc; padding-bottom: 5px;">Datos del Legajo</h2>
        <table style="width: 100%; border-collapse: collapse;">
            {fields_html}
        </table>

        <h2 style="color: #333; border-bottom: 1px solid #ccc; padding-bottom: 5px;">Documentos Vinculados</h2>
        <ul style="line-height: 1.8;">
            {docs_html}
        </ul>

        <h2 style="color: #333; border-bottom: 1px solid #ccc; padding-bottom: 5px;">Expedientes Vinculados</h2>
        <ul style="line-height: 1.8;">
            {cases_html}
        </ul>

        <hr style="border: 1px solid #ccc; margin: 20px 0;">
        <p style="color: #666; font-size: 12px;">
            Generado el {generated_at_esc} por {generated_by_esc}
            {(' - ' + generated_by_sector_esc) if generated_by_sector_esc else ''}
        </p>
    </div>
    """

    return html_content


def _format_field_value(value: Any) -> str:
    """
    Formatea un valor de campo enriquecido para mostrar en HTML.

    Los campos enriquecidos pueden ser:
    - Strings simples
    - Dicts con {value, expiration_date, document_id, notes, verified_by, verified_at}
    - Otros tipos (numeros, booleanos, listas)

    Args:
        value: Valor del campo

    Returns:
        String formateado para HTML
    """
    if value is None:
        return "<span style='color: #999;'>-</span>"

    if isinstance(value, dict):
        # Campo enriquecido
        parts = []
        actual_value = value.get("value", "")
        if actual_value is not None:
            parts.append(html.escape(str(actual_value)))

        exp_date = value.get("expiration_date")
        if exp_date:
            parts.append(f"<span style='color: #666; font-size: 11px;'>(vence {html.escape(str(exp_date))})</span>")

        verified_by = value.get("verified_by_name") or value.get("verified_by")
        if verified_by:
            verified_at = value.get("verified_at", "")
            parts.append(
                f"<br><span style='color: green; font-size: 11px;'>"
                f"Verificado por {html.escape(str(verified_by))}"
                f"{(' el ' + html.escape(str(verified_at)[:10])) if verified_at else ''}"
                f"</span>"
            )

        notes = value.get("notes")
        if notes:
            parts.append(f"<br><span style='color: #888; font-size: 11px;'>Nota: {html.escape(str(notes))}</span>")

        return " ".join(parts) if parts else "-"

    if isinstance(value, list):
        return ", ".join(html.escape(str(v)) for v in value) if value else "-"

    return html.escape(str(value))
