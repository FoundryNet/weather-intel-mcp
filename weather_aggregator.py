#!/usr/bin/env python3
"""weather_aggregator — keeps the cache warm.

  hourly: refresh the nationwide NWS active-alerts snapshot (weather_alerts) so
          weather_alerts queries by state are instant.
  daily:  warm climate_normals for a set of major locations.

The MCP server runs these in-process (hourly + daily). Manual entry point:
  python weather_aggregator.py alerts
  python weather_aggregator.py normals
  python weather_aggregator.py            # both
"""
from __future__ import annotations

import asyncio
import logging
import sys

import core
import supa
import weather_sources as ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("weather.agg")

# Warm normals for major metros (lat, lon).
_MAJOR = [
    (40.71, -74.01), (34.05, -118.24), (41.88, -87.63), (29.76, -95.37),
    (33.45, -112.07), (39.74, -104.98), (47.61, -122.33), (25.76, -80.19),
    (37.77, -122.42), (32.78, -96.80), (42.36, -71.06), (38.91, -77.04),
]


async def refresh_alerts() -> int:
    alerts = await ws.nws_alerts()  # nationwide
    await supa.replace_alerts(alerts)
    log.info(f"alerts: refreshed {len(alerts)} active NWS alerts")
    return len(alerts)


async def warm_normals() -> int:
    n = 0
    for lat, lon in _MAJOR:
        try:
            await core.do_normals(lat, lon, None, agent_key="agg:warm", api_key="internal")
            n += 1
        except Exception as e:  # noqa: BLE001
            log.info(f"normals warm {lat},{lon} failed: {e}")
    log.info(f"normals: warmed {n} locations")
    return n


async def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "both").lower()
    if mode in ("alerts", "both"):
        await refresh_alerts()
    if mode in ("normals", "both"):
        await warm_normals()


if __name__ == "__main__":
    asyncio.run(main())
