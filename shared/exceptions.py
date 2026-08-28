
import asyncio

import asyncpg
from typing import Any, Dict, Optional
from fastapi import HTTPException, status
from shared.logging import get_logger

_logger = get_logger(__name__)

class GDIBaseException(Exception):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)

class ValidationError(GDIBaseException):
    pass

class BusinessLogicError(GDIBaseException):
    pass

class DatabaseError(GDIBaseException):
    pass

class DatabaseBusyError(DatabaseError):
    pass

class TransientLookupError(DatabaseBusyError):
    pass

class AuthorizationError(GDIBaseException):
    pass

class NotFoundError(GDIBaseException):
    pass

class ConflictError(GDIBaseException):
    pass

class StaleReservationError(ConflictError):
    def __init__(self, document_id: str, reservation_id: str | None = None):
        msg = f"La reserva de numeración del documento '{document_id}' ya no está vigente"
        if reservation_id:
            msg += f" (ticket {reservation_id[:8]}...)"
        super().__init__(msg)
        self.document_id = document_id
        self.reservation_id = reservation_id


class SpecialLaneBusyError(ConflictError):
    def __init__(self, document_type_id: str, department_id: str, year: int):
        msg = (
            "Hay una numeración en curso para este tipo de documento en su departamento. "
            "Reintentá en unos segundos."
        )
        super().__init__(msg)
        self.document_type_id = document_type_id
        self.department_id = department_id
        self.year = year

class EscriQueueFullError(ConflictError):
    def __init__(self, reason: str, retry_after: int = 30):
        msg = (
            "El sistema de firma está temporalmente saturado. "
            f"Reintentá en ~{retry_after} segundos."
        )
        super().__init__(msg)
        self.reason = reason
        self.retry_after = retry_after


class ExternalServiceError(GDIBaseException):
    pass


class NotaryError(ExternalServiceError):
    pass


class NotaryUnavailableError(NotaryError):
    pass


class NotaryTimeoutError(NotaryUnavailableError):
    pass


class NotaryBusinessError(NotaryError):
    pass


class NotaryBreakerOpenError(NotaryError):
    def __init__(self, retry_after: int = 30):
        msg = (
            "El servicio de firma está en mantenimiento. "
            f"Reintentá en ~{retry_after} segundos."
        )
        super().__init__(msg)
        self.retry_after = retry_after


