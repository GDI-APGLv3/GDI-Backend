"""
Módulo shared - Componentes reutilizables y utilidades comunes.

Este módulo contiene:
- database: Operaciones y conexiones de base de datos (asyncpg)
- validation: Funciones de validación compartidas
- utils: Utilidades generales
- exceptions: Excepciones personalizadas y manejo de errores
- config: Configuraciones centralizadas del sistema

Uso:
    from database import fetch_all, fetch_one, execute, transaction, get_conn
    from shared.validation import validate_uuid, validate_user_id
    from shared.utils import generate_uuid, format_datetime
    from shared.exceptions import DocumentNotFoundError, ValidationError
    from shared.config import PaginationConfig, APIConfig
"""

# Importaciones principales de database (asyncpg)
from database import (
    get_conn,
    fetch_all,
    fetch_one,
    fetch_val,
    execute,
    transaction,
    check_user_exists,
    check_document_exists,
    get_document_basic_info,
)

# Importaciones principales de validation
from .validation import (
    validate_uuid,
    validate_required_string,
    validate_user_id,
    validate_document_id,
    validate_document_reference,
    validate_rejection_reason,
    validate_document_type_acronym,
    validate_email,
    validate_pagination_params,
    validate_document_signers
)

# Importaciones principales de utils
from .utils import (
    generate_uuid,
    generate_document_number,
    format_datetime,
    format_date,
    parse_datetime,
    safe_json_loads,
    safe_json_dumps,
    clean_string,
    normalize_text,
    calculate_offset,
    calculate_total_pages,
    truncate_text,
    CustomJSONEncoder
)

# Importaciones principales de exceptions
from .exceptions import (
    GDIBaseException,
    ValidationError,
    BusinessLogicError,
    DatabaseError,
    AuthorizationError,
    NotFoundError,
    ConflictError,
    ExternalServiceError,
    DocumentNotFoundError,
    DocumentStateError,
    DocumentPermissionError,
    DocumentAlreadySignedError,
    DocumentAlreadyRejectedError,
    InvalidSignatureOrderError,
    NumeratorRequiredError,
    UserNotFoundError,
    UserInactiveError,
    exception_to_http_exception,
    handle_database_error,
    handle_exceptions,
    ErrorTypes,
    ErrorMessages
)

__all__ = [
    # Database (asyncpg)
    "get_conn",
    "fetch_all",
    "fetch_one",
    "fetch_val",
    "execute",
    "transaction",
    "check_user_exists",
    "check_document_exists",
    "get_document_basic_info",

    # Validation
    "validate_uuid",
    "validate_required_string",
    "validate_user_id",
    "validate_document_id",
    "validate_document_reference",
    "validate_rejection_reason",
    "validate_document_type_acronym",
    "validate_email",
    "validate_pagination_params",
    "validate_document_signers",

    # Utils
    "generate_uuid",
    "generate_document_number",
    "format_datetime",
    "format_date",
    "parse_datetime",
    "safe_json_loads",
    "safe_json_dumps",
    "clean_string",
    "normalize_text",
    "calculate_offset",
    "calculate_total_pages",
    "truncate_text",
    "CustomJSONEncoder",

    # Exceptions
    "GDIBaseException",
    "ValidationError",
    "BusinessLogicError",
    "DatabaseError",
    "AuthorizationError",
    "NotFoundError",
    "ConflictError",
    "ExternalServiceError",
    "DocumentNotFoundError",
    "DocumentStateError",
    "DocumentPermissionError",
    "DocumentAlreadySignedError",
    "DocumentAlreadyRejectedError",
    "InvalidSignatureOrderError",
    "NumeratorRequiredError",
    "UserNotFoundError",
    "UserInactiveError",
    "exception_to_http_exception",
    "handle_database_error",
    "handle_exceptions",
    "ErrorTypes",
    "ErrorMessages"
]
