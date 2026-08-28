
import contextvars
import uuid
from typing import Optional


correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'correlation_id',
    default=None
)

request_endpoint_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'request_endpoint',
    default=None
)


def get_correlation_id() -> str:
    current = correlation_id_var.get()
    if current is None:
        current = str(uuid.uuid4())
        correlation_id_var.set(current)
    return current


def set_correlation_id(correlation_id: Optional[str] = None) -> str:
    cid = correlation_id if correlation_id else str(uuid.uuid4())
    correlation_id_var.set(cid)
    return cid


def clear_correlation_id() -> None:
    correlation_id_var.set(None)


def set_request_endpoint(method: str, path: str) -> None:
    request_endpoint_var.set(f"{method} {path}")


def get_request_endpoint() -> Optional[str]:
    return request_endpoint_var.get()


def clear_request_endpoint() -> None:
    request_endpoint_var.set(None)
