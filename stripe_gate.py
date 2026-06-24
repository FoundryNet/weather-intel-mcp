"""Stripe Payment Link rail — a parallel payment option to x402 for the daily brief.

WHY: not every calling agent has a USDC wallet. This gives the agent's developer a
second way to pay — a hosted Stripe Payment Link — without touching the x402 path.

FLOW:
  1. On a 402, the brief's body now carries BOTH options: the x402 Solana memo AND a
     Stripe payment_link (config.STRIPE_LINK_DAILY_BRIEF).
  2. The buyer pays the link. Stripe creates a Checkout Session and (via the link's
     after_completion redirect) lands on a page showing the session id (cs_…).
  3. The agent re-calls daily_brief with stripe_token=<cs_…> (or header X-Stripe-Token).
  4. We retrieve the session from Stripe, confirm it's `paid` for ≥ the brief price in
     USD, consume it ONCE (dedup via the existing payments ledger, keyed
     "stripe:<session_id>"), and unlock the brief.

Fails CLOSED on every uncertainty. No new dependency — talks to Stripe's REST API with
the same http_util the rest of the server uses (secret key as a Bearer token). This
module is server-agnostic: it depends only on config (STRIPE_SECRET_KEY,
STRIPE_LINK_DAILY_BRIEF, PRICE_DAILY_BRIEF, PAYMENT_RECIPIENT), http_util.request_json,
and supa.payment_tx_used / supa.insert_payment — all identical across the brief servers.
"""
from __future__ import annotations

import logging
from typing import Optional

import config
import supa
from http_util import request_json

logger = logging.getLogger("stripe.gate")

_API = "https://api.stripe.com/v1"
# In-memory consume-set, used only when Supabase isn't configured (mirrors the
# x402 gate's _mem_used_tx fallback) so dedup still works in a degraded mode.
_mem_used: set[str] = set()


def is_active() -> bool:
    return bool(getattr(config, "STRIPE_SECRET_KEY", ""))


def link_url() -> Optional[str]:
    return getattr(config, "STRIPE_LINK_DAILY_BRIEF", "") or None


def _ledger_sig(session_id: str) -> str:
    return f"stripe:{session_id}"


async def _already_used(session_id: str) -> bool:
    sig = _ledger_sig(session_id)
    if supa.configured():
        try:
            if await supa.payment_tx_used(sig):
                return True
        except Exception:  # noqa: BLE001 — ledger read failure shouldn't crash the call
            pass
    return sig in _mem_used


async def _reserve(session_id: str, amount_usd: float, tool: str, agent_key: str,
                   payer: Optional[str], created: Optional[int]) -> bool:
    """Consume the session exactly once. Returns True if WE reserved it (caller may
    serve), False if it was already taken or the ledger write failed (fail closed,
    mirroring x402._reserve_payment)."""
    sig = _ledger_sig(session_id)
    if supa.configured():
        row = {"tx_signature": sig, "intent": "stripe_daily_brief", "agent_key": agent_key,
               "tool": tool, "amount_usdc": amount_usd, "payer_wallet": payer,
               "recipient": "stripe", "status": "settled-stripe", "block_time": created}
        try:
            res = await supa.insert_payment(row)
        except Exception as e:  # noqa: BLE001
            logger.error(f"stripe ledger insert raised (treating as unreserved): {e}")
            return False
        if isinstance(res, dict) and res.get("error"):
            blob = str(res).lower()
            if "409" in blob or "duplicate" in blob or "unique" in blob:
                return False  # someone else consumed it first
            logger.error(f"stripe ledger insert failed (treating as unreserved): {res}")
            return False
        return True
    # No Supabase — best-effort in-memory dedup.
    if sig in _mem_used:
        return False
    _mem_used.add(sig)
    return True


