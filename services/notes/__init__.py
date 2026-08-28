
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
