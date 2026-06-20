"""Daily curated brief — weather-intel.

Runs once a day at BRIEF_HOUR_UTC (05:00 UTC) as an in-process background task
(same shape as the alerts/normals loops). Weather is mostly on-demand (live
Open-Meteo + NWS behind a TTL cache, no daily aggregator), so the curator
assembles the brief LIVE: it pulls the active severe NWS alerts, derives the
significant weather events of the last 24h, builds a 72h outlook for a fixed list
of major US metros, and summarizes agricultural-relevant signals. It attests the
package through MINT for verifiable provenance and upserts it into the
`daily_briefs` table. The paid `daily_brief` tool just reads that row back.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import config
import mint_integration
import supa
import weather_sources as ws

logger = logging.getLogger("weather.curator")

SERVER = config.SERVER_SLUG
PRICE = config.PRICE_DAILY_BRIEF

# Major US metros (name, lat, lon) for the 72h outlook + agricultural roll-up.
_METROS = [
    ("New York, NY", 40.71, -74.01), ("Los Angeles, CA", 34.05, -118.24),
    ("Chicago, IL", 41.88, -87.63), ("Houston, TX", 29.76, -95.37),
    ("Phoenix, AZ", 33.45, -112.07), ("Denver, CO", 39.74, -104.98),
    ("Seattle, WA", 47.61, -122.33), ("Miami, FL", 25.76, -80.19),
    ("San Francisco, CA", 37.77, -122.42), ("Dallas, TX", 32.78, -96.80),
    ("Boston, MA", 42.36, -71.06), ("Washington, DC", 38.91, -77.04),
]

# Severities the NWS considers actionable / severe.
_SEVERE = {"extreme", "severe"}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _expires_at(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (d + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")


def related_briefs(exclude: str) -> list:
    return [{"server": s, "price": p, "tool": "daily_brief"}
            for s, p in config.NETWORK_BRIEFS.items() if s != exclude]


async def _curate_signals(since_iso: str) -> tuple[dict, int]:
    """Build the weather brief body live. Returns (signals, count)."""
    # 1) Active severe NWS alerts (prefer the hourly snapshot table; fall back to
    #    a live nationwide pull). Severe/extreme severity only.
    alerts = await supa.read_alerts(limit=500)
    if not alerts:
        alerts = await ws.nws_alerts()  # nationwide live
    severe = [a for a in alerts if (a.get("severity") or "").lower() in _SEVERE]
    sev_order = {"extreme": 2, "severe": 1}
    severe.sort(key=lambda a: sev_order.get((a.get("severity") or "").lower(), 0), reverse=True)
    active_severe_alerts = [{"event": a.get("event"), "severity": a.get("severity"),
                             "urgency": a.get("urgency"), "headline": a.get("headline"),
                             "area": a.get("area_desc"), "states": a.get("states"),
                             "expires": a.get("expires")} for a in severe[:25]]

    # 2) Significant weather events in the last 24h — alerts that onset within the
    #    window OR carry an immediate/expected urgency (the day's notable events).
    def _recent(a) -> bool:
        onset = a.get("onset")
        if onset and onset >= since_iso:
            return True
        return (a.get("urgency") or "").lower() in ("immediate", "expected")
    sig = [a for a in alerts if _recent(a)]
    significant_events = [{"event": a.get("event"), "severity": a.get("severity"),
                           "headline": a.get("headline"), "area": a.get("area_desc"),
                           "onset": a.get("onset")} for a in sig[:25]]

    # 3) 72-hour outlook for major metros (live Open-Meteo forecast, first 3 days).
    metro_outlook_72h = []
    for name, lat, lon in _METROS:
        try:
            fc = await ws.forecast(lat, lon, 3)
            if isinstance(fc, dict) and "error" not in fc:
                metro_outlook_72h.append({
                    "metro": name, "latitude": lat, "longitude": lon,
                    "outlook": [{"date": d.get("date"), "conditions": d.get("conditions"),
                                 "high_f": d.get("high_f"), "low_f": d.get("low_f"),
                                 "precip_prob_pct": d.get("precip_prob_pct"),
                                 "wind_max_mph": d.get("wind_max_mph")}
                                for d in (fc.get("daily") or [])[:3]]})
        except Exception as e:  # noqa: BLE001
            logger.info(f"metro outlook {name} failed: {e}")

    # 4) Agricultural-relevant signals (frost/heat/precip/growing conditions) for
    #    the same metros — the planting/frost roll-up.
    agricultural_signals = []
    for name, lat, lon in _METROS:
        try:
            ag = await ws.agricultural(lat, lon)
            if isinstance(ag, dict) and "error" not in ag:
                agricultural_signals.append({
                    "metro": name, "latitude": lat, "longitude": lon,
                    "growing_degree_days_season_to_date": ag.get("growing_degree_days_season_to_date"),
                    "frost_risk_next_14d_days": ag.get("frost_risk_next_14d_days"),
                    "soil_temp_0cm_f": ag.get("soil_temp_0cm_f"),
                    "precip_outlook_7d_in": ag.get("precip_outlook_7d_in"),
                    "planting_window_assessment": ag.get("planting_window_assessment")})
        except Exception as e:  # noqa: BLE001
            logger.info(f"ag signal {name} failed: {e}")

    signals = {
        "active_severe_alerts": active_severe_alerts,
        "significant_events": significant_events,
        "metro_outlook_72h": metro_outlook_72h,
        "agricultural_signals": agricultural_signals,
    }
    count = (len(active_severe_alerts) + len(significant_events)
             + len(metro_outlook_72h) + len(agricultural_signals))
    return signals, count


async def run_curation(date_str: str | None = None) -> dict:
    """Generate, attest, and store today's brief. Idempotent per date (upsert)."""
    date_str = date_str or _today()
    since_iso = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    signals, count = await _curate_signals(since_iso)

    brief = {
        "brief_date": date_str, "server": SERVER, "signal_count": count,
        "signals": signals, "expires_at": _expires_at(date_str),
        "related_briefs": related_briefs(SERVER),
    }
    # Attest for provenance (sync httpx → run off the event loop; fail-open).
    attestation = await asyncio.to_thread(
        mint_integration.attest_data, brief, "analysis",
        f"Daily {SERVER} brief: {count} signals")
    brief["provenance"] = attestation

    row = {
        "brief_date": date_str, "brief_data": brief, "signal_count": count,
        "attestation_hash": attestation.get("attestation_hash"),
        "expires_at": _expires_at(date_str),
    }
    res = await supa.upsert("daily_briefs", [row], "brief_date")
    if isinstance(res, dict) and res.get("error"):
        logger.warning(f"daily brief upsert failed: {str(res)[:200]}")
    else:
        logger.info(f"daily brief stored: {date_str} ({count} signals, "
                    f"attested={attestation.get('mint_verified')})")
    return brief