class R2Error(ExternalServiceError):
    def __init__(self, message: str, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


class R2ObjectLockedError(R2Error):
    pass


class PreOficialNotProvisionedError(R2Error):
    pass


class DocumentNotFoundError(NotFoundError):
    def __init__(self, document_id: str):
        super().__init__(f"Documento con ID '{document_id}' no encontrado")
        self.document_id = document_id

class DocumentStateError(BusinessLogicError):
    def __init__(self, message: str, current_state: str, required_state: str = None):
        super().__init__(message)
        self.current_state = current_state
        self.required_state = required_state

class DocumentPermissionError(AuthorizationError):
    def __init__(self, user_id: str, document_id: str, action: str):
        super().__init__(f"Usuario '{user_id}' no tiene permisos para '{action}' en documento '{document_id}'")
        self.user_id = user_id
        self.document_id = document_id
        self.action = action

class DocumentAlreadySignedError(ConflictError):
    def __init__(self, document_id: str, user_id: str):
        super().__init__(f"El documento '{document_id}' ya fue firmado por el usuario '{user_id}'")
        self.document_id = document_id
        self.user_id = user_id

class DocumentAlreadyRejectedError(ConflictError):
    def __init__(self, document_id: str):
        super().__init__(f"El documento '{document_id}' ya fue rechazado y no puede ser modificado")
        self.document_id = document_id

class InvalidSignatureOrderError(BusinessLogicError):
    def __init__(self, document_id: str, user_id: str, expected_signer: str):
        super().__init__(f"Orden de firma incorrecto en documento '{document_id}'. Usuario '{user_id}' no puede firmar, se esperaba '{expected_signer}'")
        self.document_id = document_id
        self.user_id = user_id
        self.expected_signer = expected_signer

class SignerTurnPendingError(ConflictError):
    def __init__(self, pending_common_signers: int):
        msg = (
            "Todavía no es tu turno de firmar: "
            f"queda(n) {pending_common_signers} firma(s) anterior(es) en proceso. "
            "El numerador firma al final — reintentá en unos segundos."
        )
        super().__init__(msg)
        self.pending_common_signers = pending_common_signers

class NumeratorRequiredError(BusinessLogicError):
    def __init__(self, document_id: str):
        super().__init__(f"El documento '{document_id}' requiere numeración pero no tiene numerador asignado")
        self.document_id = document_id


class UserNotFoundError(NotFoundError):
    def __init__(self, user_id: str):
        super().__init__(f"Usuario con ID '{user_id}' no encontrado")
        self.user_id = user_id

class UserInactiveError(BusinessLogicError):
    def __init__(self, user_id: str):
        super().__init__(f"Usuario con ID '{user_id}' está inactivo")
        self.user_id = user_id


class CasePermissionError(AuthorizationError):
    def __init__(self, user_id: str, case_id: str, action: str):
        super().__init__(f"Usuario '{user_id}' no tiene permisos para '{action}' en expediente '{case_id}'")
        self.user_id = user_id
        self.case_id = case_id
        self.action = action


class IsLastTaskError(BusinessLogicError):
    pass


class DocumentSignedWhileRejectingError(ConflictError):
    def __init__(self, document_id: str):
        super().__init__(
            f"El documento '{document_id}' fue firmado y convertido en acto oficial "
            "durante el proceso de rechazo — la operación fue abortada para preservar "
            "la integridad del acto administrativo."
        )
        self.document_id = document_id


class DocumentRejectedWhileInQueueError(ConflictError):
    def __init__(self, document_id: str):
        super().__init__(
            f"El documento '{document_id}' fue rechazado antes de que el worker "
            "pudiera firmarlo — la firma async se aborta."
        )
        self.document_id = document_id


class NumeratorPreCasError(ConflictError):
    pass


class NumeratorUploadError(ConflictError):
    pass


class NotaryHashMismatchError(NotaryBusinessError):
    pass


_TRANSIENT_DB_ERRORS: tuple = (
    DatabaseBusyError,
    asyncio.TimeoutError,
    TimeoutError,
    asyncpg.exceptions.QueryCanceledError,
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.InterfaceError,
    asyncpg.exceptions.TooManyConnectionsError,
    asyncpg.exceptions.CannotConnectNowError,
    asyncpg.exceptions.PostgresConnectionError,
    ConnectionResetError,
    OSError,
)


def is_transient_db_error(exc: Exception) -> bool:
    return isinstance(exc, _TRANSIENT_DB_ERRORS)


def reraise_if_transient(exc: Exception, *, context: str) -> None:
    if is_transient_db_error(exc):
        raise TransientLookupError(
            f"No se pudo completar la consulta ({context}): base de datos saturada",
            details={"context": context, "cause": type(exc).__name__},
        ) from exc


def causada_por_pool_saturado(exc: BaseException, _profundidad_max: int = 12) -> bool:
    vistas = set()
    actual = exc
    for _ in range(_profundidad_max):
        if actual is None or id(actual) in vistas:
            return False
        vistas.add(id(actual))
        if isinstance(actual, DatabaseBusyError):
            return True
        actual = actual.__cause__ or actual.__context__
    return False


def exception_to_http_exception(exc: Exception) -> HTTPException:
    if not isinstance(exc, DatabaseBusyError) and causada_por_pool_saturado(exc):
        _logger.warning(
            "[GDI-372] %s enmascaraba un pool saturado; se responde 503",
            type(exc).__name__,
        )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "1"},
            detail={
                "message": "Servidor ocupado, reintente en unos segundos",
                "type": "DatabaseBusyError"
            }
        )

    if not isinstance(exc, GDIBaseException):
        _logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}")
        try:
            from shared.error_alerts import report_error
            report_error(None, exc, kind="UNHANDLED")
        except Exception:
            pass
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Error interno del servidor",
                "type": "InternalServerError"
            }
        )
    
    error_detail = {
        "message": exc.message,
        "type": exc.__class__.__name__
    }
    
    if exc.details:
        error_detail["details"] = exc.details
    
    if isinstance(exc, ValidationError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail
        )
    
    elif isinstance(exc, AuthorizationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail
        )
    
    elif isinstance(exc, NotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_detail
        )
    
    elif isinstance(exc, ConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_detail
        )
    
    elif isinstance(exc, BusinessLogicError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=error_detail
        )
    
    elif isinstance(exc, TransientLookupError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "5"},
            detail={
                "message": exc.message,
                "type": "TransientLookupError",
            }
        )

    elif isinstance(exc, DatabaseBusyError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "1"},
            detail={
                "message": "Servidor ocupado, reintente en unos segundos",
                "type": "DatabaseBusyError"
            }
        )

    elif isinstance(exc, DatabaseError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Error interno del servidor",
                "type": "DatabaseError"
            }
        )
    
    elif isinstance(exc, NotaryBreakerOpenError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": str(exc.retry_after)},
            detail=error_detail,
        )

    elif isinstance(exc, ExternalServiceError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_detail
        )
    
    else:
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Error interno del servidor",
                "type": "InternalServerError"
            }
        )

