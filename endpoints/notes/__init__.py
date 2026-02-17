"""
Endpoints para el módulo de NOTAS.
Sistema de documentos oficiales con destinatarios (TO/CC/BCC) y tracking de apertura.
"""

from .router import router as notes_router

__all__ = ['notes_router']
