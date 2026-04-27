"""
Services - Logica de negocio modular de GDI Backend.

Estructura organizada por dominios:

services/
  documents/
    lifecycle/       # Creacion, edicion, eliminacion, rechazo
    catalog/         # Estados, tipos, metadata
    signing/         # Firma, numerador, detalles
    core/            # Queries, builder, validator
    preview/         # Preview de documentos
    retrieval/       # Consulta de documentos oficiales
    importing/       # Importacion de PDFs externos
  users/             # Servicios de usuarios
  shared/            # Servicios compartidos
  cases/             # Expedientes

Uso recomendado:
    from services.documents.lifecycle.creation import create_document
    from services.documents.signing.signing import sign_document
    from services.users.search import search_users_for_autocomplete
"""

# Documentos - Funciones más utilizadas
from .documents.lifecycle.creation import create_document
from .documents.editing import (
    save_document_changes,
    get_document_details_for_editing
)
from .documents.catalog.states import get_all_display_states
from .documents.catalog.types import get_all_document_types
from .users.user_documents import get_user_documents
from .documents.signing import start_document_signing_process, get_document_signature_details, sign_document
from .documents.signing.numerator import sign_document_as_numerator
from .documents.lifecycle.rejection import reject_document, get_document_rejections

# Usuarios - Funciones principales (refactorizado)
from .users.search import search_users_for_autocomplete
from .users.management import get_user_statistics, validate_user_permissions

# APIs externas
from .shared.external_api import call_signature_stamping_api, get_external_services_status

__all__ = [
    # Documentos - Creation
    "create_document",
    
    # Documentos - Editing
    "save_document_changes",
    "get_document_details_for_editing",
    
    # Documentos - Metadata
    "get_user_documents", 
    "get_all_display_states",
    "get_all_document_types",
    
    # Documentos - Signing
    "start_document_signing_process",
    "get_document_signature_details",
    "sign_document",
    
    # Documentos - Numerator
    "sign_document_as_numerator",
    
    # Documentos - Rejection
    "reject_document",
    "get_document_rejections",
    
    # Usuarios (refactorizado)
    "search_users_for_autocomplete",
    "get_user_statistics",
    "validate_user_permissions",
    
    # APIs externas
    "call_signature_stamping_api",
    "get_external_services_status"
]