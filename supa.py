"""Supabase PostgREST client for weather-intel-mcp (standalone project).

A TTL cache for live tool responses, the hourly alerts snapshot, the free-tier
counter, and the x402 ledger. Defensive: failures degrade so tools still serve
live data even if the cache is down.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import config
from http_util import request_json

logger = logging.getLogger("weather.supa")


def configured() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY)


def _headers(extra: Optional[dict] = None) -> dict:
    h = {"apikey": config.SUPABASE_SERVICE_KEY,
         "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
         "Content-Type": "application/json", "Accept": "application/json"}
    # Shared-hub consolidation: when SUPABASE_SCHEMA != public, target this
    # service's namespaced schema via PostgREST profile headers (Accept-Profile
    # for reads, Content-Profile for writes/RPC). No table-name changes needed.
    sch = getattr(config, "SUPABASE_SCHEMA", "public")
    if sch and sch != "public":
        h["Accept-Profile"] = sch
        h["Content-Profile"] = sch
    if extra:
        h.update(extra)
    return h


def _url(path: str) -> str:
    return f"{config.SUPABASE_URL}/rest/v1/{path}"


async def select(table: str, params: dict) -> list:
    if not configured():
        return []
    r = await request_json("GET", _url(table), headers=_headers(), params=params,
                           timeout=config.REQUEST_TIMEOUT)
    return r if isinstance(r, list) else []


async def upsert(table: str, rows: list, on_conflict: str) -> dict:
    if not configured() or not rows:
        return {"data": []}
    r = await request_json("POST", _url(table),
                           headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
                           params={"on_conflict": on_conflict},
                           body=rows, timeout=max(config.REQUEST_TIMEOUT, 60))
    if isinstance(r, dict) and r.get("error"):
        return r
    return {"data": rows}


async def rpc(fn: str, body: dict):
    if not configured():
        return None
    return await request_json("POST", _url(f"rpc/{fn}"), headers=_headers(),
                              body=body, timeout=config.REQUEST_TIMEOUT)


# ── TTL cache ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


async def get_cache(cache_key: str) -> Optional[dict]:
    rows = await select("weather_cache", {"select": "payload,expires_at",
                                          "cache_key": f"eq.{cache_key}", "limit": "1"})
    if not rows:
        return None
    exp = rows[0].get("expires_at")
    if exp and exp < _now_iso():
        return None
    return rows[0].get("payload")


async def set_cache(cache_key: str, tool: str, lat, lon, payload: dict, ttl: int) -> None:
    if not configured():
        return
    expires = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + ttl))
    row = {"cache_key": cache_key, "tool": tool, "lat": lat, "lon": lon,
           "payload": payload, "expires_at": expires, "created_at": _now_iso()}
    await request_json("POST", _url("weather_cache"),
                       headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
                       params={"on_conflict": "cache_key"}, body=[row],
                       timeout=config.REQUEST_TIMEOUT)


# ── alerts snapshot ───────────────────────────────────────────────────────────
async def replace_alerts(rows: list) -> None:
    """Upsert the current alert snapshot, then prune expired rows."""
    if not configured() or not rows:
        return
    # dedup on alert_id
    seen, deduped = set(), []
    for r in rows:
        if r["alert_id"] in seen:
            continue
        seen.add(r["alert_id"])
        deduped.append(r)
    for i in range(0, len(deduped), 500):
        await request_json("POST", _url("weather_alerts"),
                           headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
                           params={"on_conflict": "alert_id"}, body=deduped[i:i + 500],
                           timeout=max(config.REQUEST_TIMEOUT, 60))
    # prune expired
    await request_json("DELETE", _url("weather_alerts"),
                       headers=_headers({"Prefer": "return=minimal"}),
                       params={"expires": f"lt.{_now_iso()}"}, timeout=config.REQUEST_TIMEOUT)


async def read_alerts(state=None, severity=None, limit=200) -> list:
    p = {"select": "*", "order": "severity.asc", "limit": str(limit)}
    if state:
        p["states"] = f"ilike.*{state.upper()}*"
    if severity:
        p["severity"] = f"eq.{severity}"
    return await select("weather_alerts", p)


# ── free-tier + payments ──────────────────────────────────────────────────────
async def claim_free_query(agent_key: str, day: str, cap: int) -> Optional[dict]:
    r = await rpc("weather_claim_free_query", {"p_agent_key": agent_key, "p_day": day, "p_cap": cap})
    if isinstance(r, dict) and "allowed" in r:
        return r
    if isinstance(r, list) and r and isinstance(r[0], dict):
        return r[0]
    return None


async def payment_tx_used(tx_signature: str) -> bool:
    rows = await select("weather_payments", {"tx_signature": f"eq.{tx_signature}",
                                             "select": "tx_signature", "limit": "1"})
    return bool(rows)


async def insert_payment(row: dict) -> dict:
    if not configured():
        return {"error": "not_configured"}
    r = await request_json("POST", _url("weather_payments"),
                           headers=_headers({"Prefer": "return=minimal"}),
                           body=row, timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, dict) and r.get("error"):
        return r
    return {"data": [row]}
