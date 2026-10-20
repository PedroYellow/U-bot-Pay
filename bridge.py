"""Ubotpay cross-chain bridge integration.

Uses LI.FI as the primary bridge partner. LI.FI aggregates 20+ bridges
and DEXs to find the best route for any token pair across 25+ chains.

Public API: https://li.quest/v1/quote (no key required, 100k req/month free)
"""

import os
import json
import time
import threading
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
    "sonic": 146, "s": 146,
    "solana": 1151111081099710, "sol": 1151111081099710,
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
    # L2s added Phase 8
    324:    "0x1d17CBcF0D6D143135aE902365D2E5e2A16538D4",   # zkSync Era
    59144:  "0x176211869cA2b568f2A7D4EE941E073a821EE1ff",   # Linea
    534352: "0x06eFdBFf2a14a7c8E15944D1F4A48F9F95F663A4",   # Scroll
    5000:   "0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9",   # Mantle
    # Phase 9 chains
    42220:  "0xcebA9300f2b948710d2653dD7B07f33A8B32118C",   # Celo (native USDC)
    480:    "0x79A02482A880bCE3F13e09Da970dC34db4CD24d1",   # World Chain (native USDC)
    130:    "0x078D782b760474a361dDA0AF3839290b0EF57AD6",   # Unichain (native USDC)
    # Solana. Not EVM — the chain id here is LI.FI's internal id, not the
    # CAIP-2 numeric one (which is "solana:2y3yub" / a genesis-hash string).
    # Only usable as a DESTINATION: the user signs an ordinary EVM transaction on
    # the source chain and LI.FI handles the Solana leg. Solana -> EVM does not
    # produce an eth_sendTransaction payload (from/chainId come back null), so it
    # is rejected rather than handed to a wallet that cannot sign it.
    1151111081099710: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",   # Solana USDC (SPL)
    # Sonic. Address verified on-chain 20 Oct 2026 — the USDC addresses that
    # circulate on the web are wrong (eth_getCode returns empty for those).
    # decimals()->6, symbol()=="USDC", 110.6M supply. Kept in sync with
    # chain.py — these two lists drifting apart would mean the UI shows one
    # token while the bridge quotes another.
    146:    "0x29219dd400f2Bf60E5a23d13Be72B486D4038894",   # Sonic (native USDC)
}

# USDT, one canonical address per chain. Every one of these was checked against
# LI.FI's own token list for that chain (each chain returns exactly one USDT,
# which is the canonical one) and then confirmed to produce a quote in both
# directions — Avalanche->X and X->Avalanche. Measured loss 0.15%-1.40%, versus
# 23% for the USDC route on Tron, which is why USDT routes are viable here and
# those are not.
#
# Tron is deliberately absent: the only route to it loses 42% even USDT-to-USDT.
# See TRON_BLOCKERS.md.
USDT_BY_CHAIN = {
    1:     "0xdAC17F958D2ee523a2206206994597C13D831ec7",   # Ethereum (Tether)
    137:   "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",   # Polygon (Po bridged)
    146:   "0x6047828dc181963ba44974801FF68e538dA5eaF9",   # Sonic (bridged)
    8453:  "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",   # Base (bridged)
    43114: "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",   # Avalanche (Po bridged)
    59144: "0xA219439258ca9da29E9Cc4cE5596924745e12B93",   # Linea (bridged)
}

# Stablecoins the bridge can move, and the map that holds each one. Kept as one
# table so a new chain has to be added in a single place: this function used to
# read USDC_BY_CHAIN directly, and adding a second token is what makes that
# pattern a bug waiting to happen (see test_chain_registry.py).
TOKENS_BY_SYMBOL = {"USDC": USDC_BY_CHAIN, "USDT": USDT_BY_CHAIN}


def _token_map(token: str) -> dict:
    """Map for a stablecoin symbol, rejecting anything we do not quote.

    Case-insensitive because the API and the UI spell it both ways. An unknown
    symbol must fail rather than fall back to USDC — that would move the user
    into a different token without saying so.
    """
    key = (token or "").strip().upper()
    if key not in TOKENS_BY_SYMBOL:
        raise _UnsupportedChain(
            f"unsupported token: {token!r} "
            f"(available: {', '.join(sorted(TOKENS_BY_SYMBOL))})")
    return TOKENS_BY_SYMBOL[key]

# Chains that work as a bridge destination but not as a source, because the
# outgoing transaction would live on a non-EVM chain. Quoting them from our side
# succeeds at LI.FI and then fails at signing, which is the worst place to fail.
_DESTINATION_ONLY_CHAINS = {1151111081099710}   # Solana


