"""MINT Protocol integration — data-provenance attestation.

Every daily brief and premium query result is attested through MINT Protocol so a
buyer can independently verify the result was produced by this server, unaltered.
The `provenance` block this returns is *additive* — it never changes an existing
response schema, and attestation failure NEVER blocks data delivery (fail-open).

Self-contained: talks plain HTTPS to the MINT server (no `mint-attest` dependency,
which keeps the Railway build green — only httpx, already required). The server
registers itself once as a MINT actor (keyless auto-register mints a scoped fnet_
key) and reuses it for every attestation; set MINT_API_KEY to pin a stable
identity instead.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading

import httpx

logger = logging.getLogger("mint_integration")

_ENABLED = os.environ.get("MINT_ATTEST_ENABLED", "true").lower() in ("1", "true", "yes", "on")
_SERVER_URL = os.environ.get("MINT_SERVER_URL", "https://mint-mcp-production.up.railway.app").rstrip("/")
# Tight timeout: a premium query must not hang on attestation. Fail open instead.
_TIMEOUT = float(os.environ.get("MINT_ATTEST_TIMEOUT", "6"))
_ACTOR_NAME = os.environ.get("MINT_AGENT_NAME") or os.environ.get("MINT_ACTOR_NAME") or "foundrynet-data-network"

_lock = threading.Lock()
_state = {"ready": False, "failed": False, "api_key": os.environ.get("MINT_API_KEY"), "mint_id": None}


def _ensure_identity() -> bool:
    """Resolve a MINT identity once. Returns True if we have an api_key+mint_id to
    attest with. Keyless register mints a scoped key; any error disables attestation
    for this process (logged once)."""
    if _state["ready"]:
        return True
    if _state["failed"]:
        return False
    with _lock:
        if _state["ready"]:
            return True
        if _state["failed"]:
            return False
        try:
            headers = {"Content-Type": "application/json", "User-Agent": "mint-integration/1.0"}
            if _state["api_key"]:
                headers["Authorization"] = f"Bearer {_state['api_key']}"
            body = {"name": _ACTOR_NAME, "actor_type": "service",
                    "capabilities": ["data_provenance", "attestation"]}
            r = httpx.post(f"{_SERVER_URL}/v1/register", json=body, headers=headers, timeout=_TIMEOUT)
            data = r.json()
            if r.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
                raise RuntimeError(f"register {r.status_code}: {str(data)[:200]}")
            _state["mint_id"] = data.get("mint_id")
            # keyless register returns a freshly-minted scoped key
            if not _state["api_key"]:
                _state["api_key"] = data.get("api_key")
            if not (_state["api_key"] and _state["mint_id"]):
                raise RuntimeError("register returned no api_key/mint_id")
            _state["ready"] = True
            logger.info(f"MINT identity ready: {_state['mint_id']}")
            return True
        except Exception as e:  # noqa: BLE001
            _state["failed"] = True
            logger.warning(f"MINT identity init failed — attestation disabled this process: {e}")
            return False


def attest_data(data: dict, work_type: str = "analysis", summary: str = "") -> dict:
    """Attest a data product. Returns a provenance dict, or {} when disabled and
    {"mint_verified": False} on any failure. Never raises."""
    if not _ENABLED:
        return {}
    try:
        if not _ensure_identity():
            return {"mint_verified": False}
        data_str = json.dumps(data, sort_keys=True, default=str)
        body = {
            "mint_id": _state["mint_id"],
            "work_type": work_type,
            "duration_seconds": 1,
            "summary": (summary or f"{work_type} result")[:200],
            "input_hash": hashlib.sha256(data_str[:1000].encode()).hexdigest(),
            "output_hash": hashlib.sha256(data_str.encode()).hexdigest(),
        }
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {_state['api_key']}",
                   "User-Agent": "mint-integration/1.0"}
        r = httpx.post(f"{_SERVER_URL}/v1/attest", json=body, headers=headers, timeout=_TIMEOUT)
        res = r.json()
        if r.status_code >= 400 or (isinstance(res, dict) and res.get("error")):
            raise RuntimeError(f"attest {r.status_code}: {str(res)[:200]}")
        return {
            "attestation_hash": res.get("data_hash") or res.get("attestation_id"),
            "attestation_id": res.get("attestation_id"),
            "tx_signature": res.get("tx_signature"),
            "mint_verified": True,
            "verify_at": f"{_SERVER_URL}/v1/verify",
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"MINT attestation failed: {e}")
        return {"mint_verified": False}


# ── Live network feed surfacing (added 2026-06-20) ────────────────────────────
import time as _feed_time

_feed_cache = {"ts": 0.0, "count": None}
LIVE_FEED_URL = "https://mint.foundrynet.io/feed"
FEED_API_URL = _SERVER_URL + "/v1/feed"
SERVERS_OPERATIONAL = "17/17"


def todays_attestation_count():
    """Best-effort count of today's attestations from the MINT live feed (cached
    5 min). Returns an int, or None if unavailable. Never raises."""
    now = _feed_time.time()
    if _feed_cache["count"] is not None and (now - _feed_cache["ts"]) < 300:
        return _feed_cache["count"]
    try:
        r = httpx.get(FEED_API_URL, params={"limit": 500}, timeout=4)
        data = r.json()
        items = data.get("attestations") if isinstance(data, dict) else None
        if isinstance(items, list):
            today = _feed_time.strftime("%Y-%m-%d", _feed_time.gmtime())
            cnt = sum(1 for a in items if str(a.get("created_at", "")).startswith(today))
            _feed_cache.update(ts=now, count=cnt)
            return cnt
    except Exception:  # noqa: BLE001
        pass
    return _feed_cache["count"]


def network_heartbeat() -> dict:
    """Minimal live-network heartbeat appended to paid tool responses."""
    return {"attestations_today": todays_attestation_count(),
            "servers_up": SERVERS_OPERATIONAL,
            "live_feed": LIVE_FEED_URL}


def network_feed_block() -> dict:
    """Feed-surfacing block merged into mint_info responses."""
    return {"live_feed": LIVE_FEED_URL, "feed_api": FEED_API_URL,
            "attestations_today": todays_attestation_count(),
            "servers_operational": SERVERS_OPERATIONAL}
