"""
Modelos para el modulo de CCOO (Comunicaciones Oficiales).
Unifica Notas y Memos en un formato de respuesta comun.
"""

from .responses import (
    CcooSender,
    CcooReadStatus,
    CcooReceivedItem,
    CcooSentItem,
    CcooPagination,
    CcooReceivedListResponse,
    CcooSentListResponse,
    CcooArchivedItem,
    CcooArchivedListResponse,
)

__all__ = [
    'CcooSender',
    'CcooReadStatus',
    'CcooReceivedItem',
    'CcooSentItem',
    'CcooPagination',
    'CcooReceivedListResponse',
    'CcooSentListResponse',
    'CcooArchivedItem',
    'CcooArchivedListResponse',
]