class _UnsupportedChain(ValueError):
    """The chain id is not in our map at all.

    A caller error: no amount of retrying will make LI.FI know about it. Separate
    from _BridgeUpstreamError so the API can answer 400 instead of 502 — and
    from the generic ValueError so get_best_quote's per-partner `except` does not
    swallow it and report a transient failure.
    """


class _BridgeUpstreamError(ValueError):
    """The providers failed, as opposed to the request being impossible.

    Subclasses ValueError so existing callers that catch ValueError keep
    working, but api.py can tell the two apart and answer 502 only for this one.
    """


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
          from_address: str, to_address: str, use_cache: bool = True,
          token: str = "USDC") -> dict:
    """Quote a stablecoin bridge route.

    `token` selects which stablecoin moves on both sides ("USDC" or "USDT") and
    defaults to USDC, so every existing caller is unaffected. `amount_usdc` keeps
    its old name for the same reason, but it now means "amount of `token`". The
    returned dict carries `token` so a caller never has to assume which it got.
    """
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
    _tmap = _token_map(token)
    if from_chain not in _tmap:
        raise _UnsupportedChain(f"unsupported source chain: {from_chain} for {token}")
    if from_chain in _DESTINATION_ONLY_CHAINS:
        # LI.FI quotes Solana -> EVM, but the response carries no
        # eth_sendTransaction payload (from and chainId are null) because the leg
        # to be signed is a Solana transaction. Handing that to an EVM wallet
        # would produce a confusing failure at signing time, so refuse here with
        # the reason rather than after the user has picked an amount.
        raise ValueError(
            f"chain {from_chain} can only be a destination, not a source: "
            "the outgoing leg would be a Solana transaction this app cannot sign")
    if to_chain not in _tmap:
        raise _UnsupportedChain(f"unsupported target chain: {to_chain} for {token}")
    if from_chain == to_chain:
        raise ValueError("source and target chains must differ")
    if amount_usdc <= 0:
        raise ValueError("amount must be positive")

    from_token = _tmap[from_chain]
    to_token = _tmap[to_chain]
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
    # The token must be part of the cache key: without it a cached USDC route is
    # returned for a USDT request and the user signs a swap into the wrong token.
    cache_key = _cache_key("lifi", from_chain, to_chain, amount_usdc, from_address, to_address) + ":" + token
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
    data = _http_get_json(url)
    if "estimate" not in data:
        _partner_fail("lifi")
        raise ValueError(f"LI.FI response missing estimate: {data}")
    _partner_ok("lifi")

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

    # NOTE: must NOT use `return {...}` here — Python's compiler would
    # see the if/return below as unreachable and eliminate the cache
    # write. Audit C4 fix: assign first, cache, then return.
    result = {
        "tool": tool_info.get("name") or data.get("tool") or "unknown",
        "from_chain": from_chain,
        # Explicit, so a caller displaying "you will receive X" never assumes
        # USDC when the user actually asked for USDT.
        "token": token.strip().upper(),
        "from_token_address": from_token,
        "to_token_address": to_token,
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
        "partner": "lifi",
    }
    if use_cache:
        _cache_set(cache_key, result)
    return result


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


# ───────── Across Protocol (fallback partner) ─────────
# Across is a competing bridge aggregator with different liquidity
# routes. We use it as a fallback when LI.FI fails or returns a
# worse rate. API: https://app.across.to/api — no key required
# for the basic /suggested-fees endpoint.

ACROSS_BASE = "https://app.across.to/api/suggested-fees"

# ACROSS expects token addresses in their canonical format. For USDC
# we hardcode the canonical addresses per chain (same as LI.FI/USDC).

# In-memory quote cache to avoid hammering LI.FI/Across on every
# keystroke. Cache lives in this Python process; on a multi-worker
# deployment each worker has its own cache, which is fine.
_CACHE = {}            # key -> (timestamp, quote)
_CACHE_TTL_SEC = 30    # 30 seconds
_CACHE_MAX_ENTRIES = 500  # bounded LRU (audit C4)
_CACHE_LOCK = threading.Lock()


def _cache_gc_locked():
    """Drop expired keys and enforce max size. Caller must hold the lock."""
    now = time.time()
    expired = [k for k, (ts, _) in _CACHE.items() if now - ts > _CACHE_TTL_SEC]
    for k in expired:
        _CACHE.pop(k, None)
    # Hard cap to prevent unbounded growth from attacker-supplied
    # unique addresses (audit C4).
    if len(_CACHE) > _CACHE_MAX_ENTRIES:
        # Sort by timestamp ascending and drop oldest until under cap
        items = sorted(_CACHE.items(), key=lambda x: x[1][0])
        while len(_CACHE) > _CACHE_MAX_ENTRIES:
            _CACHE.pop(items.pop(0)[0], None)
