"""Pay-per-query gate for brand-intel's paid tools — on-chain USDC micropayments on
Solana, fronted by a daily free tier. Same model as gov-contracts-mcp, but the
price VARIES per call (domain_profile $0.02, tech_stack $0.01, batch_enrich
$0.01/domain min $0.05), so the price is threaded through the gate and bound into
the payment memo.

FLOW: paid tool called → if under FREE_TIER_DAILY for the agent, runs free →
otherwise 402 with a memo = intent(tool, params, price). Agent sends that exact
USDC amount with that memo, retries with payment_tx → gate verifies on-chain
(amount ≥ price, memo match, fresh, unused) → query runs. domain_age is free.
fnet_ Bearer key bypasses. httpx JSON-RPC only (no x402[svm] extra).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Optional

import config
import supa
from http_util import request_json

logger = logging.getLogger("weather.pay")

_USDC_DECIMALS = 6
_MEMO_PROGRAM_IDS = frozenset({
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
})

_mem_used_tx: dict = {}
_mem_free: dict = {}


def is_active() -> bool:
    return bool(config.X402_ENABLED and config.PAYMENT_RECIPIENT)


def _base_units(price_usdc: float) -> int:
    return round(price_usdc * (10 ** _USDC_DECIMALS))


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _fmt(price: float) -> str:
    """Precise USDC amount string (supports sub-cent prices like 0.005)."""
    return f"{price:.6f}".rstrip("0").rstrip(".")


def intent_id(tool: str, params: dict, price_usdc: float) -> str:
    canonical = json.dumps({"tool": tool, "params": params or {},
                            "price": f"{price_usdc:.6f}"},
                           sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def payment_required_body(tool: str, intent: str, price_usdc: float,
                          reason: Optional[str] = None) -> dict:
    body = {
        "status": 402,
        "error": "payment_required",
        "payment_required": {
            "amount": _fmt(price_usdc),
            "currency": "USDC",
            "network": "solana",
            "recipient": config.PAYMENT_RECIPIENT,
            "memo": intent,
            "expires_in": config.PAYMENT_EXPIRY_SECONDS,
            "usdc_mint": config.PAYMENT_USDC_MINT,
            "amount_base_units": _base_units(price_usdc),
            "decimals": _USDC_DECIMALS,
        },
        "instructions": (
            f"Daily free tier ({config.FREE_TIER_DAILY} queries) is spent. Send "
            f"{_fmt(price_usdc)} USDC ({config.PAYMENT_USDC_MINT}) to "
            f"{config.PAYMENT_RECIPIENT} on Solana with the SPL-memo set to "
            f"'{intent}', then call {tool} again with the SAME arguments plus "
            f"payment_tx=<transaction signature>."),
    }
    if reason:
        body["reason"] = reason
    return body


def _fail(code: str, detail: str) -> dict:
    return {"ok": False, "reason": code, "detail": detail}


async def verify_payment(tx_signature: str, expected_memo: str, price_usdc: float) -> dict:
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
           "params": [tx_signature, {"encoding": "jsonParsed",
                                     "maxSupportedTransactionVersion": 0,
                                     "commitment": "confirmed"}]}
    resp = await request_json("POST", config.PAYMENT_VERIFY_RPC, body=rpc,
                              timeout=config.REQUEST_TIMEOUT)
    if not isinstance(resp, dict) or "error" in resp:
        return _fail("rpc_error", f"Solana RPC call failed: {resp.get('detail') if isinstance(resp, dict) else resp}")
    if resp.get("error"):
        return _fail("rpc_error", f"Solana RPC error: {resp['error']}")
    result = resp.get("result")
    if result is None:
        return _fail("not_confirmed", "Transaction not found or not yet confirmed. Wait, then retry with the same payment_tx.")
    meta = result.get("meta") or {}
    if meta.get("err") is not None:
        return _fail("tx_failed", f"Transaction failed on-chain: {meta.get('err')}")
    block_time = result.get("blockTime")
    if block_time is None:
        return _fail("not_confirmed", "Transaction has no blockTime yet (still processing).")
    age = time.time() - block_time
    if age > config.PAYMENT_EXPIRY_SECONDS:
        return _fail("expired", f"Payment is {int(age)}s old; must be within {config.PAYMENT_EXPIRY_SECONDS}s. Make a fresh payment.")
    if age < -120:
        return _fail("clock_skew", "Transaction blockTime is in the future (clock skew).")
    delta = _usdc_delta_to_recipient(meta)
    if delta is None:
        return _fail("no_transfer", f"No USDC transfer to the operations wallet {config.PAYMENT_RECIPIENT} found in this tx.")
    need = _base_units(price_usdc)
    if delta < need:
        return _fail("underpaid", f"Transferred {delta / 10**_USDC_DECIMALS:.6f} USDC; need at least {_fmt(price_usdc)} USDC.")
    memo = _extract_memo(result, meta)
    if not memo or expected_memo not in memo:
        return _fail("memo_mismatch", f"Payment memo {memo!r} does not contain the required intent '{expected_memo}'. Pay with that exact memo.")
    return {"ok": True, "amount_base": delta, "amount_usdc": delta / 10**_USDC_DECIMALS,
            "payer": _payer(result), "block_time": block_time}


def _usdc_delta_to_recipient(meta: dict) -> Optional[int]:
    mint, recip = config.PAYMENT_USDC_MINT, config.PAYMENT_RECIPIENT
    pre = {b.get("accountIndex"): b for b in (meta.get("preTokenBalances") or [])}
    post = {b.get("accountIndex"): b for b in (meta.get("postTokenBalances") or [])}
    best: Optional[int] = None
    for idx, pb in post.items():
        if pb.get("mint") != mint or pb.get("owner") != recip:
            continue
        post_amt = int(pb.get("uiTokenAmount", {}).get("amount", 0))
        pre_amt = int((pre.get(idx) or {}).get("uiTokenAmount", {}).get("amount", 0))
        d = post_amt - pre_amt
        best = d if best is None or d > best else best
    return best


def _extract_memo(result: dict, meta: dict) -> Optional[str]:
    msg = (result.get("transaction") or {}).get("message") or {}
    instrs = list(msg.get("instructions") or [])
    for inner in (meta.get("innerInstructions") or []):
        instrs.extend(inner.get("instructions") or [])
    for ins in instrs:
        if ins.get("program") == "spl-memo" or ins.get("programId") in _MEMO_PROGRAM_IDS:
            p = ins.get("parsed")
            if isinstance(p, str):
                return p
            if isinstance(p, dict):
                return p.get("memo") or p.get("info")
    for line in (meta.get("logMessages") or []):
        m = re.search(r'Memo \(len \d+\): "(.*)"', line)
        if m:
            return m.group(1)
    return None


def _payer(result: dict) -> Optional[str]:
    keys = ((result.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    if keys:
        first = keys[0]
        return first.get("pubkey") if isinstance(first, dict) else first
    return None


async def _claim_free(agent_key: str) -> dict:
    day = _today()
    if supa.configured():
        r = await supa.claim_free_query(agent_key, day, config.FREE_TIER_DAILY)
        if r is not None:
            return r
    key = (agent_key, day)
    cur = _mem_free.get(key, 0)
    if cur < config.FREE_TIER_DAILY:
        _mem_free[key] = cur + 1
        return {"allowed": True, "count": cur + 1, "cap": config.FREE_TIER_DAILY}
    return {"allowed": False, "count": cur, "cap": config.FREE_TIER_DAILY}


async def _tx_used(tx_signature: str) -> bool:
    if supa.configured():
        return await supa.payment_tx_used(tx_signature)
    return tx_signature in _mem_used_tx


async def _reserve_payment(row: dict) -> bool:
    tx = row["tx_signature"]
    if supa.configured():
        res = await supa.insert_payment(row)
        if "error" in res:
            blob = json.dumps(res).lower()
            if "409" in blob or "duplicate" in blob or "unique" in blob:
                return False
            logger.error(f"payment ledger insert failed (treating as unreserved): {res}")
            return False
        return True
    if tx in _mem_used_tx:
        return False
    _mem_used_tx[tx] = row
    return True


def _has_api_key(api_key: Optional[str]) -> bool:
    return bool(api_key and api_key.strip())


async def precheck(tool: str, params: dict, price_usdc: float, agent_key: str,
                   payment_tx: Optional[str], api_key: Optional[str]) -> dict:
    """Gate a paid query at `price_usdc`. Returns gate: open|api_key|free|paid|blocked
    (blocked carries a 402 body)."""
    if not is_active():
        return {"gate": "open"}
    if _has_api_key(api_key):
        return {"gate": "api_key"}

    claim = await _claim_free(agent_key)
    if claim.get("allowed"):
        return {"gate": "free", "count": claim.get("count"), "cap": claim.get("cap")}

    intent = intent_id(tool, params, price_usdc)
    payment_tx = (payment_tx or "").strip()
    if not payment_tx:
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(tool, intent, price_usdc)}
    if await _tx_used(payment_tx):
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(tool, intent, price_usdc,
                    reason="This payment_tx was already used. Make a new payment.")}
    v = await verify_payment(payment_tx, intent, price_usdc)
    if not v["ok"]:
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(tool, intent, price_usdc, reason=v["detail"])}
    row = {"tx_signature": payment_tx, "intent": intent, "agent_key": agent_key,
           "tool": tool, "amount_usdc": v["amount_usdc"], "payer_wallet": v.get("payer"),
           "recipient": config.PAYMENT_RECIPIENT, "status": "settled",
           "block_time": v.get("block_time")}
    if not await _reserve_payment(row):
        return {"gate": "blocked", "status": 402,
                "body": payment_required_body(tool, intent, price_usdc,
                    reason="This payment_tx was already used (claimed concurrently). Make a new payment.")}
    logger.info(f"x402 payment verified: {payment_tx} {v['amount_usdc']:.6f} USDC for {tool}")
    return {"gate": "paid", "payment_tx": payment_tx, "amount_usdc": v["amount_usdc"]}
