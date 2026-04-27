"""
Modelos para el modulo de MEMOS.
Sistema de comunicacion privada persona-a-persona con destinatarios TO/CC/BCC y tracking de apertura.
"""

from .recipients import MemoRecipientInfo, MemoVisibleRecipientsResponse
from .responses import (
    MemoSummary,
    MemoDetail,
    MemoSentListResponse,
    MemoReceivedListResponse,
    MemoDetailResponse,
    MemoArchivedListResponse,
    MemoUnreadCountResponse,
    PaginationInfo
)

__all__ = [
    'MemoRecipientInfo',
    'MemoVisibleRecipientsResponse',
    'MemoSummary',
    'MemoDetail',
    'MemoSentListResponse',
    'MemoReceivedListResponse',
    'MemoDetailResponse',
    'MemoArchivedListResponse',
    'MemoUnreadCountResponse',
    'PaginationInfo',
]
