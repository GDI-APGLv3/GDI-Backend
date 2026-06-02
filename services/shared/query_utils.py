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
    date_to: str | None = None,
    start: int = 1,
) -> tuple:
    """
    Construye clausula SQL de filtro de fecha.

    Args:
        date_filter: Filtro predefinido ('hoy', 'ayer', 'ultimos_7_dias', 'ultimos_30_dias').
        date_from: Fecha desde (YYYY-MM-DD).
        date_to: Fecha hasta (YYYY-MM-DD).
        start: Número de parámetro $N a partir del cual numerar los placeholders
               (solo aplica a date_from/date_to). Default=1 para compatibilidad psycopg2.
               Pasar el índice del primer parámetro libre de la query padre.

    Returns:
        (clause: str con AND prefix o vacío, params: list)
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
    idx = start
    if date_from:
        clauses.append(f"AND od.signed_at >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to:
        clauses.append(f"AND od.signed_at < ${idx}::date + INTERVAL '1 day'")
        params.append(date_to)

    return " ".join(clauses), params