async def get_brief(date_str: str | None = None) -> dict | None:
    """Read a stored brief; None if missing or expired."""
    date_str = date_str or _today()
    rows = await supa.select("daily_briefs",
                             {"select": "*", "brief_date": f"eq.{date_str}", "limit": "1"})
    if not rows:
        return None
    row = rows[0]
    exp = row.get("expires_at")
    if exp:
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(exp.replace("Z", "+00:00")):
                return None
        except Exception:  # noqa: BLE001
            pass
    return row.get("brief_data")


async def bump_purchase(date_str: str) -> None:
    """Best-effort purchase counter via RPC (no-op if the function is absent)."""
    try:
        await supa.rpc("increment_brief_purchase", {"p_brief_date": date_str})
    except Exception:  # noqa: BLE001
        pass


async def curator_loop() -> None:
    """Sleep until BRIEF_HOUR_UTC each day, then curate. Cancellable."""
    while True:
        now = datetime.now(timezone.utc)
        secs = now.hour * 3600 + now.minute * 60 + now.second
        wait = (config.BRIEF_HOUR_UTC * 3600 - secs) % 86400 or 86400
        try:
            await asyncio.sleep(wait)
            if supa.configured():
                await run_curation()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"curator loop error: {e}")
            await asyncio.sleep(3600)
