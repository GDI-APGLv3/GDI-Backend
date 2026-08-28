
from typing import Dict, Any, Optional
from database import fetch_all
from shared.logging import get_logger

logger = get_logger(__name__)

_HEADER_QUERY = """
    SELECT
        nr.recipient_type,
        d.acronym as dept_acronym,
        s.acronym as sector_acronym
    FROM notes_recipients nr
    JOIN sectors s ON s.id = nr.sector_id
    JOIN departments d ON d.id = s.department_id
    WHERE nr.document_id = $1
      AND nr.recipient_type != 'BCC'
    ORDER BY
        CASE nr.recipient_type
            WHEN 'TO' THEN 1
            WHEN 'CC' THEN 2
        END,
        d.acronym, s.acronym
"""


async def build_nota_header_html(document_id: str, *, schema_name: str) -> str:
    rows = await fetch_all(_HEADER_QUERY, document_id, schema_name=schema_name)

    if not rows:
        logger.debug(f"[{schema_name}] NOTA {document_id[:8]}... sin recipients, header vacío")
        return ""

    recipients_by_type: Dict[str, list] = {"TO": [], "CC": []}
    for row in rows:
        key = f"{row['dept_acronym']}#{row['sector_acronym']}"
        recipients_by_type[row["recipient_type"]].append(key)

    lines = []

    if recipients_by_type["TO"]:
        to_list = ", ".join(recipients_by_type["TO"])
        lines.append(f'<p style="margin: 2px 0; font-size: 13px;"><strong>PARA:</strong> {to_list}</p>')

    if recipients_by_type["CC"]:
        cc_list = ", ".join(recipients_by_type["CC"])
        lines.append(f'<p style="margin: 2px 0; font-size: 13px;"><strong>CC:</strong> {cc_list}</p>')

    lines.append('<hr style="border: 1px dashed #999; margin: 8px 0 15px 0;">')

    header_html = "\n".join(lines)

    logger.info(
        f"[{schema_name}] Header NOTA construido: "
        f"TO={len(recipients_by_type['TO'])}, CC={len(recipients_by_type['CC'])}"
    )
    return header_html


def inject_header_into_content(header: str, content: Optional[Dict | str]) -> Dict[str, Any]:
    import json

    if not header:
        if isinstance(content, dict):
            return content
        elif isinstance(content, str):
            try:
                return json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return {"html": content if content else ""}
        else:
            return {"html": ""}

    if isinstance(content, dict):
        html = content.get("html", "")
    elif isinstance(content, str):
        try:
            parsed = json.loads(content)
            html = parsed.get("html", "") if isinstance(parsed, dict) else content
        except (json.JSONDecodeError, TypeError):
            html = content if content else ""
    else:
        html = ""

    return {"html": header + "\n" + html}


def remove_existing_header(content: dict | str) -> str:
    import re

    if isinstance(content, dict):
        html = content.get("html", "")
    else:
        html = str(content) if content else ""

    header_pattern = (
        r'^(<p><strong>PARA:</strong>.*?</p>\s*)?'
        r'(<p><strong>CC:</strong>.*?</p>\s*)?'
        r'(<hr style="border: 1px dashed[^>]*>)\s*'
    )
    html_clean = re.sub(header_pattern, "", html, flags=re.DOTALL)
    return html_clean.strip()
