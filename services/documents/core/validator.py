"""
Validador de documentos - REFACTORIZADO
Centraliza reglas de negocio y validaciones.

UBICACION: services/documents/core/validator.py
MIGRADO: Fase 2.2 del refactoring
"""
from typing import Optional, List, Dict
from uuid import UUID
from shared.exceptions import ValidationError, DocumentStateError, DocumentNotFoundError
from config.constants import EDITABLE_DOCUMENT_STATES
from .repository import DocumentRepository


class DocumentValidator:
    """Validador de reglas de negocio para documentos."""

    EDITABLE_STATES = EDITABLE_DOCUMENT_STATES

    @classmethod
    def validate_document_id(cls, document_id: str) -> None:
        """Valida formato de UUID."""
        try:
            UUID(document_id)
        except ValueError:
            raise ValidationError(f"document_id '{document_id}' debe ser un UUID valido")

    @classmethod
    def validate_can_be_edited(cls, document_id: str, *, schema_name: str) -> None:
        """Valida que documento existe y puede ser editado."""
        cls.validate_document_id(document_id)

        status = DocumentRepository.get_status(document_id, schema_name=schema_name)
        if status is None:
            raise DocumentNotFoundError(document_id)

        if status not in cls.EDITABLE_STATES:
            raise DocumentStateError(
                f"Documento en estado '{status}' no puede ser editado. Estados permitidos: {cls.EDITABLE_STATES}",
                current_state=status,
                required_state=', '.join(cls.EDITABLE_STATES)
            )

    @classmethod
    def validate_update_data(cls, reference: Optional[str], content: Optional[str], signers: Optional[List[Dict]]) -> None:
        """Valida datos de actualizacion."""
        if reference is None and content is None and signers is None:
            raise ValidationError("Debe proporcionar al menos un campo para actualizar")

        if reference is not None:
            if not isinstance(reference, str) or len(reference.strip()) == 0:
                raise ValidationError("reference no puede estar vacia")
            if len(reference) > 250:
                raise ValidationError("reference no puede exceder 250 caracteres")

        if signers is not None:
            cls._validate_signers_list(signers)

    @classmethod
    def _validate_signers_list(cls, signers: List[Dict]) -> None:
        """Valida lista de firmantes."""
        if not isinstance(signers, list):
            raise ValidationError("signers debe ser una lista")

        if len(signers) == 0:
            return

        for i, signer in enumerate(signers):
            cls._validate_single_signer(signer, i + 1)

        numerators = [s for s in signers if s.get("is_numerator")]
        if len(numerators) != 1:
            raise ValidationError("Debe haber exactamente un firmante marcado como numerador")

    @classmethod
    def _validate_single_signer(cls, signer: Dict, position: int) -> None:
        """Valida un firmante individual (user_id O email)."""
        if not isinstance(signer, dict):
            raise ValidationError(f"Firmante {position}: debe ser un objeto")

        user_id = signer.get("user_id")
        email = signer.get("email")

        if user_id == "null" or user_id == "":
            user_id = None
        if email == "null" or email == "":
            email = None

        if not user_id and not email:
            raise ValidationError(f"Firmante {position}: se debe proporcionar user_id o email")

        if user_id and email:
            raise ValidationError(f"Firmante {position}: no se puede proporcionar user_id y email al mismo tiempo")

        if user_id:
            try:
                UUID(user_id)
            except ValueError:
                raise ValidationError(f"Firmante {position}: user_id debe ser un UUID valido")

        if email and "@" not in email:
            raise ValidationError(f"Firmante {position}: email debe tener formato valido")

        is_numerator = signer.get("is_numerator", False)
        if not isinstance(is_numerator, bool):
            raise ValidationError(f"Firmante {position}: is_numerator debe ser verdadero o falso")
