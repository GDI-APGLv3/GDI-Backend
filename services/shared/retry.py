
import logging
from shared.logging import get_logger
import httpx
from functools import wraps
from typing import Callable, Any, Optional
from dataclasses import dataclass

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    RetryError
)

from shared.exceptions import ExternalServiceError

logger = get_logger(__name__)


@dataclass
class RetryConfig:
    max_attempts: int = 2
    min_wait: float = 1.0
    max_wait: float = 10.0
    timeout: float = 90.0


SERVICE_CONFIGS = {
    "pdfcomposer": RetryConfig(max_attempts=2, min_wait=1.0, max_wait=10.0, timeout=90.0),
    "notary": RetryConfig(max_attempts=3, min_wait=2.0, max_wait=30.0, timeout=120.0),
    "agente": RetryConfig(max_attempts=2, min_wait=1.0, max_wait=5.0, timeout=60.0),
}


def get_service_config(service_name: str) -> RetryConfig:
    return SERVICE_CONFIGS.get(service_name, RetryConfig())


def retry_async_call(
    service_name: str,
    config: Optional[RetryConfig] = None
):
    if config is None:
        config = get_service_config(service_name)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            @retry(
                stop=stop_after_attempt(config.max_attempts),
                wait=wait_exponential(
                    multiplier=config.min_wait,
                    max=config.max_wait
                ),
                retry=retry_if_exception_type((
                    httpx.TimeoutException,
                    httpx.RequestError,
                    httpx.ConnectError,
                    httpx.ReadTimeout,
                    httpx.WriteTimeout,
                )),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                reraise=True
            )
            async def _call_with_retry():
                return await func(*args, **kwargs)

            try:
                return await _call_with_retry()

            except httpx.TimeoutException as e:
                logger.error(f"Timeout después de {config.max_attempts} intentos")
                raise ExternalServiceError(
                    f"{service_name} timeout después de {config.timeout}s",
                    details={"service": service_name, "error": "timeout"}
                )

            except httpx.RequestError as e:
                logger.error(f"Error de conexión: {str(e)}")
                raise ExternalServiceError(
                    f"Error de conexión con {service_name}: {str(e)}",
                    details={"service": service_name, "error": str(e)}
                )

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP {e.response.status_code}")
                raise ExternalServiceError(
                    f"{service_name} error: HTTP {e.response.status_code}",
                    details={
                        "service": service_name,
                        "status_code": e.response.status_code,
                        "response": e.response.text[:500] if e.response.text else None
                    }
                )

            except ExternalServiceError:
                raise

            except RetryError as e:
                original = e.last_attempt.exception()
                logger.error(f"Fallaron todos los intentos: {str(original)}")
                raise ExternalServiceError(
                    f"{service_name}: fallaron todos los intentos",
                    details={"service": service_name, "last_error": str(original)}
                )

            except Exception as e:
                logger.error(f"Error inesperado: {type(e).__name__} - {str(e)}")
                raise ExternalServiceError(
                    f"Error inesperado en {service_name}: {str(e)}",
                    details={"service": service_name, "error_type": type(e).__name__}
                )

        return wrapper
    return decorator
