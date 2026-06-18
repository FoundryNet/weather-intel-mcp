# Weather & Climate Intelligence MCP

**Weather & climate intelligence for AI agents** — current conditions, forecasts,
historical weather, climate normals, severe-weather alerts, agricultural weather,
and travel weather. Built on free, **keyless** sources (Open-Meteo + NWS).

> Part of the **FoundryNet Data Network**. Attest your agent's weather/climate
> analysis with [MINT Protocol](https://mint-mcp-production.up.railway.app/mcp).
> See also: **gov-contracts-mcp**, **brand-intel-mcp**, **patent-intel-mcp**,
> **financial-signals-mcp**.

Live MCP endpoint (Streamable HTTP):
`https://weather-intel-mcp-production.up.railway.app/mcp`

## Tools

| Tool | Price | What it does |
|---|---|---|
| `current_weather` | **free** | Temp, feels-like, humidity, wind, conditions, visibility |
| `forecast` | $0.005 | Up to 16-day daily + 48h hourly with precip probability |
| `historical_weather` | $0.01 | Daily temp/precip/wind for a date range |
| `climate_normals` | $0.01 | Multi-decade monthly averages, frost dates, growing degree days |
| `weather_alerts` | **free** | Active NWS severe-weather alerts (public safety) |
| `agricultural_outlook` | $0.01 | GDD, frost risk, soil moisture, precip outlook, planting window |
| `travel_conditions` | $0.01 | Two-location comparison + advisories + packing recs (structured) |
| `mint_info` | **free** | FoundryNet Data Network + MINT Protocol |

**Free tier:** 50 paid-tool queries/day per agent — generous, because weather is
the highest-frequency query type and we want maximum adoption (plus unlimited free
`current_weather` + `weather_alerts`). Then x402: the tool returns an HTTP-402 with
a Solana USDC payment memo — pay it, re-call with the same args plus
`payment_tx=<signature>`. An `Authorization: Bearer fnet_…` key bypasses the paywall.

## Sources

**Open-Meteo** (current/forecast/historical/archive/geocoding — keyless, global)
and **NWS** (`api.weather.gov` — keyless, US alerts). Climate normals and
agricultural signals are derived from the Open-Meteo archive; set `NOAA_CDO_TOKEN`
to use official NOAA 30-year normals instead. Responses are TTL-cached; the
nationwide alert snapshot refreshes hourly.

## Connect

Smithery: `@foundrynet/weather-intel` · MCP registry: `io.github.FoundryNet/weather-intel-mcp`

```json
{ "mcpServers": { "weather-intel": { "url": "https://weather-intel-mcp-production.up.railway.app/mcp" } } }
```

Built by [FoundryNet](https://foundrynet.io) · hello@foundrynet.io
