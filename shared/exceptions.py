"""
Excepciones personalizadas centralizadas para manejo consistente de errores.
Define la jerarquía de excepciones de la aplicación.
"""

from typing import Any, Dict, Optional
from fastapi import HTTPException, status
import logging

_logger = logging.getLogger(__name__)

class GDIBaseException(Exception):
    """
    Excepción base para todas las excepciones personalizadas de GDI.
    """
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)

class ValidationError(GDIBaseException):
    """
    Error de validación de datos.
    """
    pass

class BusinessLogicError(GDIBaseException):
    """
    Error de lógica de negocio.
    """
    pass

class DatabaseError(GDIBaseException):
    """
    Error de base de datos.
    """
    pass

class AuthorizationError(GDIBaseException):
    """
    Error de autorización/permisos.
    """
    pass

class NotFoundError(GDIBaseException):
    """
    Error cuando un recurso no se encuentra.
    """
    pass

class ConflictError(GDIBaseException):
    """
    Error de conflicto (recurso ya existe, estado inválido, etc.).
    """
    pass

class ExternalServiceError(GDIBaseException):
    """
    Error al comunicarse con servicios externos.
    """
    pass

# Excepciones específicas del dominio de documentos

class DocumentNotFoundError(NotFoundError):
    """
    Error cuando un documento no se encuentra.
    """
    def __init__(self, document_id: str):
        super().__init__(f"Documento con ID '{document_id}' no encontrado")
        self.document_id = document_id

class DocumentStateError(BusinessLogicError):
    """
    Error relacionado con el estado de un documento.
    """
    def __init__(self, message: str, current_state: str, required_state: str = None):
        super().__init__(message)
        self.current_state = current_state
        self.required_state = required_state

class DocumentPermissionError(AuthorizationError):
    """
    Error de permisos sobre un documento.
    """
    def __init__(self, user_id: str, document_id: str, action: str):
        super().__init__(f"Usuario '{user_id}' no tiene permisos para '{action}' en documento '{document_id}'")
        self.user_id = user_id
        self.document_id = document_id
        self.action = action

class DocumentAlreadySignedError(ConflictError):
    """
    Error cuando se intenta firmar un documento ya firmado.
    """
    def __init__(self, document_id: str, user_id: str):
        super().__init__(f"El documento '{document_id}' ya fue firmado por el usuario '{user_id}'")
        self.document_id = document_id
        self.user_id = user_id

class DocumentAlreadyRejectedError(ConflictError):
    """
    Error cuando se intenta operar sobre un documento ya rechazado.
    """
    def __init__(self, document_id: str):
        super().__init__(f"El documento '{document_id}' ya fue rechazado y no puede ser modificado")
        self.document_id = document_id

class InvalidSignatureOrderError(BusinessLogicError):
    """
    Error cuando se intenta firmar fuera del orden establecido.
    """
    def __init__(self, document_id: str, user_id: str, expected_signer: str):
        super().__init__(f"Orden de firma incorrecto en documento '{document_id}'. Usuario '{user_id}' no puede firmar, se esperaba '{expected_signer}'")
        self.document_id = document_id
        self.user_id = user_id
        self.expected_signer = expected_signer

class NumeratorRequiredError(BusinessLogicError):
    """
    Error cuando se requiere numerador pero no hay uno disponible.
    """
    def __init__(self, document_id: str):
        super().__init__(f"El documento '{document_id}' requiere numeración pero no tiene numerador asignado")
        self.document_id = document_id

# Excepciones específicas del dominio de usuarios

class UserNotFoundError(NotFoundError):
    """
    Error cuando un usuario no se encuentra.
    """
    def __init__(self, user_id: str):
        super().__init__(f"Usuario con ID '{user_id}' no encontrado")
        self.user_id = user_id

class UserInactiveError(BusinessLogicError):
    """
    Error cuando un usuario está inactivo.
    """
    def __init__(self, user_id: str):
        super().__init__(f"Usuario con ID '{user_id}' está inactivo")
        self.user_id = user_id

# Excepciones específicas del dominio de casos/expedientes

class CaseNotFoundError(NotFoundError):
    """
    Error cuando un caso/expediente no se encuentra.
    """
    def __init__(self, case_id: str):
        super().__init__(f"Expediente con ID '{case_id}' no encontrado")
        self.case_id = case_id

