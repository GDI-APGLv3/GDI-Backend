
import asyncio
from typing import Iterable, Optional

import httpx

from shared.logging import get_logger

logger = get_logger(__name__)

COLDSTART_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
    httpx.WriteError,
    httpx.WriteTimeout,
)

COLDSTART_STATUS_CODES = (502, 503)

MICRO_COLDSTART_MAX_ATTEMPTS = 4
MICRO_COLDSTART_BACKOFF = (2.0, 4.0, 6.0)


async def post_micro_with_coldstart_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_attempts: int = MICRO_COLDSTART_MAX_ATTEMPTS,
    backoff: Iterable[float] = MICRO_COLDSTART_BACKOFF,
    log_label: str = "micro",
    **kwargs,
) -> httpx.Response:
    backoff_list = list(backoff)
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.post(url, **kwargs)
        except COLDSTART_EXCEPTIONS as e:
            last_exc = e
            if attempt >= max_attempts:
                logger.error(
                    f"[{log_label}] cold-start: agotados {max_attempts} intentos, "
                    f"último error de red: {type(e).__name__}: {e}"
                )
                raise
            sleep_s = backoff_list[min(attempt - 1, len(backoff_list) - 1)]
            logger.warning(
                f"[{log_label}] cold-start (posible micro dormido), reintentando "
                f"intento {attempt + 1}/{max_attempts} en {sleep_s}s — "
                f"error: {type(e).__name__}: {e}"
            )
            await asyncio.sleep(sleep_s)
            continue

        if response.status_code in COLDSTART_STATUS_CODES:
            if attempt >= max_attempts:
                logger.error(
                    f"[{log_label}] cold-start: agotados {max_attempts} intentos, "
                    f"último status HTTP {response.status_code}"
                )
                return response
            sleep_s = backoff_list[min(attempt - 1, len(backoff_list) - 1)]
            logger.warning(
                f"[{log_label}] cold-start (HTTP {response.status_code}, proxy "
                f"despertando la máquina), reintentando intento {attempt + 1}/"
                f"{max_attempts} en {sleep_s}s"
            )
            await asyncio.sleep(sleep_s)
            continue

        return response

    if last_exc:
        raise last_exc
    raise RuntimeError(f"[{log_label}] post_micro_with_coldstart_retry: estado inesperado")
