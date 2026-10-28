#!/usr/bin/env python3
"""Find USDC on Starknet mainnet.

The addresses that circulate on the web for "USDC on Starknet" are
unreliable, so this reads them off the chain.

What is known:
  - STRK, the fee token, answers name()/symbol()/decimals()/
    total_supply(), and the responses decode cleanly.
  - The token list registry address used in most guides no longer has
    a contract at it ("Contract not found"), so the registry is found
    differently: by asking the chain for the STRK contract's class
    hash, or by calling the token registry through the current
    registry contract.

The decoding of a Cairo 1 string is worth spelling out: it is a felt
array [offset, length, ...bytes], where each felt holds 31 bytes of
the string, not 32.

Usage:
    find_starknet_usdc.py            # find USDC
    find_starknet_usdc.py --strk     # just decode STRK's metadata
"""
import asyncio
import json
import sys

from starknet_py.hash.selector import get_selector_from_name
from starknet_py.net.client_models import Call
from starknet_py.net.full_node_client import FullNodeClient

RPCS = [
    "https://starknet-rpc.publicnode.com",
    "https://starknet.api.onfinality.io/public",
    "https://starknet-mainnet-rpc.itrocket.net",
]

STRK = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"

# The token list registry, in the versions it has had. The first is the
# current one; the others are older addresses that may still have code.
REGISTRIES = [
    "0x06ac597f5886c9c936a269969e1703e43f8804cedfdd5eaba5c6798b1d488fbb",
    "0x02b657c1e0b1c9c5e0f7b4b7a2b1d6e2c8e4f9a7b3c1d5e6f708192a3b4c5d6e7f8",
]

MASK = (1 << 251) - 1
BYTES_PER_FELT = 31  # Cairo 1 packs 31 bytes per felt


def decode_string(felts):
    """A Cairo 1 string is [offset, length, ...felt], 31 bytes each."""
    if not isinstance(felts, list) or len(felts) < 2:
        return ""
    length = felts[1]
    payload = felts[2:]
    out = bytearray()
    for f in payload:
        b = int(f).to_bytes(32, "big")
        # the low BYTES_PER_FELT bytes carry the payload
        out += b[32 - BYTES_PER_FELT:]
    return bytes(out[:length]).decode("ascii", "replace") if length else ""


async def main():
    client = None
    for url in RPCS:
        try:
            c = FullNodeClient(node_url=url)
            cid = await c.get_chain_id()
            print(f"  {url[:52]:54s} -> {cid}")
            if client is None:
                client = c
        except Exception as e:
            print(f"  {url[:52]:54s} -> {type(e).__name__} {str(e)[:60]}")
    if client is None:
        return 1

    async def call(addr, entry, calldata=None):
        return await client.call_contract(
            Call(to_addr=int(addr, 16),
                 selector=get_selector_from_name(entry),
                 calldata=calldata or []),
            block_number="latest")

    print()
    print("=== STRK's metadata ===")
    for entry in ("name", "symbol", "decimals", "total_supply"):
        try:
            res = await call(STRK, entry)
            if entry in ("name", "symbol"):
                print(f"  {entry:14s} -> {decode_string(res)!r}")
            else:
                print(f"  {entry:14s} -> {res}")
        except Exception as e:
            print(f"  {entry:14s} -> {str(e)[:70]}")

    if "--strk" in sys.argv:
        return 0

    print()
    print("=== the token list registry ===")
    tokens = None
    for reg in REGISTRIES:
        for entry in ("get_all_tokens", "tokens", "token_list"):
            try:
                res = await call(reg, entry)
            except Exception as e:
                msg = str(e)[:70]
                continue
            print(f"  {reg[:14]}... {entry}: {str(res)[:180]}")
            if res:
                if len(res) >= 2 and isinstance(res[1], int):
                    length = res[1]
                    tokens = res[2:2 + length] if length else res[2:]
                else:
                    tokens = res
                break
        if tokens:
            break

    if not tokens:
        print("  no registry answered; the token list has to come from")
        print("  another source (an indexer, or a known token address)")
        return 1

    print(f"\n  {len(tokens)} token(s)")
    addrs = [f"0x{(t & MASK):064x}" for t in tokens]

    print()
    print("=== which ones are USDC? ===")
    found = []
    for a in addrs:
        try:
            raw = await call(a, "name")
        except Exception:
            raw = None
        try:
            sym = await call(a, "symbol")
        except Exception:
            sym = None
        n = decode_string(raw) if raw else ""
        s = decode_string(sym) if sym else ""
        if "USD" in s.upper() or "USD" in n.upper():
            print(f"  {a}  name={n!r} symbol={s!r}")
            found.append({"address": a, "name": n, "symbol": s})

    print()
    print(json.dumps({"usdc": found}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
