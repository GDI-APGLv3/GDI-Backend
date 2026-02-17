"""
Contexto de ejecución usando contextvars.
Permite propagar correlation_id a través de toda la cadena de llamadas.
"""

import contextvars
import uuid
from typing import Optional


# ContextVar para correlation_id - default None significa "generar si no existe"
correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'correlation_id',
    default=None
)


def get_correlation_id() -> str:
    """
    Obtiene el correlation_id actual o genera uno nuevo si no existe.

    Returns:
        str: UUID del correlation_id
    """
    current = correlation_id_var.get()
    if current is None:
        current = str(uuid.uuid4())
        correlation_id_var.set(current)
    return current


def set_correlation_id(correlation_id: Optional[str] = None) -> str:
    """
    Establece el correlation_id para el contexto actual.
    Si no se proporciona, genera uno nuevo.

    Args:
        correlation_id: UUID string a establecer (opcional)

    Returns:
        str: El correlation_id establecido
    """
    cid = correlation_id if correlation_id else str(uuid.uuid4())
    correlation_id_var.set(cid)
    return cid


def clear_correlation_id() -> None:
    """
    Limpia el correlation_id del contexto actual.
    Útil para tests o reset entre requests.
    """
    correlation_id_var.set(None)
