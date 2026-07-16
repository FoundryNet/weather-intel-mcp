---
name: foundrynet-weather-intelligence
description: Keyless weather + climate intelligence (Open-Meteo + NWS) — current conditions, severe alerts, forecasts, climate normals, and agricultural outlooks
---

# FoundryNet Weather Intelligence

## Connect
```bash
claude mcp add --transport http foundrynet-weather https://weather-intel-mcp-production.up.railway.app/mcp
```

## Available Tools
- `current_weather` (free) — Live conditions for a city or coordinates
- `weather_alerts` (free) — Active NWS severe-weather alerts
- `forecast` ($0.005) — Multi-day forecast
- `historical_weather` ($0.01) — Past observations
- `climate_normals` ($0.01) — 30-year climate normals
- `agricultural_outlook` ($0.01) — Growing-degree-days, frost risk, soil, precip
- `travel_conditions` ($0.01) — Route / metro travel weather
- `daily_brief` ($5) — Curated daily weather brief, with verifiable attestation
- `mint_info` (free) — Network + provenance attestation info

A daily free-tier allowance precedes the paywall; paid tools settle via metered
pay-per-query **or** Stripe. An `Authorization: Bearer fnet_…` key bypasses the gate.

## Part of the FoundryNet Data Network
17 interconnected data-intelligence servers with verifiable, attested outputs.
Live network activity: https://mint.foundrynet.io/feed
