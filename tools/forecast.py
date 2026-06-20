from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def forecast(
        latitude: float,
        longitude: float,
        days: int = 7,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Forecast weather for a location from Open-Meteo — up to 16-day daily
        outlook (high/low, conditions, precipitation probability, wind) plus the
        next 48 hours hourly. Cheap enough to call constantly.

        PAID: $0.005 USDC per query after a generous daily free allowance (50/day).
        On a 402, pay the returned Solana memo and re-call with the SAME args plus
        payment_tx=<signature>. agent_id scopes your allowance; an Authorization:
        Bearer fnet_ key bypasses it.

        Args:
            latitude: decimal latitude.
            longitude: decimal longitude.
            days: forecast days (1-16, default 7).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_forecast(latitude, longitude, days,
                                      agent_key=identity.resolve_agent_key(agent_id),
                                      payment_tx=payment_tx, api_key=identity.bearer())
