"""
Servicio para vinculación de documentos y expedientes con legajos.
"""

import psycopg2

from shared.logging import get_logger
from database import execute_query, execute_update, execute_single_update, check_document_exists, check_case_exists
from shared.exceptions import NotFoundError, AuthorizationError, ConflictError
from services.rlm.queries import (
    get_record_detail_query,
    get_record_documents_query,
    get_record_documents_count_query,
    insert_document_link_query,
    delete_document_link_query,
    get_record_cases_query,
    get_record_cases_count_query,
    insert_case_link_query,
    delete_case_link_query,
    get_user_sector_info_query,
)
from services.rlm.permissions import check_permission
from services.rlm.history import record_action

logger = get_logger(__name__)


# ============================================================================
# DOCUMENTOS
# ============================================================================

def get_linked_documents(
    record_id: str,
    user_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Lista documentos vinculados a un legajo con paginacion."""
    from services.rlm.permissions import verify_record_view_permission
    verify_record_view_permission(record_id, user_id, schema_name=schema_name)

    # Count
    count_result = execute_query(
        get_record_documents_count_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    total = count_result["total"] if count_result else 0

    # List
    offset = (page - 1) * page_size
    results = execute_query(
        get_record_documents_query(),
        (record_id, page_size, offset),
        schema_name=schema_name
    )

    documents = [
        {
            "link_id": row["link_id"],
            "document_id": row["document_id"],
            "field_name": row["field_name"],
            "notes": row["notes"],
            "linked_at": str(row["created_at"]),
            "official_number": row.get("official_number"),
            "reference": row.get("reference"),
            "document_type": row.get("document_type"),
            "is_auto": row.get("document_type") == "IFRLM",
            "resume": row.get("resume", ""),
        }
        for row in (results or [])
    ]

    total_pages = max(1, -(-total // page_size))

    return {
        "documents": documents,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def link_document(
    record_id: str,
    document_id: str,
    user_id: str,
    field_name: str = None,
    notes: str = None,
    *,
    schema_name: str
) -> dict:
    """Vincula un documento oficial a un legajo."""
    record = execute_query(
        get_record_detail_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    # Validar que el documento existe
    if not check_document_exists(document_id, schema_name=schema_name):
        raise NotFoundError(f"Documento con ID '{document_id}' no encontrado")

    if not check_permission(record["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para vincular documentos a este legajo")

    try:
        execute_update(
            insert_document_link_query(),
            (record_id, document_id, field_name, notes, user_id),
            schema_name=schema_name
        )
    except psycopg2.IntegrityError:
        raise ConflictError(
            f"El documento '{document_id}' ya esta vinculado al legajo '{record_id}'"
        )

    user_info = execute_query(
        get_user_sector_info_query(),
        (user_id,),
        schema_name=schema_name,
        fetch_one=True
    )

    # Fetch document info for enriched history
    doc_info = execute_query(
        """SELECT COALESCE(od.official_number, '') as official_number,
                  COALESCE(od.reference, dd.reference, '') as reference,
                  COALESCE(od.short_resume, dd.short_resume, '') as short_resume
           FROM official_documents od
           LEFT JOIN document_draft dd ON od.id = dd.id
           WHERE od.id = %s
             AND od.signed_at IS NOT NULL""",
        (document_id,),
        schema_name=schema_name,
        fetch_one=True
    )

    record_action(
        record_id=record_id,
        action="document_linked",
        user_id=user_id,
        sector_id=user_info.get("sector_id") if user_info else None,
        after_value={
            "document_id": document_id,
            "field_name": field_name,
            "official_number": doc_info.get("official_number", "") if doc_info else "",
            "reference": doc_info.get("reference", "") if doc_info else "",
            "short_resume": doc_info.get("short_resume", "") if doc_info else "",
        },
        schema_name=schema_name
    )

    logger.info(f"Document {document_id[:8]} linked to record {record_id[:8]}")
    return {"record_id": record_id, "document_id": document_id}


def unlink_document(record_id: str, link_id: str, user_id: str, *, schema_name: str) -> dict:
    """Desvincula un documento de un legajo."""
    record = execute_query(
        get_record_detail_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    if not check_permission(record["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para desvincular documentos de este legajo")

    # Fetch document info BEFORE deleting for history
    doc_info = execute_query(
        """SELECT rdl.document_id, COALESCE(od.official_number, '') as official_number,
                  COALESCE(od.reference, dd.reference, '') as reference
           FROM record_document_links rdl
           LEFT JOIN official_documents od ON rdl.document_id = od.id AND od.signed_at IS NOT NULL
           LEFT JOIN document_draft dd ON rdl.document_id = dd.id
           WHERE rdl.id = %s AND rdl.record_id = %s""",
        (link_id, record_id),
        schema_name=schema_name,
        fetch_one=True
    )

    result = execute_single_update(
        delete_document_link_query(),
        (link_id, record_id),
        returning=True,
        schema_name=schema_name
    )

    if result["rows_affected"] == 0:
        raise NotFoundError(f"Vinculo de documento con ID '{link_id}' no encontrado en legajo '{record_id}'")

    record_action(
        record_id=record_id,
        action="document_unlinked",
        user_id=user_id,
        after_value={
            "link_id": link_id,
            "official_number": doc_info.get("official_number", "") if doc_info else "",
            "reference": doc_info.get("reference", "") if doc_info else "",
        },
        schema_name=schema_name
    )

    logger.info(f"Document link {link_id} removed from record {record_id[:8]}")
    return {"removed": True}


# ============================================================================
# EXPEDIENTES (CASES)
# ============================================================================

def get_linked_cases(
    record_id: str,
    user_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Lista expedientes vinculados a un legajo con paginacion."""
    from services.rlm.permissions import verify_record_view_permission
    verify_record_view_permission(record_id, user_id, schema_name=schema_name)

    # Count
    count_result = execute_query(
        get_record_cases_count_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    total = count_result["total"] if count_result else 0

    # List
    offset = (page - 1) * page_size
    results = execute_query(
        get_record_cases_query(),
        (record_id, page_size, offset),
        schema_name=schema_name
    )

    cases = [
        {
            "link_id": row["link_id"],
            "case_id": row["case_id"],
            "notes": row["notes"],
            "linked_at": str(row["created_at"]),
            "case_number": row.get("case_number"),
            "reference": row.get("case_reference"),
            "short_ai_summary": row.get("short_ai_summary", ""),
        }
        for row in (results or [])
    ]

    total_pages = max(1, -(-total // page_size))

    return {
        "cases": cases,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def link_case(
    record_id: str,
    case_id: str,
    user_id: str,
    notes: str = None,
    *,
    schema_name: str
) -> dict:
    """Vincula un expediente a un legajo."""
    record = execute_query(
        get_record_detail_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    # Validar que el expediente existe
    if not check_case_exists(case_id, schema_name=schema_name):
        raise NotFoundError(f"Expediente con ID '{case_id}' no encontrado")

    if not check_permission(record["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para vincular expedientes a este legajo")

    try:
        execute_update(
            insert_case_link_query(),
            (record_id, case_id, notes, user_id),
            schema_name=schema_name
        )
    except psycopg2.IntegrityError:
        raise ConflictError(
            f"El expediente '{case_id}' ya esta vinculado al legajo '{record_id}'"
        )

    user_info = execute_query(
        get_user_sector_info_query(),
        (user_id,),
        schema_name=schema_name,
        fetch_one=True
    )

    # Fetch case info for enriched history
    case_info = execute_query(
        """SELECT case_number, reference, COALESCE(short_ai_summary, '') as short_ai_summary
           FROM cases WHERE id = %s""",
        (case_id,),
        schema_name=schema_name,
        fetch_one=True
    )

    record_action(
        record_id=record_id,
        action="case_linked",
        user_id=user_id,
        sector_id=user_info.get("sector_id") if user_info else None,
        after_value={
            "case_id": case_id,
            "case_number": case_info.get("case_number", "") if case_info else "",
            "reference": case_info.get("reference", "") if case_info else "",
            "short_ai_summary": case_info.get("short_ai_summary", "") if case_info else "",
        },
        schema_name=schema_name
    )

    logger.info(f"Case {case_id[:8]} linked to record {record_id[:8]}")
    return {"record_id": record_id, "case_id": case_id}


def unlink_case(record_id: str, link_id: str, user_id: str, *, schema_name: str) -> dict:
    """Desvincula un expediente de un legajo."""
    record = execute_query(
        get_record_detail_query(),
        (record_id,),
        schema_name=schema_name,
        fetch_one=True
    )
    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    if not check_permission(record["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para desvincular expedientes de este legajo")

    # Fetch case info BEFORE deleting for history
    case_info = execute_query(
        """SELECT rcl.case_id, c.case_number, c.reference
           FROM record_case_links rcl
           LEFT JOIN cases c ON rcl.case_id = c.id
           WHERE rcl.id = %s AND rcl.record_id = %s""",
        (link_id, record_id),
        schema_name=schema_name,
        fetch_one=True
    )

    result = execute_single_update(
        delete_case_link_query(),
        (link_id, record_id),
        returning=True,
        schema_name=schema_name
    )

    if result["rows_affected"] == 0:
        raise NotFoundError(f"Vinculo de expediente con ID '{link_id}' no encontrado en legajo '{record_id}'")

    record_action(
        record_id=record_id,
        action="case_unlinked",
        user_id=user_id,
        after_value={
            "link_id": link_id,
            "case_number": case_info.get("case_number", "") if case_info else "",
            "reference": case_info.get("reference", "") if case_info else "",
        },
        schema_name=schema_name
    )

    logger.info(f"Case link {link_id} removed from record {record_id[:8]}")
    return {"removed": True}
