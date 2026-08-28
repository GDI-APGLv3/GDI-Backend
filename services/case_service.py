
from shared.logging import get_logger
from services.cases.permissions import (
    _calculate_access_reason,
    get_user_editable_sector_ids,
    get_user_viewable_sector_ids,
    get_user_case_permissions,
    can_user_view_case,
    can_user_edit_case,
)
from services.shared.sector_utils import get_user_sector_ids as _get_user_sector_ids
from services.cases.history import (
    get_case_movements,
    create_movement,
    get_case_history,
)
from services.cases.retrieval import (
    get_cases_by_user,
    get_cases_summary,
)
from services.cases.queries import (
    get_case_detail,
    get_case_by_exact_number,
    get_case_by_exact_number_unrestricted,
    get_available_templates,
)
from services.cases.transfer import (
    transfer_case,
    close_assignment,
    get_available_sectors_for_transfer,
    get_sector_users,
)
from services.cases.documents import (
    get_case_documents,
    link_official_document,
    accept_proposed_document,
    reject_proposed_document,
)
from services.cases.creation import create_case_with_cover_service
from services.cases.core import create_case

logger = get_logger(__name__)


class CaseService:

    _calculate_access_reason = staticmethod(_calculate_access_reason)
    get_user_editable_sector_ids = staticmethod(get_user_editable_sector_ids)
    get_user_viewable_sector_ids = staticmethod(get_user_viewable_sector_ids)
    _get_user_sector_ids = staticmethod(_get_user_sector_ids)
    get_user_case_permissions = staticmethod(get_user_case_permissions)
    can_user_view_case = staticmethod(can_user_view_case)
    can_user_edit_case = staticmethod(can_user_edit_case)

    get_case_movements = staticmethod(get_case_movements)
    create_movement = staticmethod(create_movement)
    get_case_history = staticmethod(get_case_history)

    get_cases_by_user = staticmethod(get_cases_by_user)
    get_cases_summary = staticmethod(get_cases_summary)

    get_case_detail = staticmethod(get_case_detail)
    get_case_by_exact_number = staticmethod(get_case_by_exact_number)
    get_case_by_exact_number_unrestricted = staticmethod(get_case_by_exact_number_unrestricted)
    get_available_templates = staticmethod(get_available_templates)

    transfer_case = staticmethod(transfer_case)
    close_assignment = staticmethod(close_assignment)
    get_available_sectors_for_transfer = staticmethod(get_available_sectors_for_transfer)
    get_sector_users = staticmethod(get_sector_users)

    get_case_documents = staticmethod(get_case_documents)
    link_official_document = staticmethod(link_official_document)
    accept_proposed_document = staticmethod(accept_proposed_document)
    reject_proposed_document = staticmethod(reject_proposed_document)

    create_case = staticmethod(create_case)
    create_case_with_cover_service = staticmethod(create_case_with_cover_service)


__all__ = [
    'CaseService',
    '_calculate_access_reason',
    'get_user_editable_sector_ids',
    'get_user_viewable_sector_ids',
    '_get_user_sector_ids',
    'get_user_case_permissions',
    'can_user_view_case',
    'get_case_movements',
    'create_movement',
    'get_case_history',
    'get_cases_by_user',
    'get_cases_summary',
    'get_case_detail',
    'get_case_by_exact_number',
    'get_case_by_exact_number_unrestricted',
    'get_available_templates',
    'transfer_case',
    'close_assignment',
    'get_available_sectors_for_transfer',
    'get_sector_users',
    'get_case_documents',
    'link_official_document',
    'accept_proposed_document',
    'reject_proposed_document',
    'create_case',
    'create_case_with_cover_service',
]
