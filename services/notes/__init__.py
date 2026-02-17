"""
Módulo de servicios para NOTAS.
Sistema de documentos oficiales con destinatarios (TO/CC/BCC) y tracking de apertura.

Funciones principales:
- save_recipients: Guardar destinatarios al crear una NOTA
- validate_recipients: Validar que sectors existan y sean válidos
- get_visible_recipients: Obtener recipients según permisos (BCC solo para sender)
- get_sent_notes: Notas enviadas por mi sector
- get_received_notes: Notas recibidas por mi sector
- get_sent_notes_multi_sector: Notas enviadas por cualquiera de mis sectores
- get_received_notes_multi_sector: Notas recibidas en cualquiera de mis sectores
- record_opening: Registrar apertura de nota
- get_note_detail: Detalle completo de una nota
- get_note_detail_multi_sector: Detalle verificando acceso en múltiples sectores
- build_nota_header_html: Construir header HTML con destinatarios (para oficialización)
- inject_header_into_content: Inyectar header al inicio del contenido HTML
"""

from .validation import is_nota_document_type, validate_recipients_exist
from .save_recipients import save_recipients
from .recipients import get_visible_recipients, check_sector_access
from .retrieval import (
    get_sent_notes,
    get_received_notes,
    get_sent_notes_multi_sector,
    get_received_notes_multi_sector,
    get_archived_notes_multi_sector
)
from .archiving import toggle_note_archive, get_archive_status
from .tracking import record_opening, get_note_detail, get_note_detail_multi_sector
from .header_builder import build_nota_header_html, inject_header_into_content

__all__ = [
    'is_nota_document_type',
    'validate_recipients_exist',
    'save_recipients',
    'get_visible_recipients',
    'check_sector_access',
    'get_sent_notes',
    'get_received_notes',
    'get_sent_notes_multi_sector',
    'get_received_notes_multi_sector',
    'get_archived_notes_multi_sector',
    'toggle_note_archive',
    'get_archive_status',
    'record_opening',
    'get_note_detail',
    'get_note_detail_multi_sector',
    'build_nota_header_html',
    'inject_header_into_content',
]
