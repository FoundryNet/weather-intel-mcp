from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def climate_normals(
        latitude: float,
        longitude: float,
        month: Optional[int] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Get climate normals for a location — multi-decade monthly climate data
        averages (high/low/mean temp, precipitation), frost probabilities, average
        frost dates, and growing degree days. From the Open-Meteo archive (set
        NOAA_CDO_TOKEN for official 30-year NOAA normals).

        PAID: $0.01 USDC per query after the daily free allowance (50/day). On a
        402, pay the returned Solana memo and re-call with the SAME args plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            latitude: decimal latitude.
            longitude: decimal longitude.
            month: optional month 1-12 to return just that month (else all 12).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_normals(latitude, longitude, month,
                                     agent_key=identity.resolve_agent_key(agent_id),
                                     payment_tx=payment_tx, api_key=identity.bearer())