async def _get_session(session_id: str) -> Optional[dict]:
    if not is_active():
        return None
    headers = {"Authorization": f"Bearer {config.STRIPE_SECRET_KEY}"}
    return await request_json("GET", f"{_API}/checkout/sessions/{session_id}",
                              headers=headers, timeout=config.REQUEST_TIMEOUT)


def _fail(reason: str, detail: str) -> dict:
    return {"ok": False, "reason": reason, "detail": detail}


async def verify_session(stripe_token: str, price_usd: float, *, tool: str = "daily_brief",
                         agent_key: str = "stripe") -> dict:
    """Verify + consume a Stripe Checkout Session. Returns {ok: True, amount_usd, session}
    on success, else {ok: False, reason, detail}. Fails closed."""
    session_id = (stripe_token or "").strip()
    if not session_id:
        return _fail("bad_token", "No Stripe token provided.")
    if not session_id.startswith("cs_"):
        return _fail("bad_token", "Provide the Stripe Checkout Session id (starts with 'cs_'), "
                                  "shown on the page after you pay the link.")
    if not is_active():
        return _fail("stripe_disabled", "Stripe is not configured on this server.")
    if await _already_used(session_id):
        return _fail("used", "This Stripe payment was already redeemed. Pay the link again for a new brief.")

    sess = await _get_session(session_id)
    if not isinstance(sess, dict) or sess.get("error") or not sess.get("id"):
        msg = (sess or {}).get("error", {}).get("message") if isinstance(sess, dict) else None
        return _fail("not_found", f"Stripe session not found or unreadable. {msg or ''}".strip())
    if sess.get("payment_status") != "paid":
        return _fail("unpaid", f"Stripe session is not paid (status={sess.get('payment_status')}). "
                               "Complete the payment, then retry with the session id.")
    if (sess.get("currency") or "").lower() != "usd":
        return _fail("currency", "Stripe payment must be in USD.")
    amount_usd = (sess.get("amount_total") or 0) / 100.0
    if amount_usd + 1e-9 < round(float(price_usd), 2):
        return _fail("underpaid", f"Stripe payment was ${amount_usd:.2f}; this brief is "
                                  f"${float(price_usd):.2f}. Use the correct payment link.")

    payer = ((sess.get("customer_details") or {}).get("email")
             or sess.get("customer") or None)
    if not await _reserve(session_id, amount_usd, tool, agent_key, payer, sess.get("created")):
        return _fail("used", "This Stripe payment was already redeemed (claimed concurrently).")
    logger.info(f"stripe payment verified: {session_id} ${amount_usd:.2f} for {tool}")
    return {"ok": True, "amount_usd": amount_usd, "session": session_id, "payer": payer}


def augment_402(body: dict, price_usd: float, *, stripe_error: Optional[str] = None) -> dict:
    """Add a parallel `payment_options` block (Stripe + x402) to an existing x402 402
    body, non-destructively. The original `payment_required`/`instructions` fields stay
    so existing x402-only clients keep working."""
    if not isinstance(body, dict):
        return body
    pr = body.get("payment_required") or {}
    options: dict = {}
    link = link_url()
    if link:
        options["stripe"] = {
            "method": "Stripe Payment Link",
            "payment_link": link,
            "price_usd": round(float(price_usd), 2),
            "instructions": ("Pay the payment_link, then re-call daily_brief with "
                             "stripe_token=<Checkout Session id, cs_…> (or send it as the "
                             "X-Stripe-Token header). The session id is shown on the page "
                             "after payment."),
        }
    options["x402"] = {
        "method": "x402 USDC on Solana",
        "recipient": pr.get("recipient") or getattr(config, "PAYMENT_RECIPIENT", None),
        "amount_usdc": pr.get("amount"),
        "memo": pr.get("memo"),
        "network": "solana",
        "usdc_mint": pr.get("usdc_mint") or getattr(config, "PAYMENT_USDC_MINT", None),
    }
    body["price_usd"] = round(float(price_usd), 2)
    body["payment_options"] = options
    if stripe_error:
        body["stripe_error"] = stripe_error
    return body
