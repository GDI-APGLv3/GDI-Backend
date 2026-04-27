"""
Construccion de header de destinatarios para documentos MEMO.

Al oficializar un MEMO, el contenido HTML debe incluir los destinatarios al inicio:

PARA: Nombre (Sector), Nombre (Sector)
CC: Nombre (Sector)
----------------------------------------------
[Contenido del memo...]

El header se inyecta SOLO al momento de firmar (oficializar), cuando los recipients son finales.
BCC (CCO) NO se incluye en el header porque es oculto.

Diferencias clave con NOTAS:
- Formato "Nombre (Sector)" en vez de "DEPT#SECTOR"
- JOIN con users + sectors en vez de sectors + departments
"""

from typing import Dict, Any, Optional
from database import get_db_connection
from shared.logging import get_logger

logger = get_logger(__name__)


def build_memo_header_html(document_id: str, *, schema_name: str) -> str:
    """
    Construye el header HTML con destinatarios para documentos MEMO.

    Retorna HTML con formato:
    <p style="margin: 2px 0; font-size: 13px;"><strong>PARA:</strong> Nombre (Sector)</p>
    <p style="margin: 2px 0; font-size: 13px;"><strong>CC:</strong> Nombre (Sector)</p>
    <hr style="border: 1px dashed #999; margin: 8px 0 15px 0;">

    Args:
        document_id: UUID del documento MEMO (document_draft.id)
        schema_name: Schema del tenant

    Returns:
        HTML del header o string vacio si no hay recipients

    Nota: BCC/CCO NO se incluye (es oculto para recipients).
    """
    query = """
        SELECT
            mr.recipient_type,
            u.full_name as recipient_name,
            s.acronym as sector_acronym
        FROM memo_recipients mr
        JOIN users u ON u.id = mr.recipient_user_id
        LEFT JOIN sectors s ON s.id = mr.recipient_sector_id
        WHERE mr.document_id = %s
          AND mr.recipient_type != 'BCC'
        ORDER BY
            CASE mr.recipient_type
                WHEN 'TO' THEN 1
                WHEN 'CC' THEN 2
            END,
            u.full_name
    """

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (document_id,))
            rows = cursor.fetchall()

    if not rows:
        logger.debug(f"[{schema_name}] MEMO {document_id[:8]}... sin recipients, header vacio")
        return ""

    # Agrupar por tipo
    recipients_by_type: Dict[str, list] = {'TO': [], 'CC': []}
    for row in rows:
        name = row['recipient_name']
        sector = row['sector_acronym']
        # Formato: "Nombre (Sector)" o solo "Nombre" si no hay sector
        key = f"{name} ({sector})" if sector else name
        recipients_by_type[row['recipient_type']].append(key)

    # Construir HTML
    lines = []

    if recipients_by_type['TO']:
        to_list = ', '.join(recipients_by_type['TO'])
        lines.append(f'<p style="margin: 2px 0; font-size: 13px;"><strong>PARA:</strong> {to_list}</p>')

    if recipients_by_type['CC']:
        cc_list = ', '.join(recipients_by_type['CC'])
        lines.append(f'<p style="margin: 2px 0; font-size: 13px;"><strong>CC:</strong> {cc_list}</p>')

    # Linea separadora
    lines.append('<hr style="border: 1px dashed #999; margin: 8px 0 15px 0;">')

    header_html = '\n'.join(lines)

    logger.info(
        f"[{schema_name}] Header MEMO construido: "
        f"TO={len(recipients_by_type['TO'])}, CC={len(recipients_by_type['CC'])}"
    )

    return header_html


def inject_header_into_content(header: str, content: Optional[Dict | str]) -> Dict[str, Any]:
    """
    Inyecta el header al inicio del contenido HTML.

    Args:
        header: HTML del header de destinatarios
        content: Contenido original (dict con 'html' o string JSON)

    Returns:
        Dict con {'html': header + contenido_original}
    """
    import json

    # Si no hay header, devolver contenido como esta
    if not header:
        if isinstance(content, dict):
            return content
        elif isinstance(content, str):
            try:
                return json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return {'html': content if content else ''}
        else:
            return {'html': ''}

    # Extraer HTML actual
    if isinstance(content, dict):
        html = content.get('html', '')
    elif isinstance(content, str):
        try:
            parsed = json.loads(content)
            html = parsed.get('html', '') if isinstance(parsed, dict) else content
        except (json.JSONDecodeError, TypeError):
            html = content if content else ''
    else:
        html = ''

    # Concatenar header + contenido
    new_html = header + '\n' + html

    return {'html': new_html}


def remove_existing_header(content: dict | str) -> str:
    """
    Remueve header de destinatarios existente del contenido.

    Detecta y remueve el patron:
    <p><strong>PARA:</strong>...</p>
    <p><strong>CC:</strong>...</p>
    <hr style="border: 1px dashed #999;...">

    Util para re-envio despues de rechazo.

    Args:
        content: Dict con 'html' key o string HTML

    Returns:
        HTML limpio sin header de destinatarios
    """
    import re

    if isinstance(content, dict):
        html = content.get('html', '')
    else:
        html = str(content) if content else ''

    # Patron para detectar header existente (PARA y/o CC seguido de hr dashed)
    header_pattern = r'^(<p[^>]*><strong>PARA:</strong>.*?</p>\s*)?(<p[^>]*><strong>CC:</strong>.*?</p>\s*)?(<hr style="border: 1px dashed[^>]*>)\s*'

    # Remover header si existe
    html_clean = re.sub(header_pattern, '', html, flags=re.DOTALL)

    return html_clean.strip()
