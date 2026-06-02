import asyncio
import time
import httpx
import os
from shared.logging import get_logger

logger = get_logger(__name__)

# MejoraArranque FIX C: cuando AgenteLANG esta suspendido (auto_stop=suspend en Fly.io),
# el primer request falla con httpx.TransportError porque la maquina todavia no
# desperto y/o el DNS interno no resolvio aun. Resume time medido: 271-778ms
# (sub-segundo). En vez de propagar el error inmediatamente, reintentamos con
# backoff exponencial acotado por un wall-clock budget total.
#
# httpx.TransportError es la clase padre que cubre ConnectError, ConnectTimeout,
# ReadTimeout, WriteTimeout, PoolTimeout, RemoteProtocolError, ReadError, etc.
# Excluye intencionalmente httpx.HTTPStatusError, que se mantiene como bug real
# de AgenteLANG y NO se reintenta (sube limpio al router que lo mapea a 500).
_WAKEUP_ERROR = httpx.TransportError
# Total de intentos (no de "reintentos"): el primero + hasta 3 reintentos = 4.
_EMBED_MAX_ATTEMPTS = 4
# Backoff exponencial entre intentos: 0.3 → 0.6 → 1.2 s (3 sleeps entre 4
# intentos, suma ~2.1s). Tras el ultimo intento no hay sleep.
_EMBED_RETRY_BASE = 0.3
# Wall-clock budget total. Resume Fly.io es sub-segundo; 5s es agresivo pero
# suficiente para absorber DNS + TCP + TLS + embedding gen sano. Si se agota,
# propagamos TransportError y el router devuelve 503; el front (useSmartSearch)
# tiene su propio retry (~23s en retryWithBackoff.ts) para el cold-start largo.
_EMBED_BUDGET_SECONDS = 5.0
# Timeout por intento. Connect rapido (red interna Fly, *.internal sin TLS);
# read alineado al budget para que el invariante "ningun intento excede el
# budget global" sea explicito en la config (no solo dependa de wait_for).
# wait_for sigue siendo el corte estricto a nivel wall-clock, pero estos
# valores no invitan a confusion si alguien sube el budget en el futuro.
_EMBED_TIMEOUT = httpx.Timeout(connect=2.0, read=4.0, write=2.0, pool=2.0)


async def get_embedding(text: str, *, schema_name: str, rewrite: bool = True) -> dict:
    """Llama a AgenteLANG para generar embedding.

    Returns: {"embedding": [float, ...], "model": str, "total_tokens": int, "rewritten_text": str|None}

    Raises:
        httpx.HTTPStatusError: AgenteLANG respondio 4xx/5xx (bug real, NO se
            reintenta). El router lo mapea a 500 (fuera de GATEWAY_ERROR_STATUSES
            del front para evitar loop de retry).
        httpx.TransportError: AgenteLANG sigue inalcanzable tras agotar
            _EMBED_MAX_ATTEMPTS intentos o tras agotar el budget wall-clock.
            El router lo mapea a 503; el front lo trata como cold-start y
            reintenta con su propio backoff.
        RuntimeError: AGENT_URL o INTERNAL_API_KEY no configurado.

    MejoraArranque FIX C: ante TransportError reintenta con backoff exponencial
    (max _EMBED_MAX_ATTEMPTS intentos, max _EMBED_BUDGET_SECONDS de wall-clock).
    HTTPStatusError sube sin retry — es bug del server, no cold-start.
    """
    agent_url = os.getenv('AGENT_URL')
    api_key = os.getenv('INTERNAL_API_KEY')
    if not agent_url or not api_key:
        raise RuntimeError("AGENT_URL o INTERNAL_API_KEY no configurado")

    deadline = time.monotonic() + _EMBED_BUDGET_SECONDS

    # Cliente reutilizado entre intentos: si el primer intento llego a establecer
    # TCP/keep-alive, el siguiente lo aprovecha. Reduce overhead vs crear/destruir
    # el AsyncClient por intento.
    async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
        for attempt in range(_EMBED_MAX_ATTEMPTS):
            # Tiempo restante del budget. Envolvemos cada intento en wait_for para
            # que NUNCA se exceda el budget total, incluido el ultimo intento
            # (el check de "sleep + delay < deadline" mas abajo solo recorta las
            # esperas entre intentos, no la duracion del intento mismo).
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "get_embedding: budget wall-clock agotado (%.1fs) antes del intento %d — sin respuesta",
                    _EMBED_BUDGET_SECONDS, attempt + 1,
                )
                # Levantamos un TransportError generico para que el router lo
                # mapee a 503 (consistente con la rama de retry agotado).
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
                # wait_for agoto el remaining del budget. Es un corte definitivo:
                # no quedan segundos en el budget global, sea cual sea el intento.
                # No se reintenta. Convertimos a TransportError para que el
                # router lo mapee a 503 (consistente con la rama de retry agotado).
                logger.error(
                    "get_embedding: budget %.1fs agotado durante intento %d/%d (wait_for)",
                    _EMBED_BUDGET_SECONDS, attempt + 1, _EMBED_MAX_ATTEMPTS,
                )
                raise httpx.ConnectTimeout(
                    f"get_embedding: budget {_EMBED_BUDGET_SECONDS}s agotado"
                ) from exc
            except _WAKEUP_ERROR as exc:
                # Ultimo intento: propagar (el router lo mapea a 503).
                if attempt >= _EMBED_MAX_ATTEMPTS - 1:
                    logger.error(
                        "get_embedding: AgenteLANG sigue no disponible tras %d intentos — %s",
                        _EMBED_MAX_ATTEMPTS, exc,
                    )
                    raise
                delay = _EMBED_RETRY_BASE * (2 ** attempt)
                # Budget check ANTES del sleep: si el sleep nos llevaria mas alla
                # del deadline, propagar ahora sin esperar.
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
