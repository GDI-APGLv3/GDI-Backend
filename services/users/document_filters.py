"""
Filtros para consultas de documentos de usuario.
Separa la lógica de construcción de filtros SQL.
"""

from typing import List, Tuple, Optional
from dataclasses import dataclass
from shared.logging import get_logger
from services.documents.catalog.states import get_all_display_states

logger = get_logger(__name__)

@dataclass
class FilterCondition:
    """Representa una condición de filtro SQL."""
    condition: str
    params: List[str]

class DocumentFilters:
    """Constructor de filtros para consultas de documentos."""
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.conditions: List[FilterCondition] = []
    
    def add_base_access_filter(self, doc_number: Optional[str] = None) -> "DocumentFilters":
        """
        Agrega el filtro base de acceso a documentos.
        Si hay doc_number, búsqueda global. Si no, solo documentos del usuario.
        """
        if doc_number:
            # Búsqueda por número oficial O referencia (parcial, case-insensitive)
            search_pattern = f"%{doc_number}%"
            condition = FilterCondition(
                condition="(COALESCE(official_number, '') ILIKE %s OR reference ILIKE %s)",
                params=[search_pattern, search_pattern]
            )
            self.conditions.append(condition)
        else:
            # Para consulta unificada, la lógica de acceso ya está en las subconsultas
            # No agregar filtro adicional aquí
            pass
        
        return self

    def add_search_filter(self, search_text: Optional[str] = None) -> "DocumentFilters":
        """
        Agrega filtro de búsqueda parcial en reference, official_number y contenido.
        Usa LIKE con comodines para búsqueda case-insensitive.
        Usa unaccent() para ignorar tildes/acentos en la búsqueda.
        Mínimo 2 caracteres para activar.

        Args:
            search_text: Texto a buscar (case-insensitive, parcial, ignora acentos)
        """
        if not search_text or len(search_text) < 2:
            return self

        search_pattern = f"%{search_text.lower()}%"
        search_term = search_text.lower()
        condition = FilterCondition(
            condition="""(
                unaccent(LOWER(base.reference)) LIKE unaccent(%s)
                OR unaccent(LOWER(COALESCE(base.official_number, ''))) LIKE unaccent(%s)
                OR unaccent(LOWER(COALESCE(base.content->>'html', ''))) LIKE unaccent(%s)
                OR similarity(unaccent(LOWER(base.reference)), unaccent(LOWER(%s))) > 0.3
            )""",
            params=[search_pattern, search_pattern, search_pattern, search_term]
        )
        self.conditions.append(condition)
        return self

    def add_sector_filter(self, sector_filter: bool = False) -> "DocumentFilters":
        """
        Agrega filtro por sector si está activado.
        Incluye documentos del sector principal del usuario Y de sus sectores adicionales.
        """
        if sector_filter:
            # Incluye sector principal + sectores adicionales del usuario
            condition = FilterCondition(
                condition="""creator.sector_id IN (
                    SELECT sector_id FROM users WHERE id = %s
                    UNION
                    SELECT sector_id FROM user_sector_permissions WHERE user_id = %s
                )""",
                params=[self.user_id, self.user_id]
            )
            self.conditions.append(condition)
        return self
    
    def add_status_filter(self, status_filter: Optional[str] = None) -> "DocumentFilters":
        """
        Agrega filtro por estado visual.
        Nota: Se aplica al resultado combinado, no a las tablas individuales.
        """
        if not status_filter:
            return self
        
        # Mapear nombres de estados de la base de datos a condiciones SQL
        condition = self._get_status_filter_condition(status_filter)
        
        self.conditions.append(condition)
        return self
    
    def _get_status_filter_condition(self, status_filter: str) -> FilterCondition:
        """
        Mapea nombres de estados de display a condiciones SQL.
        Usa los estados de la base de datos como fuente única de verdad.
        """
        # Obtener estados desde BD y crear mapeo dinámico
        try:
            states = get_all_display_states()
            display_names = [state['display_state'] for state in states]
            
            # Mapear cada estado de display a su condición SQL correspondiente
            if status_filter == "En edición":
                return FilterCondition("status = 'draft'", [])
            elif status_filter == "En proceso de firma":
                return FilterCondition("status = 'sent_to_sign'", [])
            elif status_filter == "Firmar ahora":
                return FilterCondition(
                    "(status = 'sent_to_sign' AND is_numerator_for_user = false AND user_already_signed = false)",
                    []
                )
            elif status_filter == "Firmado":
                return FilterCondition("status IN ('signed', 'completed')", [])
            else:
                # Fallback para estado directo
                return FilterCondition("status = %s", [status_filter])
        except Exception as e:
            logger.error(f"Error obteniendo estados para filtro: {e}")
            # Fallback seguro
            return FilterCondition("status = %s", [status_filter])
    
    def add_date_filter(self, date_filter: Optional[str] = None) -> "DocumentFilters":
        """
        Agrega filtro por fechas predefinidas.
        Nota: Se aplica al resultado combinado usando el campo created_at.
        """
        if not date_filter:
            return self
        
        date_conditions = {
            "hoy": "DATE(created_at) = CURRENT_DATE",
            "ayer": "DATE(created_at) = CURRENT_DATE - INTERVAL '1 day'",
            "ultimos_7_dias": "created_at >= CURRENT_DATE - INTERVAL '7 days'",
            "ultimos_30_dias": "created_at >= CURRENT_DATE - INTERVAL '30 days'"
        }
        
        if date_filter in date_conditions:
            condition = FilterCondition(date_conditions[date_filter], [])
            self.conditions.append(condition)
        
        return self
    
    def add_date_range_filter(self, date_from: Optional[str] = None, 
                            date_to: Optional[str] = None) -> "DocumentFilters":
        """
        Agrega filtro por rango de fechas personalizado.
        Nota: Se aplica al resultado combinado usando el campo created_at.
        """
        if date_from:
            condition = FilterCondition("DATE(created_at) >= %s", [date_from])
            self.conditions.append(condition)
        
        if date_to:
            condition = FilterCondition("DATE(created_at) <= %s", [date_to])
            self.conditions.append(condition)
        
        return self
    
    def add_document_type_filter(self, document_type: Optional[str] = None) -> "DocumentFilters":
        """
        Agrega filtro por tipo de documento.
        Nota: Se aplica al resultado combinado usando el campo document_type_acronym.
        """
        if document_type:
            condition = FilterCondition("document_type_acronym = %s", [document_type])
            self.conditions.append(condition)
        return self

    def add_case_filter(self, case_id: Optional[str] = None) -> "DocumentFilters":
        """
        Agrega filtro para documentos vinculados a un expediente específico.
        Busca en case_official_documents (firmados) y case_proposed_documents (borradores).
        """
        if case_id:
            # El filtro busca documentos cuyo ID esté vinculado al expediente
            # Para oficiales: case_official_documents.official_document_id
            # Para borradores: case_proposed_documents.document_draft_id
            # base.id representa el document_id en la consulta unificada
            condition = FilterCondition(
                condition="""(
                    EXISTS (SELECT 1 FROM case_official_documents cod
                            WHERE cod.official_document_id = base.id
                            AND cod.case_id = %s
                            AND cod.is_active = true)
                    OR EXISTS (SELECT 1 FROM case_proposed_documents cpd
                              WHERE cpd.document_draft_id = base.id
                              AND cpd.case_id = %s
                              AND cpd.is_active = true)
                )""",
                params=[case_id, case_id]
            )
            self.conditions.append(condition)
        return self

    def add_min_signers_filter(self, min_signers: Optional[int] = None) -> "DocumentFilters":
        """
        Agrega filtro para documentos con mínimo N firmantes.
        Usa el campo required_signatures de la query combinada.

        Args:
            min_signers: Cantidad mínima de firmantes requeridos (ej: 2)
        """
        if min_signers and min_signers > 0:
            condition = FilterCondition(
                condition="required_signatures >= %s",
                params=[min_signers]
            )
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