"""
Filtros para consultas de expedientes de usuario.
Separa la lógica de construcción de filtros SQL siguiendo el patrón de document_filters.py.
"""

from typing import List, Tuple, Optional
from dataclasses import dataclass

@dataclass
class FilterCondition:
    """Representa una condición de filtro SQL."""
    condition: str
    params: List[str]

class CaseFilters:
    """Constructor de filtros para consultas de expedientes."""
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.conditions: List[FilterCondition] = []
    
    def add_base_access_filter(self) -> "CaseFilters":
        """
        Agrega el filtro base de acceso a expedientes.
        Este filtro mantiene la lógica compleja de acceso por sectores.
        No se modifica ya que está en el método get_cases_by_user.
        """
        # La lógica de acceso ya está implementada en get_cases_by_user
        # No agregamos filtros aquí para no duplicar la lógica
        return self
    
    def add_sector_filter(self, sector_filter: Optional[str] = None) -> "CaseFilters":
        """
        Agrega filtro por sector asignado (assigned_sector_id).
        
        Args:
            sector_filter: ID del sector para filtrar
        """
        if sector_filter:
            condition = FilterCondition(
                condition="""EXISTS (
                    SELECT 1 FROM case_movements cm_filter
                    WHERE cm_filter.case_id = c.id
                    AND cm_filter.assigned_sector_id = %s
                    AND cm_filter.is_active = true
                )""",
                params=[sector_filter]
            )
            self.conditions.append(condition)
        return self
    
    def add_trata_filter(self, trata_filter: Optional[str] = None) -> "CaseFilters":
        """
        Agrega filtro por sector administrativo (admin_sector_id).
        Este filtro busca en los movimientos donde el sector es administrador.
        
        Args:
            trata_filter: ID del sector administrativo para filtrar
        """
        if trata_filter:
            condition = FilterCondition(
                condition="""EXISTS (
                    SELECT 1 FROM case_movements cm_trata
                    WHERE cm_trata.case_id = c.id
                    AND cm_trata.admin_sector_id = %s
                    AND (
                        (cm_trata.type = 'transfer' AND cm_trata.is_active = false)
                        OR
                        (cm_trata.type = 'creation')
                    )
                )""",
                params=[trata_filter]
            )
            self.conditions.append(condition)
        return self
    
    def add_date_filter(self, date_filter: Optional[str] = None) -> "CaseFilters":
        """
        Agrega filtro por fechas predefinidas usando last_modified_at.
        
        Args:
            date_filter: Filtro de fecha predefinido
        """
        if not date_filter:
            return self
        
        date_conditions = {
            "hoy": "DATE(last_modified_at) = CURRENT_DATE",
            "ayer": "DATE(last_modified_at) = CURRENT_DATE - INTERVAL '1 day'",
            "ultimos_7_dias": "last_modified_at >= CURRENT_DATE - INTERVAL '7 days'",
            "ultimos_30_dias": "last_modified_at >= CURRENT_DATE - INTERVAL '30 days'"
        }
        
        if date_filter in date_conditions:
            condition = FilterCondition(date_conditions[date_filter], [])
            self.conditions.append(condition)
        
        return self
    
    def add_date_range_filter(self, date_from: Optional[str] = None, 
                            date_to: Optional[str] = None) -> "CaseFilters":
        """
        Agrega filtro por rango de fechas personalizado usando last_modified_at.
        
        Args:
            date_from: Fecha de inicio (YYYY-MM-DD)
            date_to: Fecha de fin (YYYY-MM-DD)
        """
        if date_from:
            condition = FilterCondition("DATE(last_modified_at) >= %s", [date_from])
            self.conditions.append(condition)
        
        if date_to:
            condition = FilterCondition("DATE(last_modified_at) <= %s", [date_to])
            self.conditions.append(condition)
        
        return self
    
    def add_status_filter(self, status_filter: Optional[str] = None) -> "CaseFilters":
        """
        Agrega filtro por estado del expediente.
        Si no se especifica status_filter, por defecto filtra solo 'active'.
        
        Args:
            status_filter: Estado del expediente (active, inactive, archived) o None para 'active'
        """
        # Por defecto, filtrar solo expedientes activos
        if status_filter is None:
            status_filter = "active"
            
        condition = FilterCondition("c.status = %s", [status_filter])
        self.conditions.append(condition)
        return self
    
    def add_search_filter(self, search_filter: Optional[str] = None) -> "CaseFilters":
        """
        Agrega filtro de búsqueda en el expediente Y documentos oficiales vinculados.
        Usa unaccent() para ignorar tildes/acentos en la búsqueda.

        Busca en EXPEDIENTE:
        - case_number (número del expediente)
        - reference (referencia del expediente)

        Y en DOCUMENTOS OFICIALES vinculados:
        - official_number (número oficial del documento)
        - reference (referencia del documento)
        - content->>'html' (contenido del documento)

        Args:
            search_filter: Texto a buscar
        """
        if search_filter:
            search_pattern = f"%{search_filter.lower()}%"
            condition = FilterCondition(
                """(
                    unaccent(LOWER(c.reference)) LIKE unaccent(%s)
                    OR unaccent(LOWER(c.case_number)) LIKE unaccent(%s)
                    OR EXISTS (
                        SELECT 1 FROM case_official_documents cod
                        JOIN official_documents od ON cod.official_document_id = od.id
                        WHERE cod.case_id = c.id
                        AND cod.is_active = true
                        AND od.signed_at IS NOT NULL
                        AND (
                            unaccent(LOWER(COALESCE(od.official_number, ''))) LIKE unaccent(%s)
                            OR unaccent(LOWER(COALESCE(od.reference, ''))) LIKE unaccent(%s)
                            OR unaccent(LOWER(COALESCE(od.content->>'html', ''))) LIKE unaccent(%s)
                        )
                    )
                )""",
                [search_pattern, search_pattern, search_pattern, search_pattern, search_pattern]
            )
            self.conditions.append(condition)
        return self
    
    def add_department_filter(self, department_filter: Optional[str] = None) -> "CaseFilters":
        """
        Agrega filtro por departamento propietario.
        
        Args:
            department_filter: ID del departamento para filtrar
        """
        if department_filter:
            condition = FilterCondition("c.owner_department_id = %s", [department_filter])
            self.conditions.append(condition)
        return self
    
    def add_creator_filter(self, creator_filter: Optional[str] = None) -> "CaseFilters":
        """
        Agrega filtro por usuario creador.
        
        Args:
            creator_filter: ID del usuario creador para filtrar
        """
        if creator_filter:
            condition = FilterCondition("c.created_by_user_id = %s", [creator_filter])
            self.conditions.append(condition)
        return self
    
    def build(self) -> Tuple[str, List[str]]:
        """
        Construye la cláusula WHERE y lista de parámetros.
        
        Returns:
            Tupla con (where_clause, params)
        """
        if not self.conditions:
            return "1=1", []
        
        where_parts = []
        all_params = []
        
        for condition in self.conditions:
            where_parts.append(condition.condition)
            all_params.extend(condition.params)
        
        where_clause = " AND ".join(where_parts)
        return where_clause, all_params