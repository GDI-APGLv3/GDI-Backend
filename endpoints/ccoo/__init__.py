"""
Endpoints para el modulo de CCOO (Comunicaciones Oficiales).
Unifica Notas y Memos en bandejas de entrada/enviados/archivados con paginacion server-side.
"""

from .router import router as ccoo_router

__all__ = ['ccoo_router']