def handle_database_error(error: Exception, operation: str) -> DatabaseError:
    import asyncpg

    if isinstance(error, asyncpg.IntegrityConstraintViolationError):
        return DatabaseError(
            f"Error de integridad en {operation}",
            details={"original_error": str(error), "type": "IntegrityError"}
        )

    elif isinstance(error, (asyncpg.PostgresConnectionError, asyncpg.TooManyConnectionsError)):
        return DatabaseError(
            f"Error de conexión en {operation}",
            details={"original_error": str(error), "type": "OperationalError"}
        )

    elif isinstance(error, (asyncpg.PostgresSyntaxError, asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError)):
        return DatabaseError(
            f"Error de programación en {operation}",
            details={"original_error": str(error), "type": "ProgrammingError"}
        )

    else:
        return DatabaseError(
            f"Error de base de datos en {operation}",
            details={"original_error": str(error), "type": "UnknownDatabaseError"}
        )

def handle_exceptions(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except GDIBaseException as exc:
            raise exception_to_http_exception(exc)
        except Exception as exc:
            _logger.error(f"Error no manejado en {func.__name__}: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "message": "Error interno del servidor",
                    "type": "UnhandledException"
                }
            )
    
    return wrapper

class ErrorTypes:
    VALIDATION_ERROR = "ValidationError"
    NOT_FOUND = "NotFoundError"
    UNAUTHORIZED = "UnauthorizedError"
    FORBIDDEN = "ForbiddenError"
    CONFLICT = "ConflictError"
    BUSINESS_LOGIC = "BusinessLogicError"
    DATABASE_ERROR = "DatabaseError"
    EXTERNAL_SERVICE = "ExternalServiceError"
    INTERNAL_ERROR = "InternalServerError"

class ErrorMessages:
    DOCUMENT_NOT_FOUND = "Documento no encontrado"
    USER_NOT_FOUND = "Usuario no encontrado"
    INVALID_DOCUMENT_STATE = "Estado de documento inválido para esta operación"
    PERMISSION_DENIED = "No tiene permisos para realizar esta acción"
    ALREADY_SIGNED = "El documento ya fue firmado por este usuario"
    ALREADY_REJECTED = "El documento ya fue rechazado"
    INVALID_SIGNATURE_ORDER = "Orden de firma incorrecto"
    NUMERATOR_REQUIRED = "Se requiere numerador para completar esta operación"
    VALIDATION_FAILED = "Error de validación de datos"
    DATABASE_CONNECTION_ERROR = "Error de conexión a la base de datos"
    EXTERNAL_SERVICE_UNAVAILABLE = "Servicio externo no disponible"