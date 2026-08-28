
import re
from datetime import datetime
from typing import Optional, Tuple
from uuid import UUID
from shared.exceptions import ValidationError

class CaseFiltersValidator:
    
    @staticmethod
    def validate_user_id(user_id: str) -> str:
        try:
            UUID(user_id)
            return user_id
        except ValueError:
            raise ValidationError("El ID de usuario proporcionado no es válido")
    
    @staticmethod
    def validate_status_filter(status_filter: Optional[str]) -> Optional[str]:
        if status_filter is None:
            return None
        
        valid_statuses = ["active", "inactive", "archived"]
        if status_filter not in valid_statuses:
            raise ValidationError(f"El filtro de estado debe ser uno de: {', '.join(valid_statuses)}")
        
        return status_filter
    
    @staticmethod
    def validate_date_filter(date_filter: Optional[str]) -> Optional[str]:
        if date_filter is None:
            return None
        
        valid_dates = ["hoy", "ayer", "ultimos_7_dias", "ultimos_30_dias"]
        if date_filter not in valid_dates:
            raise ValidationError(f"El filtro de fecha debe ser uno de: {', '.join(valid_dates)}")
        
        return date_filter
    
    @staticmethod
    def validate_date_format(date_str: Optional[str], field_name: str) -> Optional[str]:
        if date_str is None:
            return None
        
        date_pattern = r'^\d{4}-\d{2}-\d{2}$'
        if not re.match(date_pattern, date_str):
            raise ValidationError(f"{field_name}: El formato de fecha debe ser YYYY-MM-DD")
        
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            raise ValidationError(f"{field_name}: El formato de fecha debe ser YYYY-MM-DD")
        
        return date_str
    
    @staticmethod
    def validate_date_range(date_from: Optional[str], date_to: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        if date_from is None or date_to is None:
            return date_from, date_to
        
        try:
            start_date = datetime.strptime(date_from, '%Y-%m-%d')
            end_date = datetime.strptime(date_to, '%Y-%m-%d')
            
            if start_date > end_date:
                raise ValidationError("La fecha de inicio no puede ser posterior a la fecha de fin")
                
        except ValueError:
            pass
        
        return date_from, date_to
    
    @staticmethod
    def validate_sector_id(sector_id: Optional[str]) -> Optional[str]:
        if sector_id is None:
            return None
        
        sector_id = sector_id.strip()
        
        if not sector_id:
            return None
        
        try:
            UUID(sector_id)
            return sector_id
        except ValueError:
            raise ValidationError("El ID de sector debe ser un UUID válido")
    
    @staticmethod
    def validate_search_text(search_text: Optional[str]) -> Optional[str]:
        if search_text is None:
            return None
        
        search_text = search_text.strip()
        
        if not search_text:
            return None
        
        if len(search_text) < 2:
            raise ValidationError("El texto de búsqueda debe tener al menos 2 caracteres")
        
        if len(search_text) > 100:
            raise ValidationError("El texto de búsqueda no puede tener más de 100 caracteres")
        
        return search_text
    
    @staticmethod
    def validate_department_id(department_id: Optional[str]) -> Optional[str]:
        if department_id is None:
            return None
        
        department_id = department_id.strip()
        
        if not department_id:
            return None
        
        try:
            UUID(department_id)
            return department_id
        except ValueError:
            raise ValidationError("El ID de departamento debe ser un UUID válido")
    
    @staticmethod
    def validate_all_filters(
        user_id: str,
        status_filter: Optional[str] = None,
        date_filter: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sector_filter: Optional[str] = None,
        trata_filter: Optional[str] = None,
        search_filter: Optional[str] = None,
        department_filter: Optional[str] = None,
        creator_filter: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> dict:
        validated_user_id = CaseFiltersValidator.validate_user_id(user_id)
        validated_status = CaseFiltersValidator.validate_status_filter(status_filter)
        validated_date_filter = CaseFiltersValidator.validate_date_filter(date_filter)
        
        validated_date_from = CaseFiltersValidator.validate_date_format(date_from, "date_from")
        validated_date_to = CaseFiltersValidator.validate_date_format(date_to, "date_to")
        
        validated_date_from, validated_date_to = CaseFiltersValidator.validate_date_range(
            validated_date_from, validated_date_to
        )
        
        validated_sector_filter = CaseFiltersValidator.validate_sector_id(sector_filter)
        validated_trata_filter = CaseFiltersValidator.validate_sector_id(trata_filter)
        validated_department_filter = CaseFiltersValidator.validate_department_id(department_filter)
        
        validated_search_filter = CaseFiltersValidator.validate_search_text(search_filter)
        
        validated_creator_filter = None
        if creator_filter:
            try:
                UUID(creator_filter)
                validated_creator_filter = creator_filter
            except ValueError:
                raise ValidationError("El ID de creador debe ser un UUID válido")
        
        return {
            "user_id": validated_user_id,
            "status_filter": validated_status,
            "date_filter": validated_date_filter,
            "date_from": validated_date_from,
            "date_to": validated_date_to,
            "sector_filter": validated_sector_filter,
            "trata_filter": validated_trata_filter,
            "search_filter": validated_search_filter,
            "department_filter": validated_department_filter,
            "creator_filter": validated_creator_filter,
            "page": max(1, page),
            "page_size": max(1, min(100, page_size))
        }