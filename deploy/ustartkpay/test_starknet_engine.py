#!/usr/bin/env python3
"""Test the Starknet engine against the real chain.

This is not "does it parse". It reads a live token's metadata and a
live balance from Starknet mainnet, through the same engine the
deployment will use.

Usage: test_starknet_engine.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chains_starknet_engine import (  # noqa: E402
    StarknetEngine, STRK, USDC, decode_string,
)

RPC = "https://starknet-rpc.publicnode.com"

# A known holder. The Starknet fee token's own address is not a holder,
# so this is the USDC contract itself, which holds a little of its own
# token in the constructor. The balance is read from the chain either
# way - the point is that the call works and the number comes back.
READS = [
    "https://usonicpay.com/",
    "https://ubotpay.com/",
]


async def main():
    eng = StarknetEngine(
        rpc=RPC,
        stable_address=USDC,
        explorer="https://starkscan.co",
        native_symbol="STRK",
        account_address="",
    )

    print("=== the token ===")
    name = await eng.token_name()
    symbol = await eng.token_symbol()
    decimals = await eng.decimals()
    print(f"  name     {name!r}")
    print(f"  symbol   {symbol!r}")
    print(f"  decimals {decimals}")

    ok = True
    if name != "USD Coin":
        print("  !! expected 'USD Coin'"); ok = False
    if symbol != "USDC":
        print("  !! expected 'USDC'"); ok = False
    if decimals != 6:
        print("  !! expected 6 decimals"); ok = False

    print()
    print("=== total supply ===")
    supply = await eng.total_supply()
    print(f"  {supply:,.2f} USDC")
    if supply <= 0:
        print("  !! a live token has a supply"); ok = False

    print()
    print("=== a balance read ===")
    for addr in [USDC, STRK]:
        bal = await eng.balance(addr)
        print(f"  {addr[:14]}... -> {bal:,.6f}")

    print()
    print("=== the account ===")
    print(f"  has_signer: {eng.has_signer()}")
    print(f"  address:    {eng._treasury_address or '(none)'}")
    if eng.has_signer():
        print("  !! this deployment should have no account yet"); ok = False

    print()
    print("=== the decoder against a known-good string ===")
    # STRK's symbol, from the legacy felt-array encoding
    from starknet_py.hash.selector import get_selector_from_name
    from starknet_py.net.client_models import Call
    raw = await eng.client.call_contract(
        Call(to_addr=int(STRK, 16),
             selector=get_selector_from_name("symbol"),
             calldata=[]),
        block_number="latest")
    sym = decode_string(raw)
    print(f"  STRK symbol -> {sym!r}")
    if sym != "STRK":
        print("  !! expected 'STRK'"); ok = False

    print()
    print("VERDICT:", "the Starknet engine reads the real chain" if ok
          else "NOT correct")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
