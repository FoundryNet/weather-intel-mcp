from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def supply_chain_risk(
        origin: str,
        destination: str,
        ship_date: Optional[str] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Score weather risk along a supply-chain / shipping route (0-100) with the
        specific threats at each endpoint and a shipment recommendation. Combines
        current conditions and active NWS severe-weather alerts at both the origin
        and destination into a single transport-risk score.

        PAID: $0.02 USDC per call after the daily free allowance (50/day). On a 402,
        pay the returned Solana memo and re-call with the SAME args plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            origin: origin city (e.g. "Memphis, TN") or "lat,lon".
            destination: destination city or "lat,lon".
            ship_date: optional planned ship date (YYYY-MM-DD), echoed in the result.
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_supply_chain_risk(
            origin, destination, ship_date,
            agent_key=identity.resolve_agent_key(agent_id),
            payment_tx=payment_tx, api_key=identity.bearer())
