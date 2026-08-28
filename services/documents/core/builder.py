import json
from typing import Dict, Any, List, Optional


class DocumentBuilder:

    @staticmethod
    def build_complete_response(
        document: Dict[str, Any],
        signers: List[Dict[str, Any]],
        rejection_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
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
            "created_at": None,
            "updated_at": document['last_modified_at'].isoformat() if document['last_modified_at'] else None
        }

    @staticmethod
    def _extract_content(content_json: Any) -> Optional[str]:
        if content_json is None:
            return None

        try:
            if isinstance(content_json, str):
                parsed = json.loads(content_json)
            else:
                parsed = content_json

            if not isinstance(parsed, dict):
                return None

            if 'html' in parsed:
                return parsed['html']

            if 'detalle' in parsed:
                return parsed['detalle']

            if 'body' in parsed:
                return parsed['body']

            if 'type' in parsed and 'content' in parsed:
                return json.dumps(parsed)

            return None

        except (json.JSONDecodeError, AttributeError, TypeError):
            return None

    @staticmethod
    def _build_document_type(document: Dict[str, Any]) -> Dict[str, str]:
        return {
            "name": document.get('document_type_name', 'N/A'),
            "acronym": document.get('document_type_acronym', 'N/A')
        }

    @staticmethod
    def _format_signers(signers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
        if rejection_info is None:
            return None

        return {
            "reason": rejection_info['reason'],
            "rejected_at": rejection_info['rejected_at'].isoformat() if rejection_info['rejected_at'] else None,
            "rejected_by": rejection_info['rejected_by'],
            "rejected_by_name": rejection_info['rejected_by_name']
        }
