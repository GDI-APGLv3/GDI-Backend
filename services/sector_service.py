
from typing import Dict, Any
from database import fetch_all


class SectorService:

    @staticmethod
    async def get_all_sectors_with_departments(*, schema_name: str) -> Dict[str, Any]:
        try:
            from services.sectors.queries import get_all_sectors_with_departments_query

            query = get_all_sectors_with_departments_query()

            results = await fetch_all(query, schema_name=schema_name)

            sectors = []
            for row in results:
                sector_data = {
                    "sector_id": str(row['sector_id']) if row.get('sector_id') else None,
                    "sector_acronym": row['sector_acronym'],
                    "sector_color": row.get('sector_color'),
                    "department_id": str(row['department_id']) if row.get('department_id') else None,
                    "department_acronym": row['department_acronym'],
                    "department_name": row.get('department_name'),
                    "department_color": row.get('department_color'),
                }
                sectors.append(sector_data)

            return {
                "sectors": sectors,
                "total": len(sectors)
            }

        except Exception as e:
            raise Exception(f"Error obteniendo sectores con departamentos: {str(e)}")
