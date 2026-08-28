from database import fetch_all

MAX_SEARCH_LIMIT = 20


async def search_citizens(q: str, *, schema_name: str, limit: int = MAX_SEARCH_LIMIT) -> list:
    capped_limit = min(max(limit, 1), MAX_SEARCH_LIMIT)
    term = f"%{q.strip()}%"
    rows = await fetch_all(
        """
        SELECT id, full_name, country_id, estado
        FROM citizens
        WHERE full_name ILIKE $1 OR country_id ILIKE $1
        ORDER BY full_name
        LIMIT $2
        """,
        term, capped_limit,
        schema_name=schema_name,
    )
    return [
        {
            "id": str(row["id"]),
            "full_name": row["full_name"],
            "country_id": row["country_id"],
            "estado": row["estado"],
        }
        for row in rows
    ]
