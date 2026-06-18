"""Resolve the caller identity used for the free-tier counter, and read the
Bearer key — both best-effort from the live HTTP request when available.

Free tier is keyed on an explicit `agent_id` the caller passes; absent that, on a
hash of the client IP (so anonymous MCP callers still get a per-agent allowance
rather than sharing one global counter). An `Authorization: Bearer fnet_…` key
bypasses the free tier entirely (trusted/unlimited).
"""
from __future__ import annotations

import hashlib
from typing import Optional

try:
    from fastmcp.server.dependencies import get_http_request
except Exception:  # noqa: BLE001 — older/newer fastmcp, or called outside a request
    get_http_request = None


def _request():
    if not get_http_request:
        return None
    try:
        return get_http_request()
    except Exception:  # noqa: BLE001
        return None


def client_ip(request=None) -> Optional[str]:
    req = request or _request()
    if not req:
        return None
    try:
        xff = req.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        return req.client.host if req.client else None
    except Exception:  # noqa: BLE001
        return None


def resolve_agent_key(agent_id: Optional[str] = None, *, request=None) -> str:
    if agent_id and str(agent_id).strip():
        return f"aid:{str(agent_id).strip()[:120]}"
    ip = client_ip(request)
    if ip:
        return "ip:" + hashlib.sha256(ip.encode("utf-8")).hexdigest()[:32]
    return "anon"


def bearer(request=None) -> Optional[str]:
    req = request or _request()
    if not req:
        return None
    try:
        a = req.headers.get("authorization", "")
        return a[7:].strip() if a.lower().startswith("bearer ") else None
    except Exception:  # noqa: BLE001
        return None
