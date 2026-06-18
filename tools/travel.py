from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def travel_conditions(
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        date: Optional[str] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Weather comparison between two locations for trip planning — origin vs.
        destination forecast, the temp/precip deltas, active destination advisories,
        and structured packing recommendations (not prose).

        PAID: $0.01 USDC per query after the daily free allowance (50/day). On a
        402, pay the returned Solana memo and re-call with the SAME args plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            origin_lat, origin_lon: origin coordinates.
            dest_lat, dest_lon: destination coordinates.
            date: optional ISO date "YYYY-MM-DD" within the next 7 days (else today).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_travel(origin_lat, origin_lon, dest_lat, dest_lon, date,
                                    agent_key=identity.resolve_agent_key(agent_id),
                                    payment_tx=payment_tx, api_key=identity.bearer())
