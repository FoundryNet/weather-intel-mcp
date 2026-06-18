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
        """Historical daily weather for a date range — high/low/mean temperature,
        precipitation, and max wind per day (global, from the Open-Meteo archive).

        PAID: $0.01 USDC per query after the daily free allowance (50/day). On a
        402, pay the returned Solana memo and re-call with the SAME args plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            latitude: decimal latitude.
            longitude: decimal longitude.
            date_from: ISO date "YYYY-MM-DD".
            date_to: ISO date "YYYY-MM-DD".
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_historical(latitude, longitude, date_from, date_to,
                                        agent_key=identity.resolve_agent_key(agent_id),
                                        payment_tx=payment_tx, api_key=identity.bearer())
