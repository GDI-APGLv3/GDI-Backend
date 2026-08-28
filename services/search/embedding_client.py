import asyncio
import time
import httpx
import os
from shared.logging import get_logger

logger = get_logger(__name__)

_WAKEUP_ERROR = httpx.TransportError
_EMBED_MAX_ATTEMPTS = 4
_EMBED_RETRY_BASE = 0.3
_EMBED_BUDGET_SECONDS = 12.0
_EMBED_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=2.0, pool=2.0)


async def get_embedding(text: str, *, schema_name: str, rewrite: bool = True) -> dict:
    agent_url = os.getenv('AGENT_URL')
    api_key = os.getenv('INTERNAL_API_KEY')
    if not agent_url or not api_key:
        raise RuntimeError("AGENT_URL o INTERNAL_API_KEY no configurado")

    deadline = time.monotonic() + _EMBED_BUDGET_SECONDS

    async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
        for attempt in range(_EMBED_MAX_ATTEMPTS):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "get_embedding: budget wall-clock agotado (%.1fs) antes del intento %d — sin respuesta",
                    _EMBED_BUDGET_SECONDS, attempt + 1,
                )
                raise httpx.ConnectTimeout(
                    f"get_embedding: budget {_EMBED_BUDGET_SECONDS}s agotado"
                )
            try:
                response = await asyncio.wait_for(
                    client.post(
                        f"{agent_url}/api/v1/embeddings/generate",
                        json={"text": text, "schema_name": schema_name, "rewrite": rewrite},
                        headers={"X-API-Key": api_key},
                    ),
                    timeout=remaining,
                )
                response.raise_for_status()
                return response.json()
            except asyncio.TimeoutError as exc:
                logger.error(
                    "get_embedding: budget %.1fs agotado durante intento %d/%d (wait_for)",
                    _EMBED_BUDGET_SECONDS, attempt + 1, _EMBED_MAX_ATTEMPTS,
                )
                raise httpx.ConnectTimeout(
                    f"get_embedding: budget {_EMBED_BUDGET_SECONDS}s agotado"
                ) from exc
            except _WAKEUP_ERROR as exc:
                if attempt >= _EMBED_MAX_ATTEMPTS - 1:
                    logger.error(
                        "get_embedding: AgenteLANG sigue no disponible tras %d intentos — %s",
                        _EMBED_MAX_ATTEMPTS, exc,
                    )
                    raise
                delay = _EMBED_RETRY_BASE * (2 ** attempt)
                if time.monotonic() + delay >= deadline:
                    logger.error(
                        "get_embedding: budget wall-clock agotado (%.1fs) tras "
                        "intento %d/%d — %s",
                        _EMBED_BUDGET_SECONDS, attempt + 1, _EMBED_MAX_ATTEMPTS, exc,
                    )
                    raise
                logger.warning(
                    "get_embedding: AgenteLANG no disponible (intento %d/%d), "
                    "esperando %.1fs para resume — %s",
                    attempt + 1, _EMBED_MAX_ATTEMPTS, delay, exc,
                )
                await asyncio.sleep(delay)
