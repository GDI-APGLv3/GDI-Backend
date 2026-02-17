"""
Constructor de respuestas de documentos - REFACTORIZADO
Maneja el formateo de datos siguiendo Single Responsibility.

UBICACION: services/documents/core/builder.py
MIGRADO: Fase 2.2 del refactoring
"""
import json
from typing import Dict, Any, List, Optional


class DocumentBuilder:
    """
    Constructor de respuestas de documentos.
    Aplica Single Responsibility: Solo formateo de datos.
    """

    @staticmethod
    def build_complete_response(
        document: Dict[str, Any],
        signers: List[Dict[str, Any]],
        rejection_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Construye respuesta completa del documento."""
        return {
            "document_id": document['id'],
            "reference": document['reference'],
            "content": DocumentBuilder._extract_content(document['content']),
            "status": document['status'],
            "document_type": DocumentBuilder._build_document_type(document),
            "created_by": document['creator_id'],
            "creator_name": document['creator_name'],
            "signers": DocumentBuilder._format_signers(signers),
            "rejection_info": DocumentBuilder._format_rejection_info(rejection_info),
            "created_at": None,  # No disponible en esta tabla
            "updated_at": document['last_modified_at'].isoformat() if document['last_modified_at'] else None
        }

    @staticmethod
    def _extract_content(content_json: Any) -> Optional[str]:
        """
        Extrae contenido HTML del JSON almacenado.
        Soporta multiples formatos para migracion gradual:
        - Nuevo estandar: {'html': 'content'}
        - Formato legacy: {'detalle': 'content'}
        - Formato mixto: {'body': 'content'}
        """
        if content_json is None:
            return None

        try:
            if isinstance(content_json, str):
                parsed = json.loads(content_json)
            else:
                parsed = content_json

            if not isinstance(parsed, dict):
                return None

            # PRIORIDAD 1: Nuevo formato estandar 'html'
            if 'html' in parsed:
                return parsed['html']

            # PRIORIDAD 2: Formato legacy 'detalle'
            if 'detalle' in parsed:
                return parsed['detalle']

            # PRIORIDAD 3: Formato alternativo 'body'
            if 'body' in parsed:
                return parsed['body']

            # PRIORIDAD 4: Para formato TipTap, devolver JSON como string
            if 'type' in parsed and 'content' in parsed:
                # Es formato TipTap, mantenemos como esta por ahora
                return json.dumps(parsed)

            return None

        except (json.JSONDecodeError, AttributeError, TypeError):
            return None

    @staticmethod
    def _build_document_type(document: Dict[str, Any]) -> Dict[str, str]:
        """Construye informacion del tipo de documento."""
        return {
            "name": document.get('document_type_name', 'N/A'),
            "acronym": document.get('document_type_acronym', 'N/A')
        }

    @staticmethod
    def _format_signers(signers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Formatea lista de firmantes para la respuesta incluyendo email y profile_picture_url."""
        return [
            {
                "user_id": signer['user_id'],
                "user_name": signer['user_name'],
                "email": signer['email'],
                "signing_order": signer['signing_order'],
                "is_numerator": signer['is_numerator'],
                "profile_picture_url": signer['profile_picture_url']
            }
            for signer in signers
        ]

    @staticmethod
    def _format_rejection_info(rejection_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Formatea informacion de rechazo."""
        if rejection_info is None:
            return None

        return {
            "reason": rejection_info['reason'],
            "rejected_at": rejection_info['rejected_at'].isoformat() if rejection_info['rejected_at'] else None,
            "rejected_by": rejection_info['rejected_by'],
            "rejected_by_name": rejection_info['rejected_by_name']
        }
