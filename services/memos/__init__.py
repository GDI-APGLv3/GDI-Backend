"""
Modulo de servicios para MEMOS.
Sistema de comunicacion privada persona-a-persona con destinatarios TO/CC/BCC y tracking de apertura.

A diferencia de NOTAS (sector-a-sector), MEMOS envia a usuarios individuales (user_id).
No hay variantes multi_sector: la query es directa por user_id.

Funciones principales:
- save_memo_recipients: Guardar destinatarios al crear un MEMO
- validate_memo_recipients: Validar que user_ids existan y sean validos
- get_visible_memo_recipients: Obtener recipients segun permisos (BCC solo para sender)
- get_received_memos: Memos recibidos por un usuario
- get_sent_memos: Memos enviados por un usuario
- get_archived_memos: Memos archivados por un usuario
- record_memo_opening: Registrar apertura de memo (inline en memo_recipients.opened_at)
- get_memo_detail: Detalle completo de un memo
- build_memo_header_html: Construir header HTML con destinatarios (para oficializacion)
- inject_header_into_content: Inyectar header al inicio del contenido HTML
- toggle_memo_archive: Archivar/desarchivar memo
- get_unread_memo_count: Contador de memos no leidos (badge)
"""

from .validation import (
    is_memo_document_type,
    is_memo_document_type_by_acronym,
    get_memo_document_type_id,
    validate_memo_recipients_exist,
    validate_memo_recipients_input,
    is_memo_document_type_by_id,
    validate_memo_recipients_for_signing,
)
from .save_recipients import save_memo_recipients, delete_memo_recipients
from .recipients import (
    get_visible_memo_recipients,
    get_memo_sender_user,
    check_memo_user_access,
    format_memo_recipients_for_pdf,
)
from .retrieval import get_received_memos, get_sent_memos, get_archived_memos
from .archiving import toggle_memo_archive, get_memo_archive_status
from .tracking import record_memo_opening, get_memo_detail
from .header_builder import build_memo_header_html, inject_header_into_content, remove_existing_header
from .unread import get_unread_memo_count

__all__ = [
    'is_memo_document_type',
    'is_memo_document_type_by_acronym',
    'get_memo_document_type_id',
    'validate_memo_recipients_exist',
    'validate_memo_recipients_input',
    'is_memo_document_type_by_id',
    'validate_memo_recipients_for_signing',
    'save_memo_recipients',
    'delete_memo_recipients',
    'get_visible_memo_recipients',
    'get_memo_sender_user',
    'check_memo_user_access',
    'format_memo_recipients_for_pdf',
    'get_received_memos',
    'get_sent_memos',
    'get_archived_memos',
    'toggle_memo_archive',
    'get_memo_archive_status',
    'record_memo_opening',
    'get_memo_detail',
    'build_memo_header_html',
    'inject_header_into_content',
    'remove_existing_header',
    'get_unread_memo_count',
]
