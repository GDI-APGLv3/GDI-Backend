"""
Módulo de modelos para documentos.
Exporta todos los modelos relacionados con documentos para facilitar su importación.
"""

# Modelos de creación
from .creation import (
    CreateDocumentRequest,
    CreateDocumentResponse
)

# Modelos de edición
from .editing import (
    DocumentSigner,
    SaveDocumentRequest,
    SaveDocumentResponse,
    DocumentSignerInfo,
    RejectionInfo,
    DocumentDetailResponse
)

# Modelos de rechazo
from .rejection import (
    RejectDocumentRequest,
    RejectDocumentResponse
)

# Modelos de previsualización
from .preview import (
    DocumentPreviewResponse,
    DocumentPreviewInfo,
    PreviewInfoResponse
)

# Modelos de estados
from .states import (
    DisplayStateInfo,
    DocumentStatesResponse
)

# Modelos de tipos
# Nota: DocumentTypeInfo se importa desde shared/base.py via types.py (re-export)
from .types import DocumentTypesResponse
from models.shared.base import DocumentTypeInfo

# Modelos de URL oficial
from .official_url import (
    OfficialDocumentUrlResponse,
    OfficialDocumentUrlData
)

# Modelos de firma
from .signing import (
    StartSigningRequest,
    SignDocumentResponse,
    SignerInfo,
    DocumentSigningDetails,
    SigningProgressInfo,
    CurrentSignerInfo,
    DocumentSignatureDetailsResponse,
    StartSigningProcessResponse,
    SignNumeratorResponse
)

__all__ = [
    # Creación
    "CreateDocumentRequest",
    "CreateDocumentResponse",
    
    # Edición
    "DocumentSigner",
    "SaveDocumentRequest", 
    "SaveDocumentResponse",
    "DocumentSignerInfo",
    "RejectionInfo",
    "DocumentDetailResponse",
    
    # Rechazo
    "RejectDocumentRequest",
    "RejectDocumentResponse",
    
    # Previsualización
    "DocumentPreviewResponse",
    "DocumentPreviewInfo",
    "PreviewInfoResponse",
    
    # Metadatos
    "DocumentTypeInfo",
    "DocumentTypesResponse",
    "DisplayStateInfo", 
    "DocumentStatesResponse",
    
    # URL Oficial
    "OfficialDocumentUrlResponse",
    "OfficialDocumentUrlData",
    
    # Firma
    "StartSigningRequest",
    "SignDocumentResponse", 
    "SignerInfo",
    "DocumentSigningDetails",
    "SigningProgressInfo",
    "CurrentSignerInfo",
    "DocumentSignatureDetailsResponse",
    "StartSigningProcessResponse",
    "SignNumeratorResponse",
    "NumeratorInfo"
]