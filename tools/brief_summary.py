from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def brief_summary(
        date: Optional[str] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Get the top 5 signals from today's brief as structured JSON — a cheap
        sample of the full daily_brief. Returns the day's highest-priority items
        (no prose) so an agent can decide whether to buy the full brief.

        PAID: $0.50 USDC (vs the full daily_brief price). Defaults to today (UTC).
        On a 402, pay the returned Solana memo and re-call with the SAME args plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses payment.

        Args:
            date: brief date YYYY-MM-DD (default today, UTC).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_brief_summary(
            date, agent_key=identity.resolve_agent_key(agent_id),
            payment_tx=payment_tx, api_key=identity.bearer())
