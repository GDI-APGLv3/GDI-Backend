
from typing import Dict, Any, List, Optional


class DocumentResponseBuilder:
    
    def build_preview_response(
        self, 
        document_info: Dict[str, Any],
        creator_data: Optional[Dict[str, Any]],
        signers_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        
        return {
            "document_id": document_info['document_id'],
            "display_status": document_info['display_status'],
            "document_type": {
                "acronym": document_info['type_acronym'],
                "name": document_info['type_name'],
                "is_public": document_info.get('type_visibility') == 'publico',
            },
            "signers": signers_data,
            "document_generate_id": document_info.get('document_generate_id')
        }
    
