"""Queries para operaciones de sectores y departamentos."""

def get_all_sectors_with_departments_query() -> str:
    """Query para obtener sectores activos con sus departamentos (incluyendo nombres)."""
    return """
        SELECT
            s.id as sector_id,
            s.acronym as sector_acronym,
            s.primary_color as sector_color,
            d.id as department_id,
            d.acronym as department_acronym,
            d.name as department_name,
            d.primary_color as department_color
        FROM sectors s
        JOIN departments d ON s.department_id = d.id
        WHERE s.is_active = true
          AND d.is_active = true
        ORDER BY d.acronym, s.acronym
    """