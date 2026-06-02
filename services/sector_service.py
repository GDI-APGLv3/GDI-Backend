"""
Servicio para gestión de sectores.
"""

from typing import Dict, List, Any, Optional
from database import fetch_all


class SectorService:
    """Servicio para operaciones relacionadas con sectores."""

    @staticmethod
    async def get_all_sectors_with_departments(*, schema_name: str) -> Dict[str, Any]:
        """
        Obtener todos los sectores activos con sus respectivos departamentos.
        Incluye IDs, acronimos y nombre del departamento.

        Args:
            schema_name: Nombre del schema del tenant (requerido)

        Returns:
            Dict con lista de sectores y metadatos:
            {
                "sectors": [
                    {
                        "sector_id": "uuid",
                        "sector_acronym": "SECT",
                        "department_id": "uuid",
                        "department_acronym": "DEPT",
                        "department_name": "Nombre del Departamento"
                    }
                ],
                "total": 100
            }
        """
        try:
            from services.sectors.queries import get_all_sectors_with_departments_query

            # Query centralizada para obtener sectores activos con nombres
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
