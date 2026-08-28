
import os
import time
import html
import asyncio
import threading
import traceback
from typing import Optional

from shared.logging import get_logger

logger = get_logger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"

_state: dict[str, list] = {}
_lock = threading.Lock()

_tasks: set = set()


def _cfg() -> dict:
    return {
        "api_key": os.getenv("RESEND_API_KEY", "").strip(),
        "to": os.getenv("ERROR_ALERT_EMAIL", "").strip(),
        "from": os.getenv("FROM_EMAIL", "GDI Latam <noreply@example.com>").strip(),
        "cooldown": max(int(os.getenv("ERROR_ALERT_COOLDOWN_MIN", "30")), 1) * 60,
        "env": os.getenv("SENTRY_ENV") or os.getenv("FLY_APP_NAME") or "local",
    }


def _enabled(cfg: dict) -> bool:
    return bool(cfg["api_key"] and cfg["to"])


def _should_send(fingerprint: str, cooldown: int) -> Optional[int]:
    now = time.time()
    with _lock:
        entry = _state.get(fingerprint)
        if entry is None or (now - entry[0]) >= cooldown:
            suppressed = entry[1] if entry else 0
            _state[fingerprint] = [now, 0]
            if len(_state) > 500:
                stale = [k for k, v in _state.items() if (now - v[0]) >= cooldown * 4]
                for k in stale:
                    _state.pop(k, None)
            return suppressed + 1
        entry[1] += 1
        return None


def report_error(request, exc: Exception, *, kind: str = "UNHANDLED") -> None:
    cfg = _cfg()
    if not _enabled(cfg):
        return
    try:
        method = getattr(request, "method", "?")
        path = getattr(getattr(request, "url", None), "path", "?")
        if method == "?" and path == "?":
            from shared.context import get_request_endpoint
            endpoint = get_request_endpoint()
            if endpoint:
                method, _, path = endpoint.partition(" ")
        fingerprint = f"{kind}:{type(exc).__name__}:{method}:{path}"
        occurrences = _should_send(fingerprint, cfg["cooldown"])
        if occurrences is None:
            return

        state = getattr(request, "state", None)
        schema = getattr(state, "schema_name", None)
        user_id = getattr(state, "user_id", None)
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-3500:]

        subject = f"[GDI {cfg['env']}] {type(exc).__name__} en {method} {path}"
        body_html = _build_html(cfg, kind, exc, method, path, schema, user_id, occurrences, tb)

        task = asyncio.create_task(_send(cfg, subject, body_html))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception as e:
        logger.warning("[error_alerts] no se pudo preparar la alerta: %s", e)


async def _send(cfg: dict, subject: str, body_html: str) -> None:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": cfg["from"],
                    "to": [cfg["to"]],
                    "subject": subject,
                    "html": body_html,
                },
            )
        if resp.status_code >= 300:
            logger.warning("[error_alerts] Resend respondió %s: %s", resp.status_code, resp.text[:200])
        else:
            logger.info("[error_alerts] alerta enviada (HTTP %s) a %s", resp.status_code, cfg["to"])
    except Exception as e:
        logger.warning("[error_alerts] fallo enviando alerta: %s", e)


def _build_html(cfg, kind, exc, method, path, schema, user_id, occurrences, tb) -> str:
    def esc(v):
        return html.escape(str(v)) if v is not None else "—"

    repeat_note = ""
    if occurrences and occurrences > 1:
        repeat_note = (
            f"<p style='color:#b45309'><b>Ocurrió {occurrences} veces</b> desde el último aviso "
            f"(se agrupan; el próximo aviso de este mismo error es en máx. {cfg['cooldown'] // 60} min).</p>"
        )
    return f"""
    <div style="font-family:system-ui,Segoe UI,Arial,sans-serif;font-size:14px;color:#111;max-width:720px">
      <h2 style="margin:0 0 4px">⚠️ Error en GDI — {esc(cfg['env'])}</h2>
      <p style="color:#555;margin:0 0 12px">Tipo <b>{esc(kind)}</b></p>
      <table cellpadding="6" style="border-collapse:collapse;width:100%">
        <tr><td style="background:#f3f4f6;width:130px"><b>Error</b></td><td><code>{esc(type(exc).__name__)}</code></td></tr>
        <tr><td style="background:#f3f4f6"><b>Mensaje</b></td><td>{esc(exc)}</td></tr>
        <tr><td style="background:#f3f4f6"><b>Endpoint</b></td><td><code>{esc(method)} {esc(path)}</code></td></tr>
        <tr><td style="background:#f3f4f6"><b>Municipio</b></td><td>{esc(schema)}</td></tr>
        <tr><td style="background:#f3f4f6"><b>Usuario</b></td><td>{esc(user_id)}</td></tr>
      </table>
      {repeat_note}
      <p style="margin:14px 0 4px"><b>Traceback</b></p>
      <pre style="background:#0b1020;color:#d1d5db;padding:12px;border-radius:6px;overflow:auto;font-size:12px;white-space:pre-wrap">{esc(tb)}</pre>
      <p style="color:#888;font-size:12px">GDI Latam · alerta automática de errores (DIY). No responder a este mail.</p>
    </div>
    """
