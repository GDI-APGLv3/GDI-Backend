"""
Modulo de servicios para CCOO (Comunicaciones Oficiales).
Unifica Notas y Memos en endpoints con paginacion server-side via UNION ALL.
"""

from .retrieval import get_received_ccoo, get_sent_ccoo, get_archived_ccoo

__all__ = [
    'get_received_ccoo',
    'get_sent_ccoo',
    'get_archived_ccoo',
]
