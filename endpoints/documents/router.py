from fastapi import APIRouter

from .get_types import router as types_router
from .get_states import router as states_router
from .create_document import router as create_document_router
from .create_imported import router as create_imported_router
from .get_type_fields import router as type_fields_router

from .autocomplete import router as autocomplete_router
from .search_official import router as search_official_router
from .search import router as search_router
from .pending_signatures import router as pending_signatures_router

from .unified_details import router as unified_details_router
from .editor_details import router as editor_details_router
from .signature_details import router as signature_details_router

from .save_document import router as save_document_router
from .replace_imported_pdf import router as replace_imported_pdf_router
from .preview_document import router as preview_document_router
from .start_signing import router as start_signing_router
from .reject_document import router as reject_document_router
from .delete_document import router as delete_document_router
from .super_sign import router as super_sign_router
from .upload_image import router as upload_image_router
from .embedded_file import router as embedded_file_router

from .geturl_officialdoc import router as geturl_officialdoc_router
from .content import router as content_router
from .check_signer_permissions import router as check_signer_permissions_router


documents_router = APIRouter()

documents_router.include_router(types_router)
documents_router.include_router(states_router)
documents_router.include_router(create_document_router)
documents_router.include_router(create_imported_router)
documents_router.include_router(type_fields_router)

documents_router.include_router(search_router)
documents_router.include_router(pending_signatures_router)
documents_router.include_router(autocomplete_router)
documents_router.include_router(search_official_router)

documents_router.include_router(unified_details_router)
documents_router.include_router(editor_details_router)
documents_router.include_router(signature_details_router)

documents_router.include_router(save_document_router)
documents_router.include_router(replace_imported_pdf_router)
documents_router.include_router(preview_document_router)
documents_router.include_router(start_signing_router)
documents_router.include_router(reject_document_router)
documents_router.include_router(delete_document_router)
documents_router.include_router(super_sign_router)
documents_router.include_router(upload_image_router)
documents_router.include_router(embedded_file_router)

documents_router.include_router(geturl_officialdoc_router)
documents_router.include_router(content_router)
documents_router.include_router(check_signer_permissions_router)