class CasePermissionError(AuthorizationError):
    """
    Error de permisos sobre un caso/expediente.
    """
    def __init__(self, user_id: str, case_id: str, action: str):
        super().__init__(f"Usuario '{user_id}' no tiene permisos para '{action}' en expediente '{case_id}'")
        self.user_id = user_id
        self.case_id = case_id
        self.action = action

class CaseStateError(BusinessLogicError):
    """
    Error relacionado con el estado de un caso/expediente.
    """
    def __init__(self, message: str, current_state: str = None, required_state: str = None):
        super().__init__(message)
        self.current_state = current_state
        self.required_state = required_state

# Excepciones específicas del dominio de sectores

class SectorNotFoundError(NotFoundError):
    """
    Error cuando un sector no se encuentra.
    """
    def __init__(self, sector_id: str):
        super().__init__(f"Sector con ID '{sector_id}' no encontrado")
        self.sector_id = sector_id

class SectorInactiveError(BusinessLogicError):
    """
    Error cuando un sector está inactivo.
    """
    def __init__(self, sector_id: str):
        super().__init__(f"Sector con ID '{sector_id}' está inactivo")
        self.sector_id = sector_id

# Funciones para convertir excepciones a HTTPException

def exception_to_http_exception(exc: Exception) -> HTTPException:
    """
    Convierte excepciones personalizadas a HTTPException de FastAPI.
    
    Args:
        exc: Excepción (puede ser GDIBaseException o cualquier Exception)
        
    Returns:
        HTTPException correspondiente
    """
    # Si no es una excepción personalizada, manejarla como error genérico
    if not isinstance(exc, GDIBaseException):
        _logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}")
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
    
    # Mapeo de excepciones a códigos HTTP
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
    
    elif isinstance(exc, DatabaseError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Error interno del servidor",
                "type": "DatabaseError"
            }
        )
    
    elif isinstance(exc, ExternalServiceError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_detail
        )
    
    else:
        # Error genérico
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Error interno del servidor",
                "type": "InternalServerError"
            }
        )

def handle_database_error(error: Exception, operation: str) -> DatabaseError:
    """
    Maneja errores de base de datos y los convierte a excepciones personalizadas.
    
    Args:
        error: Error original de la base de datos
        operation: Descripción de la operación que falló
        
    Returns:
        DatabaseError con información del error
    """
    import psycopg2
    
    if isinstance(error, psycopg2.IntegrityError):
        return DatabaseError(
            f"Error de integridad en {operation}",
            details={"original_error": str(error), "type": "IntegrityError"}
        )
    
    elif isinstance(error, psycopg2.OperationalError):
        return DatabaseError(
            f"Error de conexión en {operation}",
            details={"original_error": str(error), "type": "OperationalError"}
        )
    
    elif isinstance(error, psycopg2.ProgrammingError):
        return DatabaseError(
            f"Error de programación en {operation}",
            details={"original_error": str(error), "type": "ProgrammingError"}
        )
    
    else:
        return DatabaseError(
            f"Error de base de datos en {operation}",
            details={"original_error": str(error), "type": "UnknownDatabaseError"}
        )

def validate_and_raise(condition: bool, exception_class: type, message: str, **kwargs):
    """
    Valida una condición y lanza excepción si es falsa.
    
    Args:
        condition: Condición a validar
        exception_class: Clase de excepción a lanzar
        message: Mensaje de error
        **kwargs: Argumentos adicionales para la excepción
    """
    if not condition:
        raise exception_class(message, **kwargs)

# Decorador para manejo automático de excepciones
def handle_exceptions(func):
    """
    Decorador para manejo automático de excepciones personalizadas.
    Convierte excepciones GDI a HTTPException automáticamente.
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except GDIBaseException as exc:
            raise exception_to_http_exception(exc)
        except Exception as exc:
            # Log del error no manejado (aquí podrías agregar logging)
            _logger.error(f"Error no manejado en {func.__name__}: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "message": "Error interno del servidor",
                    "type": "UnhandledException"
                }
            )
    
    return wrapper

# Constantes para tipos de error comunes
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

# Mensajes de error comunes
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