
from datetime import date, datetime


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_date_filter(
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    start: int = 1,
) -> tuple:
    if date_filter:
        mapping = {
            "hoy": "AND DATE(od.signed_at) = CURRENT_DATE",
            "ayer": "AND DATE(od.signed_at) = CURRENT_DATE - INTERVAL '1 day'",
            "ultimos_7_dias": "AND od.signed_at >= CURRENT_DATE - INTERVAL '7 days'",
            "ultimos_30_dias": "AND od.signed_at >= CURRENT_DATE - INTERVAL '30 days'",
        }
        clause = mapping.get(date_filter, "")
        return clause, []

    clauses = []
    params = []
    idx = start
    date_from_parsed = _parse_date(date_from) if date_from else None
    date_to_parsed = _parse_date(date_to) if date_to else None
    if date_from_parsed:
        clauses.append(f"AND od.signed_at >= ${idx}")
        params.append(date_from_parsed)
        idx += 1
    if date_to_parsed:
        clauses.append(f"AND od.signed_at < ${idx}::date + INTERVAL '1 day'")
        params.append(date_to_parsed)

    return " ".join(clauses), params
