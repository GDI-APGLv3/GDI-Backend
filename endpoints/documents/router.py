"""
Router principal para endpoints de documentos.
Organiza los endpoints por flujo de negocio y frecuencia de uso.
"""
from fastapi import APIRouter

# A. ENDPOINTS DE CATÁLOGO (más usados, primero en Swagger)
from .get_types import router as types_router
from .get_states import router as states_router
from .create_document import router as create_document_router
from .create_imported import router as create_imported_router

# B. BÚSQUEDA Y AUTOCOMPLETADO
from .autocomplete import router as autocomplete_router
from .search_official import router as search_official_router

# C. OBTENER DETALLES (por especificidad)
from .unified_details import router as unified_details_router
from .editor_details import router as editor_details_router
from .signature_details import router as signature_details_router

# D. MODIFICACIÓN DE DOCUMENTOS (ciclo de vida)
from .save_document import router as save_document_router
from .replace_imported_pdf import router as replace_imported_pdf_router
from .preview_document import router as preview_document_router
from .start_signing import router as start_signing_router
from .reject_document import router as reject_document_router
from .delete_document import router as delete_document_router
from .super_sign import router as super_sign_router

# E. UTILIDADES
from .geturl_officialdoc import router as geturl_officialdoc_router
from .content import router as content_router
from .check_signer_permissions import router as check_signer_permissions_router


# Router principal de documentos
documents_router = APIRouter()

# A. ENDPOINTS DE CATÁLOGO
documents_router.include_router(types_router)
documents_router.include_router(states_router)
documents_router.include_router(create_document_router)
documents_router.include_router(create_imported_router)

# B. BÚSQUEDA Y AUTOCOMPLETADO
documents_router.include_router(autocomplete_router)
documents_router.include_router(search_official_router)

# C. OBTENER DETALLES
documents_router.include_router(unified_details_router)
documents_router.include_router(editor_details_router)
documents_router.include_router(signature_details_router)

# D. MODIFICACIÓN DE DOCUMENTOS
documents_router.include_router(save_document_router)
documents_router.include_router(replace_imported_pdf_router)
documents_router.include_router(preview_document_router)
documents_router.include_router(start_signing_router)
documents_router.include_router(reject_document_router)
documents_router.include_router(delete_document_router)
documents_router.include_router(super_sign_router)

# E. UTILIDADES
documents_router.include_router(geturl_officialdoc_router)
documents_router.include_router(content_router)
documents_router.include_router(check_signer_permissions_router)
