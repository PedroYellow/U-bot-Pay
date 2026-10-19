"""Ubotpay cross-chain bridge integration.

Uses LI.FI as the primary bridge partner. LI.FI aggregates 20+ bridges
and DEXs to find the best route for any token pair across 25+ chains.

Public API: https://li.quest/v1/quote (no key required, 100k req/month free)
"""

import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import logging
from typing import Optional

log = logging.getLogger("ubotpay.bridge")

# LI.FI base URL (no API key required for basic quote)
LIFI_BASE = "https://li.quest/v1"

# Chain name to chain_id mapping (matches chain.py)
CHAIN_NAME = {
    "ethereum": 1, "eth": 1,
    "optimism": 10, "op": 10,
    "polygon": 137, "matic": 137,
    "base": 8453,
    "arbitrum": 42161, "arb": 42161,
    "avalanche": 43114, "avax": 43114, "avaxc": 43114,
    "fuji": 43113,
}

# USDC contract address per chain (for the bridge quote)
USDC_BY_CHAIN = {
    1:     "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    10:    "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    137:   "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    8453:  "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    43114: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
    43113: "0x5425890298aed601595a70AB815c96711a31Bc65",
}


def _http_get_json(url: str, timeout: int = 12) -> dict:
    """GET request and parse JSON, raising on error."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "ubotpay-bridge/1.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if not body:
            return {}
        return json.loads(body)


def quote(from_chain: int, to_chain: int, amount_usdc: float,
          from_address: str, to_address: str) -> dict:
    """Get a LI.FI bridge quote from one EVM chain to another.

    Args:
        from_chain: source chain id (e.g. 8453 for Base)
        to_chain: target chain id (e.g. 43114 for Avalanche)
        amount_usdc: amount in USDC (human units, e.g. 50.0)
        from_address: sender address (EVM)
        to_address: recipient address (EVM)

    Returns a dict with the bridge summary:
        - tool: bridge name (e.g. "Layerswap", "Stargate")
        - from_chain, to_chain
        - from_amount: exact source amount in token units
        - to_amount: estimated destination amount
        - to_amount_min: minimum destination (for slippage protection)
        - gas_cost_usd: estimated gas cost in USD
        - bridge_fee_usd: bridge fee in USD
        - total_fee_usd: total cost (gas + fee) in USD
        - execution_duration: estimated time in seconds
        - transaction: dict with to, data, value, from (for eth_sendTransaction)
        - estimate: raw LI.FI estimate
    """
    if from_chain not in USDC_BY_CHAIN:
        raise ValueError(f"unsupported source chain: {from_chain}")
    if to_chain not in USDC_BY_CHAIN:
        raise ValueError(f"unsupported target chain: {to_chain}")
    if from_chain == to_chain:
        raise ValueError("source and target chains must differ")
    if amount_usdc <= 0:
        raise ValueError("amount must be positive")

    from_token = USDC_BY_CHAIN[from_chain]
    to_token = USDC_BY_CHAIN[to_chain]
    # USDC has 6 decimals
    from_amount_int = int(amount_usdc * 1_000_000)

    params = {
        "fromChain": str(from_chain),
        "toChain": str(to_chain),
        "fromToken": from_token,
        "toToken": to_token,
        "fromAmount": str(from_amount_int),
        "fromAddress": from_address,
        "toAddress": to_address,
    }
    url = f"{LIFI_BASE}/quote?{urllib.parse.urlencode(params)}"
    log.info("LI.FI quote: %s", url)
    data = _http_get_json(url)

    # Parse and normalize
    estimate = data.get("estimate") or {}
    action = data.get("action") or {}
    tool_info = data.get("toolDetails") or {}
    gas_costs = estimate.get("gasCosts") or []
    fee_costs = estimate.get("feeCosts") or []

    # Sum gas in USD
    gas_usd = sum(float(c.get("amountUSD") or 0) for c in gas_costs)
    bridge_fee_usd = sum(float(c.get("amountUSD") or 0) for c in fee_costs)
    total_fee_usd = round(gas_usd + bridge_fee_usd, 4)

    return {
        "tool": tool_info.get("name") or data.get("tool") or "unknown",
        "from_chain": from_chain,
        "to_chain": to_chain,
        "from_amount": int(action.get("fromAmount") or from_amount_int),
        "from_amount_human": (int(action.get("fromAmount") or from_amount_int) / 1e6),
        "to_amount": int(estimate.get("toAmount") or 0),
        "to_amount_human": (int(estimate.get("toAmount") or 0)) / 1e6,
        "to_amount_min": int(estimate.get("toAmountMin") or 0),
        "to_amount_min_human": (int(estimate.get("toAmountMin") or 0)) / 1e6,
        "gas_cost_usd": round(gas_usd, 4),
        "bridge_fee_usd": round(bridge_fee_usd, 4),
        "total_fee_usd": total_fee_usd,
        "execution_duration": int(estimate.get("executionDuration") or 30),
        "approval_address": estimate.get("approvalAddress"),
        "transaction_request": action.get("fromAddress"),
        "raw": data,
    }


def get_status(bridge_id: str) -> dict:
    """Get the status of an in-flight bridge transaction.
    Uses LI.FI's status endpoint. The 'bridge_id' is typically the
    transaction hash on the source chain.
    """
    if not bridge_id:
        return {"status": "unknown", "message": "no bridge id"}
    url = f"{LIFI_BASE}/status?txHash={urllib.parse.quote(bridge_id)}"
    try:
        return _http_get_json(url)
    except Exception as e:
        log.warning("LI.FI status check failed: %s", e)
        return {"status": "pending", "error": str(e)[:120]}


# Smoke test on import
if __name__ == "__main__":
    q = quote(
        from_chain=8453,
        to_chain=43114,
        amount_usdc=1.0,
        from_address="0x713261F2151a759d9D935e6De2DB1d63A98176f6",
        to_address="0x713261F2151a759d9D935e6De2DB1d63A98176f6",
    )
    print(json.dumps({k: v for k, v in q.items() if k != "raw"}, indent=2))