_PARTNER_STATS = {     # rolling success rate
    "lifi":    {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0},
    "across":  {"ok": 0, "fail": 0, "last_ok": 0, "last_fail": 0},
}

def _partner_ok(partner: str):
    s = _PARTNER_STATS.get(partner)
    if s: s["ok"] += 1; s["last_ok"] = time.time()

def _partner_fail(partner: str):
    s = _PARTNER_STATS.get(partner)
    if s: s["fail"] += 1; s["last_fail"] = time.time()

def _cache_key(partner, from_chain, to_chain, amount_usdc, from_address, to_address):
    return f"{partner}:{from_chain}:{to_chain}:{amount_usdc}:{from_address.lower()}:{to_address.lower()}"

def _cache_get(key):
    with _CACHE_LOCK:
        v = _CACHE.get(key)
        if not v: return None
        ts, data = v
        if time.time() - ts > _CACHE_TTL_SEC:
            _CACHE.pop(key, None)
            return None
        # Touch to mark recently-used (LRU-friendly)
        _CACHE[key] = (ts, data)
        return data

def _cache_set(key, data):
    with _CACHE_LOCK:
        _cache_gc_locked()
        _CACHE[key] = (time.time(), data)


def quote_across(from_chain: int, to_chain: int, amount_usdc: float,
                 from_address: str, to_address: str,
                 token: str = "USDC") -> Optional[dict]:
    """Get an Across Protocol quote. Returns None if Across cannot
    route this pair (e.g. unsupported chain pair) or if the API is
    unavailable. Same dict shape as quote() so callers can pick the
    best one.

    Across moves USDC only. It must return None for any other token rather than
    quote its USDC route anyway: the caller merges partner results by output
    amount, so a USDC route offered in response to a USDT request would win on
    price and the user would sign a swap into the wrong token.
    """
    if (token or "USDC").strip().upper() != "USDC":
        return None
    if from_chain == to_chain:
        return None  # Across only does cross-chain
    if from_chain not in USDC_BY_CHAIN or to_chain not in USDC_BY_CHAIN:
        return None
    cache_key = _cache_key("across", from_chain, to_chain, amount_usdc, from_address, to_address)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    from_token = USDC_BY_CHAIN[from_chain]
    to_token = USDC_BY_CHAIN[to_chain]
    from_amount_int = int(amount_usdc * 1_000_000)
    params = {
        "inputToken": from_token,
        "outputToken": to_token,
        "originChainId": str(from_chain),
        "destinationChainId": str(to_chain),
        "amount": str(from_amount_int),
        "skipAmountLimit": "true",
    }
    url = f"{ACROSS_BASE}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "ubotpay-bridge/1.0",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:
        log.warning("Across quote failed: %s", e)
        _partner_fail("across")
        return None
    # Across returns: outputAmount (str, in token units), totalRelayFee.total (str, in token units)
    if "outputAmount" not in data or "totalRelayFee" not in data:
        log.warning("Across response missing fields: %s", data)
        _partner_fail("across")
        return None
    # USDC has 6 decimals
    output_amount = int(data["outputAmount"])
    fee_total = int(data["totalRelayFee"]["total"])
    fee_usd = fee_total / 1e6
    # Across doesn't include gas cost in USD. Estimate based on chain.
    gas_estimate_usd = {
        1: 3.0, 10: 0.5, 137: 0.05, 8453: 0.5, 42161: 0.5, 43114: 0.3,
    }.get(from_chain, 1.0)
    result = {
        "tool": "Across Protocol",
        "from_chain": from_chain,
        "to_chain": to_chain,
        "from_amount": from_amount_int,
        "from_amount_human": amount_usdc,
        "to_amount": output_amount,
        "to_amount_human": output_amount / 1e6,
        "to_amount_min": int(data.get("limits", {}).get("minDeposit", "0")),
        "to_amount_min_human": int(data.get("limits", {}).get("minDeposit", "0")) / 1e6,
        "gas_cost_usd": gas_estimate_usd,
        "bridge_fee_usd": round(fee_usd, 4),
        "total_fee_usd": round(fee_usd + gas_estimate_usd, 4),
        "execution_duration": int(data.get("estimatedFillTimeSec", 30)),
        "approval_address": data.get("spokePoolAddress"),
        "transaction_request": from_address,  # Across expects user to send via their contract
        "raw": data,
        "partner": "across",
    }
    _cache_set(cache_key, result)
    _partner_ok("across")
    log.info("Across quote OK: %s USDC %s → %s", amount_usdc, from_chain, to_chain)
    return result


