"""weather-intel-mcp — weather & climate intelligence for autonomous agents.

Part of the FoundryNet Data Network. Free, keyless sources (Open-Meteo + NWS) with
a TTL cache. current_weather + weather_alerts are free (loss leaders / public
safety → maximum discovery); forecast is sub-cent; the rest are 1¢. A background
task refreshes the nationwide alert snapshot hourly and warms climate normals daily.

8 tools + free mint_info. Free tier 50/day, then x402 (USDC on Solana).
Transport: Streamable HTTP at /mcp (+ legacy /sse). Health: /health.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import core
import daily_curator
import identity
import payment_gate
import x402_standard
import supa
import tools
import weather_aggregator as agg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("weather.mcp")

if not supa.configured():
    logger.warning("SUPABASE_SERVICE_KEY not set — cache + alert snapshot disabled (live still works).")

mcp = FastMCP("weather-intel")

if payment_gate.is_active():
    logger.info(f"pay-per-query ARMED → {config.PAYMENT_RECIPIENT} after {config.FREE_TIER_DAILY}/day free "
                f"(forecast=${config.PRICE_FORECAST})")
else:
    logger.info("pay-per-query INERT — all tools free")

tools.register_all(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok", "service": "weather-intel-mcp", "transport": "streamable-http",
        "network": "FoundryNet Data Network",
        "tools": ["current_weather", "forecast", "historical_weather", "climate_normals",
                  "weather_alerts", "agricultural_outlook", "travel_conditions",
                  "supply_chain_risk", "daily_brief", "mint_info"],
        "cache": "supabase:weather_cache" if supa.configured() else "unconfigured",
        "sources": "open-meteo + nws (keyless)",
        "noaa_cdo": "set" if config.NOAA_CDO_TOKEN else "unset (normals derived from open-meteo)",
        "x402_enabled": config.X402_ENABLED,
        "query_payment": "armed" if payment_gate.is_active() else "free",
        "free_tier_daily": config.FREE_TIER_DAILY,
        "payment_recipient": config.PAYMENT_RECIPIENT,
    })


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ── REST surface ─────────────────────────────────────────────────────────────
_ERR = {"bad_request": 400, "not_configured": 503, "not_found": 404, "payment_required": 402}


def _resp(d: dict) -> JSONResponse:
    if "error" not in d:
        return JSONResponse(d, status_code=200)
    err = str(d.get("error") or "")
    code = _ERR.get(err, 502 if err in ("network", "non_json_response", "unreachable", "source_error") else 400)
    if err.startswith("http_") and err[5:].isdigit():
        code = int(err[5:])
    return JSONResponse(d, status_code=code)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def _akey(request: Request, body: dict) -> str:
    return identity.resolve_agent_key(body.get("agent_id"), request=request)


@mcp.custom_route("/v1/current", methods=["POST"])
async def rest_current(request: Request) -> JSONResponse:
    b = await _body(request)
    return _resp(await core.do_current(b.get("latitude"), b.get("longitude"),
                                       b.get("city"), b.get("state"), b.get("country")))


@mcp.custom_route("/v1/forecast", methods=["POST"])
async def rest_forecast(request: Request) -> JSONResponse:
    b = await _body(request)
    return _resp(await core.do_forecast(b.get("latitude"), b.get("longitude"), b.get("days", 7),
                                        agent_key=_akey(request, b), payment_tx=b.get("payment_tx"),
                                        api_key=identity.bearer(request)))


@mcp.custom_route("/v1/historical", methods=["POST"])
async def rest_historical(request: Request) -> JSONResponse:
    b = await _body(request)
    return _resp(await core.do_historical(b.get("latitude"), b.get("longitude"),
                                          b.get("date_from"), b.get("date_to"),
                                          agent_key=_akey(request, b), payment_tx=b.get("payment_tx"),
                                          api_key=identity.bearer(request)))


@mcp.custom_route("/v1/normals", methods=["POST"])
async def rest_normals(request: Request) -> JSONResponse:
    b = await _body(request)
    return _resp(await core.do_normals(b.get("latitude"), b.get("longitude"), b.get("month"),
                                       agent_key=_akey(request, b), payment_tx=b.get("payment_tx"),
                                       api_key=identity.bearer(request)))


@mcp.custom_route("/v1/alerts", methods=["GET", "POST"])
async def rest_alerts(request: Request) -> JSONResponse:
    b = await _body(request)
    return _resp(await core.do_alerts(b.get("state"), b.get("latitude"), b.get("longitude"),
                                      b.get("radius_km")))


@mcp.custom_route("/v1/agricultural", methods=["POST"])
async def rest_ag(request: Request) -> JSONResponse:
    b = await _body(request)
    return _resp(await core.do_agricultural(b.get("latitude"), b.get("longitude"),
                                            agent_key=_akey(request, b), payment_tx=b.get("payment_tx"),
                                            api_key=identity.bearer(request)))


@mcp.custom_route("/v1/travel", methods=["POST"])
async def rest_travel(request: Request) -> JSONResponse:
    b = await _body(request)
    return _resp(await core.do_travel(b.get("origin_lat"), b.get("origin_lon"),
                                      b.get("dest_lat"), b.get("dest_lon"), b.get("date"),
                                      agent_key=_akey(request, b), payment_tx=b.get("payment_tx"),
                                      api_key=identity.bearer(request)))


@mcp.custom_route("/v1/supply-chain-risk", methods=["POST"])
async def rest_supply_chain(request: Request) -> JSONResponse:
    b = await _body(request)
    return _resp(await core.do_supply_chain_risk(
        b.get("origin"), b.get("destination"), b.get("ship_date"),
        agent_key=_akey(request, b), payment_tx=b.get("payment_tx"),
        api_key=identity.bearer(request)))


@mcp.custom_route("/v1/mint-info", methods=["GET", "POST"])
async def rest_mint(request: Request) -> JSONResponse:
    return JSONResponse(core.mint_info())


@mcp.custom_route("/admin/refresh-alerts", methods=["POST"])
async def admin_refresh(request: Request) -> JSONResponse:
    import os
    tok = os.environ.get("ADMIN_TOKEN", "")
    if not tok or request.headers.get("x-admin-token") != tok:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    n = await agg.refresh_alerts()
    return JSONResponse({"refreshed_alerts": n})


# ── Discovery ────────────────────────────────────────────────────────────────
_TAGLINE = "Supply-chain weather risk scoring + raw NOAA forecasts & alerts for agents."
_DESC = ("Supply chain weather risk scoring and transport condition assessment. Score routes, "
         "flag threats, and get shipment recommendations (supply_chain_risk). Also provides raw "
         "NOAA forecasts, severe-weather alerts, historical weather, climate normals, agricultural "
         "weather, and travel comparison. Free current conditions + alerts. Part of the FoundryNet "
         "Data Network — attest analysis with MINT Protocol; see also gov-contracts, brand-intel, "
         "patent-intel, financial-signals.")
_KEYWORDS = ["supply-chain", "logistics", "shipping-risk", "transport-weather", "route-risk",
             "weather data", "forecast API", "climate data", "historical weather",
             "weather alerts", "agricultural weather", "travel weather"]

_AGENT_CARD = {
    "name": "Supply Chain Weather Risk Scorer",
    "description": ("Score supply-chain / shipping routes for weather risk (0-100) with specific "
                    "threats and shipment recommendations. Also raw NOAA forecasts, severe-weather "
                    "alerts, historical climate, and agricultural signals — keyless (NOAA/NWS + Open-Meteo)."),
    "url": "https://weather-intel-mcp-production.up.railway.app/mcp",
    "version": "1.0.0",
    "capabilities": {"tools": ["current_weather", "forecast", "historical_weather",
                               "climate_normals", "weather_alerts", "agricultural_outlook",
                               "travel_conditions", "supply_chain_risk", "daily_brief", "mint_info"]},
    "provider": {"name": "FoundryNet", "url": "https://foundrynet.io"},
    "network": "FoundryNet Data Network",
    "attestation": {"protocol": "MINT Protocol",
                    "endpoint": "https://mint-mcp-production.up.railway.app/mcp",
                    "verified_outputs": True, "live_feed": "https://mint.foundrynet.io/feed", "feed_api": "https://mint-mcp-production.up.railway.app/v1/feed"},
    "protocols": {"mcp": {"endpoint": config.PUBLIC_MCP_URL, "transport": "streamable-http", "tools_count": 10},
                  "x402": {"supported": True, "currency": "USDC", "network": "solana"}},
    "see_also": config.SISTER_SERVERS, "mint_protocol": config.MINT_MCP_URL,
    "contact": "hello@foundrynet.io",
}


@mcp.custom_route("/.well-known/agent-card.json", methods=["GET"])
async def agent_card(request: Request) -> JSONResponse:
    return JSONResponse(_AGENT_CARD, headers={"Cache-Control": "public, max-age=300"})


@mcp.custom_route("/.well-known/mcp", methods=["GET"])
async def mcp_endpoints(request: Request) -> JSONResponse:
    return JSONResponse({"endpoints": [{"url": config.PUBLIC_MCP_URL, "transport": "streamable-http",
                                        "name": "Weather & Climate Intelligence MCP"}]},
                        headers={"Cache-Control": "public, max-age=300"})


async def _live_tools() -> list:
    res = mcp.list_tools()
    if inspect.iscoroutine(res):
        res = await res
    return [{"name": t.name, "description": (getattr(t, "description", "") or "").strip(),
             "inputSchema": getattr(t, "parameters", None) or {"type": "object"}} for t in res]


@mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def server_card(request: Request) -> JSONResponse:
    live = await _live_tools()
    return JSONResponse({
        "serverInfo": {"name": "Weather & Climate Intelligence MCP", "version": "1.0.0"},
        "authentication": {"type": "http", "scheme": "bearer",
                           "description": ("current_weather, weather_alerts, and mint_info are free; other "
                                           "tools give 50 free queries/day then take an fnet_ Bearer key OR x402 USDC.")},
        "tools": live, "version": "1.0", "name": "Weather & Climate Intelligence MCP",
        "tagline": _TAGLINE, "description": _DESC,
        "serverUrl": config.PUBLIC_MCP_URL, "transport": "streamable-http",
        "tools_count": len(live),
        "categories": ["weather", "climate", "data", "agriculture", "travel"],
        "keywords": _KEYWORDS, "network": "FoundryNet Data Network",
        "see_also": config.SISTER_SERVERS,
        "pricing": {"model": "metered",
                    "free_tier": f"{config.FREE_TIER_DAILY} queries/day + free current_weather & weather_alerts",
                    "paid_from": f"{config.PRICE_FORECAST} USDC per query (x402)"},
    }, headers={"Cache-Control": "public, max-age=300"})


# ── Background: hourly alerts + daily normals ────────────────────────────────
async def _alerts_loop():
    while True:
        try:
            if supa.configured():
                await agg.refresh_alerts()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"alerts loop: {e}")
        await asyncio.sleep(config.ALERT_REFRESH_MIN * 60)


async def _normals_loop():
    while True:
        await asyncio.sleep(86400)
        try:
            if supa.configured():
                await agg.warm_normals()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"normals loop: {e}")


_FREE_TOOL_NAMES = {"mint_info", "macro_dashboard", "cve_detail", "detail",
                    "domain_age", "convert", "rates", "market_overview", "price",
                    "quote", "batch_quote", "sector_performance"}


@mcp.custom_route("/.well-known/mcp.json", methods=["GET"])
async def wellknown_mcp_json(request: Request) -> JSONResponse:
    """Machine-discovery card (emerging standard) for AI clients/crawlers."""
    live = await _live_tools()
    names = [t["name"] for t in live]
    return JSONResponse({
        "name": _AGENT_CARD["name"],
        "description": _AGENT_CARD["description"],
        "url": config.PUBLIC_MCP_URL,
        "transport": ["streamable-http"],
        "tools": names,
        "pricing": {"model": "per-query", "free_tier": True,
                    "paid_tools": [n for n in names if n not in _FREE_TOOL_NAMES]},
        "attestation": {"enabled": True, "protocol": "MINT Protocol",
                        "feed": "https://mint.foundrynet.io/feed"},
        "network": {"name": "FoundryNet Data Network", "servers": 17,
                    "homepage": "https://foundrynet.io"},
    }, headers={"Cache-Control": "public, max-age=300"})



# ── Standard x402 compliance (discoverable on x402scan / 402 Index / CDP Bazaar) ──
@mcp.custom_route("/x402", methods=["GET"])
async def x402_index(request: Request) -> JSONResponse:
    return JSONResponse(x402_standard.index(),
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*"})


@mcp.custom_route("/.well-known/x402", methods=["GET"])
async def x402_wellknown(request: Request) -> JSONResponse:
    return JSONResponse(x402_standard.index(),
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*"})


@mcp.custom_route("/x402/{tool}", methods=["GET", "POST"])
async def x402_resource(request: Request) -> JSONResponse:
    tool = request.path_params["tool"]
    if tool not in x402_standard.PAID_TOOLS:
        return JSONResponse({"error": "unknown_resource", "tool": tool,
                             "available": list(x402_standard.PAID_TOOLS)}, status_code=404)
    challenge = x402_standard.payment_required_header(tool)
    return JSONResponse(x402_standard.payment_required(tool), status_code=402,
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*",
                                 "PAYMENT-REQUIRED": challenge,
                                 "X-PAYMENT": challenge,
                                 "Link": '</openapi.json>; rel="describedby"',
                                 "WWW-Authenticate": 'x402 version="2"'})


@mcp.custom_route("/openapi.json", methods=["GET"])
async def openapi_doc(request: Request) -> JSONResponse:
    """OpenAPI 3.1 discovery doc — x402scan requires a spec at a discoverable URL."""
    return JSONResponse(x402_standard.openapi(),
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*",
                                 "Link": '</openapi.json>; rel="describedby"'})


def build_dual_app():
    main_app = mcp.http_app(transport="http", path="/mcp")
    sse_app = mcp.http_app(transport="sse", path="/sse")
    for r in sse_app.routes:
        if getattr(r, "path", None) in ("/sse", "/messages"):
            main_app.router.routes.append(r)
    main_life, sse_life = main_app.router.lifespan_context, sse_app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _dual_lifespan(app):
        async with main_life(app):
            async with sse_life(app):
                t1 = asyncio.create_task(_alerts_loop())
                t2 = asyncio.create_task(_normals_loop())
                brief_task = asyncio.create_task(daily_curator.curator_loop())
                try:
                    yield
                finally:
                    for t in (t1, t2, brief_task):
                        t.cancel()
                        with contextlib.suppress(Exception):
                            await t
    main_app.router.lifespan_context = _dual_lifespan
    return main_app


if __name__ == "__main__":
    import uvicorn
    logger.info(f"weather-intel-mcp starting on 0.0.0.0:{config.PORT} "
                f"(cache={'supabase' if supa.configured() else 'off'}, x402={config.X402_ENABLED})")
    uvicorn.run(build_dual_app(), host="0.0.0.0", port=config.PORT, log_level="warning")
