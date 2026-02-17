"""
Modelos para el módulo de NOTAS.
Sistema de documentos oficiales con destinatarios (TO/CC/BCC) y tracking de apertura.
"""

from .recipients import RecipientInfo, VisibleRecipientsResponse
from .responses import (
    NoteSummary,
    NoteDetail,
    NoteSentListResponse,
    NoteReceivedListResponse,
    NoteDetailResponse,
    PaginationInfo
)

__all__ = [
    'RecipientInfo',
    'VisibleRecipientsResponse',
    'NoteSummary',
    'NoteDetail',
    'NoteSentListResponse',
    'NoteReceivedListResponse',
    'NoteDetailResponse',
    'PaginationInfo',
]
