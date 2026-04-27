import httpx
import os
from shared.logging import get_logger

logger = get_logger(__name__)


def get_embedding(text: str, *, schema_name: str, rewrite: bool = True) -> dict:
    """Llama a AgenteLANG para generar embedding. Sync.

    Returns: {"embedding": [float, ...], "model": str, "total_tokens": int, "rewritten_text": str|None}
    Raises: httpx.HTTPError si AgenteLANG no responde
    """
    agent_url = os.getenv('AGENT_URL')
    api_key = os.getenv('INTERNAL_API_KEY')
    if not agent_url or not api_key:
        raise RuntimeError("AGENT_URL o INTERNAL_API_KEY no configurado")

    # Timeout generoso (60s) para absorber cold start de AgenteLANG en Fly.io
    # cuando auto_stop_machines esta habilitado (DEMO/PRD). Sin este margen,
    # el primer request tras periodo de inactividad revienta con 503.
    response = httpx.post(
        f"{agent_url}/api/v1/embeddings/generate",
        json={"text": text, "schema_name": schema_name, "rewrite": rewrite},
        headers={"X-API-Key": api_key},
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()
