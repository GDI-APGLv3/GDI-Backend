import json
import sys
from datetime import datetime, timezone


def _emit(entry: dict):
    entry["audit"] = True
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(entry, default=str), file=sys.stderr, flush=True)


def log_mcp_tool_call(
    *,
    cid: str,
    user_id: str | None,
    schema: str | None,
    tool: str,
    status: str,
    duration_ms: int,
    error: str | None = None,
):
    entry = {
        "type": "mcp_tool",
        "cid": cid,
        "user": user_id[:8] if user_id else None,
        "schema": schema,
        "tool": tool,
        "status": status,
        "ms": duration_ms,
    }
    if error:
        entry["error"] = error[:200]
    _emit(entry)


def log_rest_request(
    *,
    cid: str,
    user_id: str | None,
    schema: str | None,
    method: str,
    path: str,
    http_status: int,
    duration_ms: int,
    error: str | None = None,
):
    entry = {
        "type": "rest",
        "cid": cid,
        "user": user_id[:8] if user_id else None,
        "schema": schema,
        "method": method,
        "path": path,
        "http_status": http_status,
        "ms": duration_ms,
    }
    if error:
        entry["error"] = error[:200]
    _emit(entry)
