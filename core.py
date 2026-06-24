"""Shared logic behind the MCP tools + REST routes: the 8 operations, the TTL
cache, and x402 gating. current_weather, weather_alerts, and mint_info are free;
the rest run payment_gate.precheck(price) first. Live data is served from
Open-Meteo/NWS and cached per (tool, rounded location, args).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

import config
import daily_curator
import mint_integration
import payment_gate
import stripe_gate
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
    # Provenance attestation (additive; fail-open; off the event loop).
    out["provenance"] = await asyncio.to_thread(
        mint_integration.attest_data, out, "analysis", "forecast query result")
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


# ── daily_brief (premium, curated) ────────────────────────────────────────────
async def do_daily_brief(date, *, agent_key, payment_tx=None, api_key=None,
                         stripe_token=None) -> dict:
    day = (date or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()

    # Stripe rail (parallel to x402): a paid Checkout Session unlocks the brief.
    stripe_err = None
    if stripe_token and stripe_gate.is_active():
        sv = await stripe_gate.verify_session(stripe_token, config.PRICE_DAILY_BRIEF,
                                              tool="daily_brief", agent_key=agent_key)
        if sv["ok"]:
            brief = await daily_curator.get_brief(day)
            if not brief:
                return {"error": "not_available",
                        "detail": f"No brief for {day} (not yet generated, or expired at "
                                  f"midnight UTC). Curated daily at {config.BRIEF_HOUR_UTC:02d}:00 UTC.",
                        "billing": "stripe"}
            await daily_curator.bump_purchase(day)
            return {**brief, "billing": "stripe", "stripe_session": sv["session"]}
        stripe_err = sv.get("detail")  # surface on the 402 below

    dec = await payment_gate.precheck("daily_brief", {"date": day}, config.PRICE_DAILY_BRIEF,
                                      agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return stripe_gate.augment_402(dec["body"], config.PRICE_DAILY_BRIEF,
                                       stripe_error=stripe_err)
    brief = await daily_curator.get_brief(day)
    if not brief:
        return {"error": "not_available",
                "detail": f"No brief for {day} (not yet generated, or expired at midnight UTC). "
                          f"Briefs are curated daily at {config.BRIEF_HOUR_UTC:02d}:00 UTC.",
                "billing": _billing(dec)}
    await daily_curator.bump_purchase(day)
    return {**brief, "billing": _billing(dec)}


# ── supply_chain_risk (PAID $0.02) ────────────────────────────────────────────
def _parse_latlon(s):
    """Accept a 'lat,lon' string; return (lat, lon) or None."""
    if not isinstance(s, str) or "," not in s:
        return None
    try:
        a, b = s.split(",", 1)
        lat, lon = float(a.strip()), float(b.strip())
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (TypeError, ValueError):
        pass
    return None


async def _resolve_point(place):
    """Resolve a free-form place string (city, "City, ST", or 'lat,lon') to a
    coordinate dict. Open-Meteo geocoding wants the city name alone, so a trailing
    ", ST"/", Country" is split off and used to disambiguate."""
    if not isinstance(place, str):
        return None
    ll = _parse_latlon(place)
    if ll:
        return {"latitude": ll[0], "longitude": ll[1], "name": None}
    city, state, country = place.strip(), None, None
    if "," in place:
        parts = [p.strip() for p in place.split(",") if p.strip()]
        if parts:
            city = parts[0]
            if len(parts) >= 2:
                state = parts[1]
            if len(parts) >= 3:
                country = parts[2]
    loc = await ws.resolve_location(None, None, city, state, country)
    if not loc and (state or country):  # retry on the bare city
        loc = await ws.resolve_location(None, None, city, None, None)
    return loc


async def do_supply_chain_risk(origin, destination, ship_date=None, *,
                               agent_key, payment_tx=None, api_key=None) -> dict:
    if not origin or not destination:
        return {"error": "bad_request", "detail": "origin and destination are required"}
    dec = await payment_gate.precheck("supply_chain_risk",
                                      {"o": origin, "d": destination, "s": ship_date},
                                      config.PRICE_SUPPLY_CHAIN, agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]

    o_loc = await _resolve_point(origin)
    d_loc = await _resolve_point(destination)
    if not o_loc:
        return {"error": "not_found", "detail": f"Could not resolve origin: {origin}",
                "billing": _billing(dec)}
    if not d_loc:
        return {"error": "not_found", "detail": f"Could not resolve destination: {destination}",
                "billing": _billing(dec)}
    o_lat, o_lon = _round(o_loc["latitude"]), _round(o_loc["longitude"])
    d_lat, d_lon = _round(d_loc["latitude"]), _round(d_loc["longitude"])

    o_wx = await _cached(_key("current", {"lat": o_lat, "lon": o_lon}), "current", o_lat, o_lon,
                         config.TTL_CURRENT, lambda: ws.current(o_lat, o_lon))
    d_wx = await _cached(_key("current", {"lat": d_lat, "lon": d_lon}), "current", d_lat, d_lon,
                         config.TTL_CURRENT, lambda: ws.current(d_lat, d_lon))
    o_alerts = await ws.nws_alerts(lat=o_lat, lon=o_lon)
    d_alerts = await ws.nws_alerts(lat=d_lat, lon=d_lon)

    risk_score = 0
    threats: list = []

    # Score active NWS alerts at either endpoint.
    for leg, alerts in (("origin", o_alerts or []), ("destination", d_alerts or [])):
        for a in alerts:
            sev = str(a.get("severity") or "").lower()
            event = str(a.get("event") or "")
            etxt = event.lower()
            if "extreme" in sev or "severe" in sev or "warning" in etxt:
                risk_score += 30
                threats.append({"leg": leg, "location": a.get("area_desc"),
                                "threat": event or "severe alert", "severity": a.get("severity")})
            elif "moderate" in sev or "watch" in etxt or "advisory" in etxt:
                risk_score += 15
                threats.append({"leg": leg, "location": a.get("area_desc"),
                                "threat": event or "weather advisory", "severity": a.get("severity")})

    # Score current conditions at either endpoint.
    for leg, wx, loc in (("origin", o_wx, o_loc), ("destination", d_wx, d_loc)):
        if not isinstance(wx, dict) or "error" in wx:
            continue
        temp = wx.get("temp_f")
        wind = wx.get("wind_gust_mph") or wx.get("wind_mph") or 0
        if temp is not None and (temp > 100 or temp < 10):
            risk_score += 15
            threats.append({"leg": leg, "location": loc.get("name"),
                            "threat": f"Extreme temperature: {temp}°F"})
        if wind and wind > 40:
            risk_score += 20
            threats.append({"leg": leg, "location": loc.get("name"),
                            "threat": f"High wind: {wind} mph"})

    risk_score = min(risk_score, 100)
    level = ("critical" if risk_score > 70 else "elevated" if risk_score > 40
             else "moderate" if risk_score > 20 else "low")
    recommendation = (
        "Delay shipment or use an alternative route" if risk_score > 70
        else "Monitor conditions closely; prepare a contingency" if risk_score > 40
        else "Normal operations with standard precautions" if risk_score > 20
        else "Clear conditions for transport")

    def _endpoint(place, loc, wx):
        return {"input": place, "resolved": loc.get("name"),
                "coordinates": {"lat": loc.get("latitude"), "lon": loc.get("longitude")},
                "conditions": (wx if isinstance(wx, dict) and "error" not in wx else None)}

    out = {
        "risk_score": risk_score,
        "risk_level": level,
        "ship_date": ship_date,
        "origin": _endpoint(origin, o_loc, o_wx),
        "destination": _endpoint(destination, d_loc, d_wx),
        "active_threats": threats,
        "threat_count": len(threats),
        "recommendation": recommendation,
        "billing": _billing(dec),
    }
    out["provenance"] = await asyncio.to_thread(
        mint_integration.attest_data, out, "analysis", "supply chain weather risk score")
    return out


# ── mint_info (FREE) ──────────────────────────────────────────────────────────
def mint_info() -> dict:
    return {
        "network": "FoundryNet Data Network", **mint_integration.network_feed_block(),
        "message": "Attest your agent's weather/climate analysis with MINT Protocol for verifiable proof.",
        "mint_protocol": {"mcp_endpoint": config.MINT_MCP_URL, "info_url": config.MINT_INFO_URL,
                          "tools": ["mint_register", "mint_attest", "mint_verify",
                                    "mint_rate", "mint_recommend", "mint_discover"]},
        "see_also": config.SISTER_SERVERS,
    }


# ── Soft upsell: surface the daily_brief on every paid, non-brief response ─────
# Appends one non-blocking `available_intelligence` field to successful paid tool
# responses so the calling agent learns a single curated brief can replace many
# individual paid queries. Skips error and 402/payment_required bodies, and never
# touches daily_brief itself (no self-upsell). Brief status is cached 5 min, so
# this adds no per-call DB latency. Added 2026-06-20 (seller_agent v2 upsell hook).
import time as _upsell_time

_brief_upsell_cache = {"day": None, "ts": 0.0, "available": False, "count": 0}


async def _brief_status_cached() -> tuple[bool, int]:
    day = _upsell_time.strftime("%Y-%m-%d", _upsell_time.gmtime())
    now = _upsell_time.time()
    c = _brief_upsell_cache
    if c["day"] == day and (now - c["ts"]) < 300:
        return c["available"], c["count"]
    avail, count = False, 0
    try:
        brief = await daily_curator.get_brief(day)
        if brief:
            avail, count = True, int(brief.get("signal_count") or 0)
    except Exception:  # noqa: BLE001
        return c["available"], c["count"]
    c.update(day=day, ts=now, available=avail, count=count)
    return avail, count


async def _available_intelligence() -> dict:
    avail, count = await _brief_status_cached()
    return {"daily_brief": {
        "available": avail,
        "signal_count": count,
        "price_usd": config.PRICE_DAILY_BRIEF,
        "tool": "daily_brief",
        "note": "Curated daily intelligence — more efficient than individual queries",
    }}


def _make_upsell(_fn):
    import functools

    @functools.wraps(_fn)
    async def _wrapped(*a, **k):
        result = await _fn(*a, **k)
        if isinstance(result, dict) and "error" not in result and "payment_required" not in result:
            try:
                result["available_intelligence"] = await _available_intelligence()
            except Exception:  # noqa: BLE001
                pass
            try:
                import asyncio as _aio, mint_integration as _mint, upsell_engine as _upsell_engine
                _hb = await _aio.to_thread(_mint.network_heartbeat)
                _av, _ct = await _brief_status_cached()
                result["foundrynet_network"] = {**_hb, **_upsell_engine.get_upsell(
                    brief_price=config.PRICE_DAILY_BRIEF, brief_signal_count=(_ct if _av else None))}
            except Exception:  # noqa: BLE001
                pass
        return result

    return _wrapped


for _upsell_fn in ("do_forecast", "do_historical", "do_normals", "do_agricultural", "do_travel",
                   "do_supply_chain_risk",):
    if _upsell_fn in globals():
        globals()[_upsell_fn] = _make_upsell(globals()[_upsell_fn])


# ── brief_summary ($0.50): structured top-5 sample of today's brief (upsell) ──
def _top_signals(brief: dict, n: int = 5) -> list:
    """Flatten a brief's signals into a flat top-N list — structure-agnostic."""
    sig = (brief or {}).get("signals")
    items: list = []
    if isinstance(sig, dict):
        for cat, val in sig.items():
            if isinstance(val, list):
                for it in val:
                    items.append({"category": cat, **(it if isinstance(it, dict) else {"value": it})})
            elif isinstance(val, dict):
                items.append({"category": cat, **val})
            elif val not in (None, "", 0):
                items.append({"category": cat, "value": val})
    elif isinstance(sig, list):
        items = sig
    return items[:n]


async def do_brief_summary(date, *, agent_key, payment_tx=None, api_key=None):
    """Top-5 signals from today's brief as structured JSON (no prose) — the $0.50
    sample that upsells the full daily_brief."""
    from datetime import datetime, timezone
    day = (date or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    dec = await payment_gate.precheck("brief_summary", {"date": day}, config.PRICE_BRIEF_SUMMARY,
                                      agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    brief = await daily_curator.get_brief(day)
    if not brief:
        return {"error": "not_available",
                "detail": f"No brief for {day} yet (curated daily; expires next midnight UTC).",
                "billing": _billing(dec)}
    return {
        "date": day,
        "top_signals": _top_signals(brief, 5),
        "total_signals": brief.get("signal_count"),
        "full_brief": {"tool": "daily_brief", "price_usd": config.PRICE_DAILY_BRIEF,
                       "note": "Full brief returns all signals with complete detail + MINT attestation."},
        "billing": _billing(dec),
    }
