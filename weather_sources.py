"""Free weather/climate data clients + derivations.

Open-Meteo (current/forecast/historical/archive/geocoding, keyless, global) + NWS
(alerts, US). Climate normals + agricultural signals are derived from the
Open-Meteo archive (NOAA CDO is an optional upgrade for official normals). All
async via the shared request_json helper.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import config
from http_util import request_json

logger = logging.getLogger("weather.src")

# WMO weather interpretation codes → text.
WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
    55: "Dense drizzle", 56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain", 66: "Light freezing rain",
    67: "Heavy freezing rain", 71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers",
    82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ slight hail", 99: "Thunderstorm w/ heavy hail",
}


def _wmo(code) -> str:
    try:
        return WMO.get(int(code), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


def _nws_headers():
    return {"User-Agent": config.NWS_USER_AGENT, "Accept": "application/geo+json"}


# ── geocoding ─────────────────────────────────────────────────────────────────
async def geocode(city: str, state=None, country=None) -> Optional[dict]:
    r = await request_json("GET", config.OPEN_METEO_GEO,
                           params={"name": city, "count": "5", "language": "en", "format": "json"},
                           timeout=config.REQUEST_TIMEOUT)
    results = (r or {}).get("results") if isinstance(r, dict) else None
    if not results:
        return None
    if state:
        for x in results:
            if (x.get("admin1") or "").lower().startswith(state.lower()) or \
               (x.get("admin1_id") and state.upper() == (x.get("admin1") or "")[:2].upper()):
                results = [x]; break
    if country:
        for x in results:
            if (x.get("country_code") or "").lower() == country.lower() or \
               (x.get("country") or "").lower() == country.lower():
                results = [x]; break
    x = results[0]
    return {"latitude": x.get("latitude"), "longitude": x.get("longitude"),
            "name": x.get("name"), "admin1": x.get("admin1"), "country": x.get("country")}


async def resolve_location(latitude=None, longitude=None, city=None, state=None, country=None):
    if latitude is not None and longitude is not None:
        return {"latitude": float(latitude), "longitude": float(longitude), "name": None}
    if city:
        return await geocode(city, state, country)
    return None


# ── current ───────────────────────────────────────────────────────────────────
async def current(lat, lon) -> dict:
    r = await request_json("GET", config.OPEN_METEO, params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
                   "precipitation,weather_code,wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover",
        "hourly": "visibility", "forecast_days": "1",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "timezone": "auto"}, timeout=config.REQUEST_TIMEOUT)
    if not isinstance(r, dict) or "current" not in r:
        return {"error": "source_error", "detail": str(r)[:200]}
    c = r["current"]
    vis = None
    hv = (r.get("hourly") or {}).get("visibility") or []
    if hv:
        vis = hv[0]
    return {
        "latitude": lat, "longitude": lon, "timezone": r.get("timezone"),
        "observed_at": c.get("time"),
        "temp_f": c.get("temperature_2m"), "feels_like_f": c.get("apparent_temperature"),
        "humidity_pct": c.get("relative_humidity_2m"),
        "wind_mph": c.get("wind_speed_10m"), "wind_gust_mph": c.get("wind_gusts_10m"),
        "wind_dir_deg": c.get("wind_direction_10m"),
        "precip_in": c.get("precipitation"), "cloud_cover_pct": c.get("cloud_cover"),
        "visibility_m": vis, "conditions": _wmo(c.get("weather_code")),
        "is_day": bool(c.get("is_day")),
    }


# ── forecast ──────────────────────────────────────────────────────────────────
async def forecast(lat, lon, days=7) -> dict:
    days = min(max(int(days or 7), 1), 16)
    r = await request_json("GET", config.OPEN_METEO, params={
        "latitude": lat, "longitude": lon, "forecast_days": str(days),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "timezone": "auto"}, timeout=config.REQUEST_TIMEOUT)
    if not isinstance(r, dict) or "daily" not in r:
        return {"error": "source_error", "detail": str(r)[:200]}
    d = r["daily"]
    daily = []
    for i, day in enumerate(d.get("time", [])):
        daily.append({"date": day, "conditions": _wmo(d["weather_code"][i]),
                      "high_f": d["temperature_2m_max"][i], "low_f": d["temperature_2m_min"][i],
                      "precip_in": d["precipitation_sum"][i],
                      "precip_prob_pct": d["precipitation_probability_max"][i],
                      "wind_max_mph": d["wind_speed_10m_max"][i],
                      "sunrise": d["sunrise"][i], "sunset": d["sunset"][i]})
    h = r.get("hourly") or {}
    hourly = [{"time": h["time"][i], "temp_f": h["temperature_2m"][i],
               "precip_prob_pct": h["precipitation_probability"][i],
               "conditions": _wmo(h["weather_code"][i])}
              for i in range(min(len(h.get("time", [])), 48))]
    return {"latitude": lat, "longitude": lon, "timezone": r.get("timezone"),
            "days": days, "daily": daily, "hourly_48h": hourly}


# ── historical ────────────────────────────────────────────────────────────────
async def historical(lat, lon, date_from, date_to) -> dict:
    r = await request_json("GET", config.OPEN_METEO_ARCHIVE, params={
        "latitude": lat, "longitude": lon, "start_date": date_from, "end_date": date_to,
        "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_sum,"
                 "wind_speed_10m_max",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "timezone": "auto"}, timeout=max(config.REQUEST_TIMEOUT, 45))
    if not isinstance(r, dict) or "daily" not in r:
        return {"error": "source_error", "detail": str(r)[:200]}
    d = r["daily"]
    rows = [{"date": d["time"][i], "high_f": d["temperature_2m_max"][i],
             "low_f": d["temperature_2m_min"][i], "mean_f": d["temperature_2m_mean"][i],
             "precip_in": d["precipitation_sum"][i], "wind_max_mph": d["wind_speed_10m_max"][i]}
            for i in range(len(d.get("time", [])))]
    return {"latitude": lat, "longitude": lon, "from": date_from, "to": date_to,
            "days": len(rows), "daily": rows}


# ── climate normals (derived from Open-Meteo archive) ────────────────────────
async def normals(lat, lon, month=None, start_year=2004, end_year=2023) -> dict:
    r = await request_json("GET", config.OPEN_METEO_ARCHIVE, params={
        "latitude": lat, "longitude": lon,
        "start_date": f"{start_year}-01-01", "end_date": f"{end_year}-12-31",
        "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_sum",
        "temperature_unit": "fahrenheit", "precipitation_unit": "inch", "timezone": "auto"},
        timeout=max(config.REQUEST_TIMEOUT, 60))
    if not isinstance(r, dict) or "daily" not in r:
        return {"error": "source_error", "detail": str(r)[:200]}
    d = r["daily"]
    times = d.get("time", [])
    by_month = {m: {"max": [], "min": [], "mean": [], "precip_by_year": {}, "frost_years": set(), "years": set()}
                for m in range(1, 13)}
    last_spring, first_fall = {}, {}  # year -> day-of-year
    for i, ds in enumerate(times):
        try:
            dt = datetime.fromisoformat(ds)
        except ValueError:
            continue
        m, y = dt.month, dt.year
        b = by_month[m]
        mx, mn, me, pr = (d["temperature_2m_max"][i], d["temperature_2m_min"][i],
                          d["temperature_2m_mean"][i], d["precipitation_sum"][i])
        if mx is not None: b["max"].append(mx)
        if mn is not None: b["min"].append(mn)
        if me is not None: b["mean"].append(me)
        if pr is not None:
            b["precip_by_year"][y] = b["precip_by_year"].get(y, 0) + pr
        b["years"].add(y)
        if mn is not None and mn <= 32:  # frost (°F)
            b["frost_years"].add(y)
            doy = dt.timetuple().tm_yday
            if dt.month <= 6:
                last_spring[y] = max(last_spring.get(y, 0), doy)
            else:
                first_fall[y] = min(first_fall.get(y, 999), doy)

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    months = {}
    for m in range(1, 13):
        b = by_month[m]
        yrs = len(b["years"]) or 1
        precip_avg = round(sum(b["precip_by_year"].values()) / yrs, 2) if b["precip_by_year"] else None
        mean_t = avg(b["mean"])
        gdd = None
        if b["mean"]:
            gdd = round(sum(max(t - 50, 0) for t in b["mean"]) / yrs, 0)  # base 50°F monthly avg
        months[m] = {"avg_high_f": avg(b["max"]), "avg_low_f": avg(b["min"]),
                     "avg_temp_f": mean_t, "avg_precip_in": precip_avg,
                     "frost_probability_pct": round(len(b["frost_years"]) / yrs * 100),
                     "growing_degree_days": gdd}

    def doy_to_date(doy):
        try:
            return datetime(2001, 1, 1).fromordinal(datetime(2001, 1, 1).toordinal() + int(doy) - 1).strftime("%b %d")
        except Exception:  # noqa: BLE001
            return None
    avg_last_spring = round(sum(last_spring.values()) / len(last_spring)) if last_spring else None
    avg_first_fall = round(sum(first_fall.values()) / len(first_fall)) if first_fall else None
    out = {
        "latitude": lat, "longitude": lon, "period": f"{start_year}-{end_year} (~20yr)",
        "source": "derived from Open-Meteo archive" + (" (set NOAA_CDO_TOKEN for official 30yr normals)" if not config.NOAA_CDO_TOKEN else ""),
        "frost_dates": {"avg_last_spring_frost": doy_to_date(avg_last_spring),
                        "avg_first_fall_frost": doy_to_date(avg_first_fall),
                        "growing_season_days": (avg_first_fall - avg_last_spring) if (avg_first_fall and avg_last_spring) else None},
    }
    if month:
        out["month"] = int(month)
        out["normals"] = months.get(int(month))
    else:
        out["monthly"] = {datetime(2001, m, 1).strftime("%B"): months[m] for m in range(1, 13)}
    return out


# ── agricultural outlook ──────────────────────────────────────────────────────
async def agricultural(lat, lon) -> dict:
    # Forecast block: soil moisture, next-14d mins (frost), 7d precip.
    fc = await request_json("GET", config.OPEN_METEO, params={
        "latitude": lat, "longitude": lon, "forecast_days": "14",
        "daily": "temperature_2m_min,temperature_2m_max,temperature_2m_mean,precipitation_sum",
        "current": "soil_moisture_0_to_1cm,soil_temperature_0cm",
        "hourly": "soil_moisture_3_to_9cm",
        "temperature_unit": "fahrenheit", "precipitation_unit": "inch", "timezone": "auto"},
        timeout=config.REQUEST_TIMEOUT)
    if not isinstance(fc, dict) or "daily" not in fc:
        return {"error": "source_error", "detail": str(fc)[:200]}
    d = fc["daily"]
    mins = [x for x in d.get("temperature_2m_min", []) if x is not None]
    frost_days = sum(1 for x in mins if x <= 32)
    precip_7d = round(sum((d.get("precipitation_sum") or [])[:7]), 2)
    # Season-to-date GDD from archive (Jan 1 → today).
    year = time.gmtime().tm_year
    today = time.strftime("%Y-%m-%d", time.gmtime())
    arch = await request_json("GET", config.OPEN_METEO_ARCHIVE, params={
        "latitude": lat, "longitude": lon, "start_date": f"{year}-01-01", "end_date": today,
        "daily": "temperature_2m_mean", "temperature_unit": "fahrenheit", "timezone": "auto"},
        timeout=max(config.REQUEST_TIMEOUT, 45))
    gdd = None
    if isinstance(arch, dict) and "daily" in arch:
        means = [x for x in arch["daily"].get("temperature_2m_mean", []) if x is not None]
        gdd = round(sum(max(t - 50, 0) for t in means))  # base 50°F season-to-date
    soil = (fc.get("current") or {}).get("soil_moisture_0_to_1cm")
    soil_temp = (fc.get("current") or {}).get("soil_temperature_0cm")
    # Planting window heuristic.
    if frost_days == 0 and soil_temp is not None and soil_temp >= 50:
        window = "favorable — no frost in 14d and soil ≥ 50°F"
    elif frost_days > 0:
        window = f"caution — frost expected on {frost_days} of next 14 days"
    else:
        window = "marginal — soil still cool"
    return {"latitude": lat, "longitude": lon,
            "growing_degree_days_season_to_date": gdd,
            "frost_risk_next_14d_days": frost_days,
            "soil_moisture_0_1cm": soil, "soil_temp_0cm_f": soil_temp,
            "precip_outlook_7d_in": precip_7d,
            "planting_window_assessment": window}


# ── NWS alerts ────────────────────────────────────────────────────────────────
def _alert_row(feat: dict) -> dict:
    p = feat.get("properties") or {}
    geo = p.get("geocode") or {}
    states = sorted({s[:2] for s in (geo.get("UGC") or []) if s})  # UGC starts with state code
    return {"alert_id": feat.get("id") or p.get("id"), "event": p.get("event"),
            "severity": p.get("severity"), "urgency": p.get("urgency"),
            "certainty": p.get("certainty"), "headline": p.get("headline"),
            "area_desc": p.get("areaDesc"), "states": ",".join(states),
            "onset": p.get("onset"), "expires": p.get("expires"),
            "sender": p.get("senderName"), "payload": p}


async def nws_alerts(state=None, lat=None, lon=None) -> list:
    if lat is not None and lon is not None:
        url = f"{config.NWS_API}/alerts/active?point={lat},{lon}"
    elif state:
        url = f"{config.NWS_API}/alerts/active?area={state.upper()}"
    else:
        url = f"{config.NWS_API}/alerts/active"
    r = await request_json("GET", url, headers=_nws_headers(), timeout=config.REQUEST_TIMEOUT)
    feats = (r or {}).get("features") if isinstance(r, dict) else None
    return [_alert_row(f) for f in (feats or [])]
