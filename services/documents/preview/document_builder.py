"""
Document Response Builder - Construcción de respuestas estructuradas.
Responsabilidad única: Construir y formatear las respuestas del preview.
"""

from typing import Dict, Any, List, Optional


class DocumentResponseBuilder:
    """
    Construye respuestas estructuradas para el preview de documentos.
    """
    
    def build_preview_response(
        self, 
        document_info: Dict[str, Any],
        creator_data: Optional[Dict[str, Any]],
        signers_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Construye la respuesta simplificada del preview.
        
        Args:
            document_info: Información básica del documento
            creator_data: Datos del creador (no usado en respuesta simplificada)
            signers_data: Lista de firmantes
            
        Returns:
            Dict con la respuesta simplificada del preview
        """
        
        return {
            "document_id": document_info['document_id'],
            "display_status": document_info['display_status'],
            "document_type": {
                "acronym": document_info['type_acronym'],
                "name": document_info['type_name']
            },
            "signers": signers_data,
            "document_generate_id": document_info.get('document_generate_id')
        }
    
