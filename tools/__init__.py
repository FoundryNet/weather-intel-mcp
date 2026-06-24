"""weather-intel-mcp tools — one per file.

  current_weather      (free)    temp/humidity/wind/conditions — loss leader
  forecast             ($0.005)  7-day daily + 48h hourly w/ precip prob
  historical_weather   ($0.01)   daily temp/precip/wind for a date range
  climate_normals      ($0.01)   ~20-30yr averages, frost dates, GDD
  weather_alerts       (free)    active NWS severe-weather alerts
  agricultural_outlook ($0.01)   GDD, frost risk, soil moisture, planting window
  travel_conditions    ($0.01)   two-location comparison + packing recs
  daily_brief          ($5)      curated daily brief — alerts, events, metro outlook, ag
  mint_info            (free)    FoundryNet Data Network + MINT cross-promo
"""
from . import current as current_tool
from . import forecast as forecast_tool
from . import historical as historical_tool
from . import normals as normals_tool
from . import alerts as alerts_tool
from . import agricultural as agricultural_tool
from . import travel as travel_tool
from . import supply_chain as supply_chain_tool
from . import daily_brief as daily_brief_tool
from . import brief_summary as brief_summary_tool
from . import mint as mint_tool


def register_all(mcp) -> None:
    for m in (current_tool, forecast_tool, historical_tool, normals_tool, alerts_tool,
              agricultural_tool, travel_tool, supply_chain_tool, daily_brief_tool,
              brief_summary_tool, mint_tool):
        m.register(mcp)