def get_best_quote(from_chain: int, to_chain: int, amount_usdc: float,
                   from_address: str, to_address: str,
                   token: str = "USDC") -> dict:
    """Query all enabled bridge partners in parallel and return the
    best quote (highest to_amount, with ties broken by lowest fee and
    shortest time). If all partners fail, raise a ValueError.
    """
    if from_chain == to_chain:
        # Same-chain: just use LI.FI for the canonical fee estimate
        q = quote(from_chain, to_chain, amount_usdc, from_address, to_address,
                  True, token)
        return q

    # A chain that cannot be a source (Solana: the leg to sign would not be an
    # EVM transaction) is rejected before fanning out, and that reason is
    # preserved. Without this the per-partner `except` below would swallow it and
    # the caller would see "all bridge partners failed", i.e. a 502 suggesting a
    # transient outage rather than a request that can never work.
    if from_chain in _DESTINATION_ONLY_CHAINS:
        raise ValueError(
            f"chain {from_chain} can only be a destination, not a source: "
            "the outgoing leg would be a Solana transaction this app cannot sign")
    # Check both chains here too. Left to the per-partner `except` below, an
    # unknown chain id is caught twice, logged as "partner failed", and reported
    # as a transient upstream failure (502) — telling the caller to retry a
    # request that can never succeed.
    # Validate against the map for *this* token, not USDC's. Validating only the
    # chains is not enough now that USDT covers fewer of them: USDT -> Solana has
    # both chains present in USDC_BY_CHAIN but neither in USDT_BY_CHAIN, and it
    # would otherwise reach the fan-out and come back as a 502.
    _tmap = _token_map(token)
    for cid, label in ((from_chain, "source"), (to_chain, "target")):
        if cid not in _tmap:
            raise _UnsupportedChain(
                f"unsupported {label} chain: {cid} for {token.strip().upper()}")

    import concurrent.futures
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = {
            "lifi":   ex.submit(quote, from_chain, to_chain, amount_usdc,
                                from_address, to_address, True, token),
            "across": ex.submit(quote_across, from_chain, to_chain, amount_usdc,
                                from_address, to_address, token),
        }
        errors = []
        for name, fut in futures.items():
            try:
                q = fut.result(timeout=15)
                if q is not None:
                    results.append(q)
            except _UnsupportedChain as e:
                # This chain/token pair is simply not quotable. Every partner
                # will say the same, so record it and surface it as a 400 rather
                # than letting it fall through to the generic "partners failed"
                # 502, which tells the caller to retry something that cannot work.
                errors.append(str(e))
            except Exception as e:
                log.warning("Partner %s failed: %s", name, e)
    if not results:
        # If every partner refused for the same reason, that reason is the answer.
        # Across abstains silently on USDT (it only routes USDC), so at least one
        # _UnsupportedChain from lifi means the pair itself is not supported.
        if errors and len(errors) == len(futures):
            raise _UnsupportedChain(errors[0])
        # Distinguish "the upstream providers are unhappy" from "this request is
        # impossible" — the caller maps the first to a retryable 502.
        raise _BridgeUpstreamError("all bridge partners failed; try again in a moment")
    # Pick the best: highest to_amount, ties broken by lowest fee, then fastest
    def score(q):
        # We want max to_amount and min total_fee, min time
        # Normalize: score = to_amount (in human) - 0.001 * fee_usd - 0.0001 * time_sec
        return q["to_amount_human"] - 0.001 * q["total_fee_usd"] - 0.0001 * q["execution_duration"]
    best = max(results, key=score)
    log.info("Best quote: %s via %s (output %s, fee %s, time %s)",
             best["partner"], best["tool"], best["to_amount_human"], best["total_fee_usd"], best["execution_duration"])
    # Attach partner metadata so the frontend can show all quotes
    best["all_partner_quotes"] = [
        {
            "partner": q.get("partner", "lifi"),
            "tool": q["tool"],
            "to_amount_human": q["to_amount_human"],
            "total_fee_usd": q["total_fee_usd"],
            "execution_duration": q["execution_duration"],
        }
        for q in results
    ]
    return best


def partner_stats() -> dict:
    """Return the rolling success stats for each partner. Used by
    /bridge/status to show in the UI which partner the quote came
    from and how reliable it is."""
    out = {}
    for name, s in _PARTNER_STATS.items():
        total = s["ok"] + s["fail"]
        rate = (s["ok"] / total) if total else None
        out[name] = {
            "ok": s["ok"],
            "fail": s["fail"],
            "success_rate": rate,
            "last_ok": s["last_ok"],
            "last_fail": s["last_fail"],
            "is_healthy": rate is None or rate > 0.5,
        }
    return out


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
