"""
Utilidades compartidas para queries SQL.
Funciones reutilizables por notas, memos, ccoo y otros modulos.
"""


def escape_like(value: str) -> str:
    """
    Escapa caracteres especiales de ILIKE (%, _) para evitar wildcards inesperados.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_date_filter(
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None
) -> tuple:
    """
    Construye clausula SQL de filtro de fecha.
    Returns: (clause: str con AND prefix o vacio, params: list)
    """
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
    if date_from:
        clauses.append("AND od.signed_at >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("AND od.signed_at < %s::date + INTERVAL '1 day'")
        params.append(date_to)

    return " ".join(clauses), params
