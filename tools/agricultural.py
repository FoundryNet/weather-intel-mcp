from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def agricultural_outlook(
        latitude: float,
        longitude: float,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Get the agricultural weather outlook for a location from Open-Meteo —
        season-to-date growing degree days, frost risk over the next 14 days, soil
        moisture + soil temperature, 7-day precipitation outlook, and a
        planting-window assessment.

        PAID: $0.01 per query after the daily free allowance (50/day). On a
        402, pay the returned payment memo and re-call with the SAME args plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            latitude: decimal latitude.
            longitude: decimal longitude.
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: payment transaction signature, when re-calling after a 402.
        """
        return await core.do_agricultural(latitude, longitude,
                                          agent_key=identity.resolve_agent_key(agent_id),
                                          payment_tx=payment_tx, api_key=identity.bearer())
