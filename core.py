"""Shared logic behind the MCP tools + REST routes: the 8 operations, the TTL
cache, and x402 gating. current_weather, weather_alerts, and mint_info are free;
the rest run payment_gate.precheck(price) first. Live data is served from
Open-Meteo/NWS and cached per (tool, rounded location, args).
"""
from __future__ import annotations

import hashlib
import json
import logging

import config
import payment_gate
import supa
import weather_sources as ws

logger = logging.getLogger("weather.core")


def _key(tool: str, params: dict) -> str:
    blob = json.dumps({"t": tool, "p": params}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def _round(v, n=3):
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return v


def _billing(d: dict) -> dict:
    g = d.get("gate")
    if g == "free":
        cap, cnt = d.get("cap"), d.get("count")
        return {"tier": "free", "used_today": cnt, "daily_free": cap,
                "remaining_today": (cap - cnt) if (cap is not None and cnt is not None) else None}
    if g == "paid":
        return {"tier": "paid", "charged_usdc": d.get("amount_usdc")}
    if g == "api_key":
        return {"tier": "api_key", "note": "billed to your Forge account"}
    return {"tier": "free", "note": "gating inert"}


async def _cached(key, tool, lat, lon, ttl, fetch):
    c = await supa.get_cache(key)
    if c is not None:
        return {**c, "cache": "hit"}
    data = await fetch()
    if isinstance(data, dict) and "error" not in data:
        await supa.set_cache(key, tool, lat, lon, data, ttl)
        return {**data, "cache": "miss"}
    return data


# ── current_weather (FREE) ────────────────────────────────────────────────────
async def do_current(latitude, longitude, city, state, country) -> dict:
    loc = await ws.resolve_location(latitude, longitude, city, state, country)
    if not loc:
        return {"error": "not_found", "detail": "Could not resolve location"}
    lat, lon = _round(loc["latitude"]), _round(loc["longitude"])
    key = _key("current", {"lat": lat, "lon": lon})
    out = await _cached(key, "current", lat, lon, config.TTL_CURRENT, lambda: ws.current(lat, lon))
    if loc.get("name"):
        out["resolved_location"] = {"name": loc.get("name"), "admin1": loc.get("admin1"),
                                    "country": loc.get("country")}
    out["billing"] = {"tier": "free"}
    return out


# ── forecast (PAID) ───────────────────────────────────────────────────────────
async def do_forecast(latitude, longitude, days, *, agent_key, payment_tx=None, api_key=None) -> dict:
    if latitude is None or longitude is None:
        return {"error": "bad_request", "detail": "latitude and longitude are required"}
    lat, lon = _round(latitude), _round(longitude)
    days = min(max(int(days or 7), 1), 16)
    dec = await payment_gate.precheck("forecast", {"lat": lat, "lon": lon, "days": days},
                                      config.PRICE_FORECAST, agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    out = await _cached(_key("forecast", {"lat": lat, "lon": lon, "days": days}),
                        "forecast", lat, lon, config.TTL_FORECAST, lambda: ws.forecast(lat, lon, days))
    out["billing"] = _billing(dec)
    return out


# ── historical_weather (PAID) ─────────────────────────────────────────────────
async def do_historical(latitude, longitude, date_from, date_to, *, agent_key, payment_tx=None, api_key=None) -> dict:
    if latitude is None or longitude is None or not date_from or not date_to:
        return {"error": "bad_request", "detail": "latitude, longitude, date_from, date_to required"}
    lat, lon = _round(latitude), _round(longitude)
    dec = await payment_gate.precheck("historical_weather",
                                      {"lat": lat, "lon": lon, "f": date_from, "t": date_to},
                                      config.PRICE_HISTORICAL, agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    out = await _cached(_key("historical", {"lat": lat, "lon": lon, "f": date_from, "t": date_to}),
                        "historical", lat, lon, config.TTL_HISTORICAL,
                        lambda: ws.historical(lat, lon, date_from, date_to))
    out["billing"] = _billing(dec)
    return out


# ── climate_normals (PAID) ────────────────────────────────────────────────────
async def do_normals(latitude, longitude, month, *, agent_key, payment_tx=None, api_key=None) -> dict:
    if latitude is None or longitude is None:
        return {"error": "bad_request", "detail": "latitude and longitude are required"}
    lat, lon = _round(latitude, 2), _round(longitude, 2)
    dec = await payment_gate.precheck("climate_normals", {"lat": lat, "lon": lon, "m": month},
                                      config.PRICE_NORMALS, agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    out = await _cached(_key("normals", {"lat": lat, "lon": lon, "m": month}),
                        "normals", lat, lon, config.TTL_NORMALS,
                        lambda: ws.normals(lat, lon, month))
    out["billing"] = _billing(dec)
    return out


# ── weather_alerts (FREE) ─────────────────────────────────────────────────────
async def do_alerts(state, latitude, longitude, radius_km) -> dict:
    if latitude is not None and longitude is not None:
        alerts = await ws.nws_alerts(lat=_round(latitude), lon=_round(longitude))
        scope = {"point": [latitude, longitude]}
    elif state:
        snap = await supa.read_alerts(state=state)
        alerts = snap if snap else await ws.nws_alerts(state=state)
        scope = {"state": state.upper()}
    else:
        alerts = await ws.nws_alerts()
        scope = {"scope": "nationwide (US)"}
    summary = [{"event": a.get("event"), "severity": a.get("severity"), "urgency": a.get("urgency"),
                "headline": a.get("headline"), "area": a.get("area_desc"),
                "expires": a.get("expires")} for a in alerts]
    return {"scope": scope, "count": len(summary), "alerts": summary, "billing": {"tier": "free"},
            "note": "NWS active alerts (US). Public safety data is free."}


# ── agricultural_outlook (PAID) ───────────────────────────────────────────────
async def do_agricultural(latitude, longitude, *, agent_key, payment_tx=None, api_key=None) -> dict:
    if latitude is None or longitude is None:
        return {"error": "bad_request", "detail": "latitude and longitude are required"}
    lat, lon = _round(latitude), _round(longitude)
    dec = await payment_gate.precheck("agricultural_outlook", {"lat": lat, "lon": lon},
                                      config.PRICE_AGRICULTURAL, agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    out = await _cached(_key("ag", {"lat": lat, "lon": lon}), "agricultural", lat, lon,
                        config.TTL_AG, lambda: ws.agricultural(lat, lon))
    out["billing"] = _billing(dec)
    return out


# ── travel_conditions (PAID) ──────────────────────────────────────────────────
async def do_travel(origin_lat, origin_lon, dest_lat, dest_lon, date, *,
                    agent_key, payment_tx=None, api_key=None) -> dict:
    if None in (origin_lat, origin_lon, dest_lat, dest_lon):
        return {"error": "bad_request", "detail": "origin_lat/lon and dest_lat/lon required"}
    olat, olon = _round(origin_lat), _round(origin_lon)
    dlat, dlon = _round(dest_lat), _round(dest_lon)
    dec = await payment_gate.precheck("travel_conditions",
                                      {"o": [olat, olon], "d": [dlat, dlon], "date": date},
                                      config.PRICE_TRAVEL, agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]

    async def _fetch():
        of = await ws.forecast(olat, olon, 7)
        df = await ws.forecast(dlat, dlon, 7)
        if "error" in of or "error" in df:
            return {"error": "source_error"}
        od = _pick_day(of, date)
        dd = _pick_day(df, date)
        dest_alerts = await ws.nws_alerts(lat=dlat, lon=dlon)
        return {
            "date": date or (dd or {}).get("date"),
            "origin": {"lat": olat, "lon": olon, "day": od},
            "destination": {"lat": dlat, "lon": dlon, "day": dd},
            "comparison": _compare(od, dd),
            "advisories": [{"event": a.get("event"), "severity": a.get("severity"),
                            "headline": a.get("headline")} for a in dest_alerts[:10]],
            "packing_recommendations": _packing(dd),
        }
    out = await _cached(_key("travel", {"o": [olat, olon], "d": [dlat, dlon], "date": date}),
                        "travel", dlat, dlon, config.TTL_TRAVEL, _fetch)
    out["billing"] = _billing(dec)
    return out


def _pick_day(fc, date):
    daily = fc.get("daily") or []
    if not daily:
        return None
    if date:
        for d in daily:
            if d.get("date") == date:
                return d
    return daily[0]


def _compare(od, dd):
    if not od or not dd:
        return None
    return {"temp_high_delta_f": round((dd.get("high_f") or 0) - (od.get("high_f") or 0), 1),
            "temp_low_delta_f": round((dd.get("low_f") or 0) - (od.get("low_f") or 0), 1),
            "destination_warmer": (dd.get("high_f") or 0) > (od.get("high_f") or 0),
            "destination_wetter": (dd.get("precip_prob_pct") or 0) > (od.get("precip_prob_pct") or 0)}


def _packing(dd):
    if not dd:
        return []
    recs = []
    hi, lo = dd.get("high_f"), dd.get("low_f")
    if lo is not None and lo <= 32:
        recs += ["heavy coat", "gloves", "hat"]
    elif lo is not None and lo <= 50:
        recs += ["jacket", "layers"]
    elif hi is not None and hi >= 85:
        recs += ["light/breathable clothing", "sun protection", "hydration"]
    else:
        recs += ["light jacket or long sleeves"]
    if (dd.get("precip_prob_pct") or 0) >= 40:
        recs += ["umbrella", "rain jacket"]
    if (dd.get("wind_max_mph") or 0) >= 25:
        recs += ["windbreaker"]
    return recs


# ── mint_info (FREE) ──────────────────────────────────────────────────────────
def mint_info() -> dict:
    return {
        "network": "FoundryNet Data Network",
        "message": "Attest your agent's weather/climate analysis with MINT Protocol for verifiable proof.",
        "mint_protocol": {"mcp_endpoint": config.MINT_MCP_URL, "info_url": config.MINT_INFO_URL,
                          "tools": ["mint_register", "mint_attest", "mint_verify",
                                    "mint_rate", "mint_recommend", "mint_discover"]},
        "see_also": config.SISTER_SERVERS,
    }
