from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def historical_weather(
        latitude: float,
        longitude: float,
        date_from: str,
        date_to: str,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Get historical weather for a location and date range from the Open-Meteo
        archive — daily high/low/mean temperature, precipitation, and max wind per
        day (global climate data).

        PAID: $0.01 per query after the daily free allowance (50/day). On a
        402, pay the returned payment memo and re-call with the SAME args plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            latitude: decimal latitude.
            longitude: decimal longitude.
            date_from: ISO date "YYYY-MM-DD".
            date_to: ISO date "YYYY-MM-DD".
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: payment transaction signature, when re-calling after a 402.
        """
        return await core.do_historical(latitude, longitude, date_from, date_to,
                                        agent_key=identity.resolve_agent_key(agent_id),
                                        payment_tx=payment_tx, api_key=identity.bearer())
