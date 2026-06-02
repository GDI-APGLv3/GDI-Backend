"""
Servicios de documentos - Logica de negocio para gestion de documentos.

Modulos organizados por dominio funcional:
- lifecycle/: Creacion, edicion, eliminacion, rechazo
- catalog/: Estados, tipos, metadata
- signing/: Firma, numerador, detalles
- core/: Queries, builder, validator
- preview/: Preview de documentos
- retrieval/: Consulta de documentos oficiales
- importing/: Importacion de PDFs externos

Uso:
    from services.documents.lifecycle.creation import create_document
    from services.documents.lifecycle.rejection import reject_document
    from services.documents.signing.signing import sign_document
    from services.documents.signing.numerator import sign_document_as_numerator
    from services.documents.catalog.states import get_all_display_states
    from services.documents.catalog.types import get_all_document_types
    from services.documents.importing import create_imported_document
"""

# Importaciones de creation
from .lifecycle.creation import create_document

# Importaciones de editing
from .editing import (
    save_document_changes,
    get_document_details_for_editing,
    check_document_can_be_edited as validate_document_for_editing
)

# Importaciones de catalog (estados, tipos, metadata)
from .catalog import (
    # States
    get_display_state_name,
    get_all_display_states,
    get_all_state_mappings,
    # Types
    get_all_document_types,
    # Metadata
    get_document_basic_info
)

# Importaciones de signing (ahora desde signing/)
from .signing import (
    start_document_signing_process,
    sign_document,
    sign_document_as_numerator,
    get_numerator_documents,
    super_sign_document,
    build_signature_details_response
)

# Importaciones de preview
from .preview import (
    generate_document_preview
)

# Importaciones de rejection
from .lifecycle.rejection import (
    reject_document,
    get_document_rejections,
    get_rejected_documents_for_user,
    can_user_reject_document
)

# Importaciones de importing (nueva ubicacion)
from .importing import (
    create_imported_document,
    replace_imported_pdf
)

__all__ = [
    # Creation
    "create_document",
    
    # Editing
    "save_document_changes",
    "get_document_details_for_editing", 
    "validate_document_for_editing",
    
    # Estados
    "get_display_state_name",
    "get_all_display_states",
    "get_all_state_mappings",

    # Tipos
    "get_all_document_types",
    
    # Metadata
    "get_document_basic_info",
    
    # Signing
    "start_document_signing_process",
    "sign_document",
    "super_sign_document",
    "build_signature_details_response",

    # Preview
    "generate_document_preview",

    # Numerator
    "sign_document_as_numerator",
    "get_numerator_documents",
    
    # Rejection
    "reject_document",
    "get_document_rejections",
    "get_rejected_documents_for_user",
    "can_user_reject_document",
    
    # Importing
    "create_imported_document",
    "replace_imported_pdf"
]