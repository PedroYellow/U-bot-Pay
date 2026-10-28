#!/usr/bin/env python3
"""Test the Starknet engine against the real chain, and against the interface.

This is not "does it parse". It reads a live token's metadata and a
live balance from Starknet mainnet, through the same engine the
deployment will use.

And it checks the INTERFACE, because a Starknet engine that reads the
chain but does not implement the interface is something the app cannot
run - which is exactly what ustartkpay was serving: api.ustartkpay.com
proxied every route to the AVALANCHE deployment.

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

RESULTS = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-56s %s" % ("PASS" if cond else "FAIL", name, detail))


async def main():
    eng = StarknetEngine(
        rpc=RPC,
        stable_address=USDC,
        explorer="https://starkscan.co",
        native_symbol="STRK",
        account_address="",
    )

    print("=== the token, from the live chain ===")
    name = await eng.token_name()
    symbol = await eng.token_symbol()
    decimals = await eng._decimals_async()
    print(f"  name     {name!r}")
    print(f"  symbol   {symbol!r}")
    print(f"  decimals {decimals}")

    chk("the name is 'USD Coin'", name == "USD Coin", name)
    chk("the symbol is 'USDC'", symbol == "USDC", symbol)
    chk("it has 6 decimals", decimals == 6, decimals)

    print()
    print("=== the total supply ===")
    supply = await eng._total_supply_async()
    print(f"  {supply:,.2f} USDC")
    chk("a live token has a supply", supply > 0, f"{supply:,.2f}")

    print()
    print("=== the interface ===")
    for m in ("decimals", "to_units", "from_units", "is_address",
              "is_tx_hash", "balance", "submit", "confirm"):
        chk(f"the engine has {m}()", callable(getattr(eng, m, None)))

    print()
    print("=== the sync surface ===")
    try:
        d = eng.decimals()
        chk("decimals() reads the contract", d == 6, d)
    except Exception as e:
        chk("decimals() reads the contract", False,
            f"{type(e).__name__}: {str(e)[:70]}")

    try:
        units = eng.to_units(1.0)
        back = float(eng.from_units(units))
        chk("to_units()/from_units() round-trip", abs(back - 1.0) < 1e-9,
            f"{units} -> {back}")
    except Exception as e:
        chk("to_units()/from_units() round-trip", False,
            f"{type(e).__name__}: {str(e)[:70]}")

    print()
    print("=== the address shapes ===")
    STARKNET_ADDR = ("0x4af96d3945b91f77f2f36abfd45291ec5e0d7d753709ee2a5484a34f05ce259")
    EVM_ADDR = "0xCC57d2f8A941B3408f939E6fe949fb7C2b1F01Af"

    chk("a Starknet address is accepted", eng.is_address(STARKNET_ADDR))
    chk("an EVM address is refused", not eng.is_address(EVM_ADDR),
        "the field is 251 bits, not 256")
    chk("a 64-digit address is refused", not eng.is_address("0x" + "11" * 32))
    # A Starknet transaction hash is at most 63 hex digits - the field
    # is 251 bits. The test's first version used 64, and the engine
    # was right to refuse it.
    chk("a Starknet tx hash is accepted",
        eng.is_tx_hash("0x" + "ab" * 31 + "c"),
        "63 hex digits")
    chk("a 64-digit hash is refused",
        not eng.is_tx_hash("0x" + "ab" * 32),
        "the field is 251 bits, not 256")
    chk("nonsense is refused", not eng.is_tx_hash("not a hash"))

    print()
    print("=== a balance, from the live chain ===")
    try:
        b = eng.balance(STARKNET_ADDR)
        for k in ("usdc", "stable_balance", "native", "native_balance",
                  "chain", "chain_id", "decimals", "symbol"):
            chk(f"balance() has {k}", k in b, b.get(k))
        chk("the balance is a number",
            isinstance(b.get("usdc"), (int, float))
            and not isinstance(b.get("usdc"), bool), b.get("usdc"))
    except Exception as e:
        chk("balance() reads the chain", False,
            f"{type(e).__name__}: {str(e)[:80]}")

    print()
    print("=== the money movement refuses without a signer ===")
    try:
        r = eng.submit(STARKNET_ADDR, 0.1)
        chk("submit() without a signer refuses", not r.get("ok"), r.get("error"))
    except Exception as e:
        chk("submit() without a signer refuses", False,
            f"{type(e).__name__}: {str(e)[:70]}")

    print()
    print("=== the decoder against a known-good string ===")
    from starknet_py.hash.selector import get_selector_from_name
    from starknet_py.net.client_models import Call
    raw = await eng.client.call_contract(
        Call(to_addr=int(STRK, 16),
             selector=get_selector_from_name("symbol"),
             calldata=[]),
        block_number="latest")
    sym = decode_string(raw)
    print(f"  STRK symbol -> {sym!r}")
    chk("the decoder reads STRK", sym == "STRK", sym)

    print()
    ok = all(RESULTS)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    if ok:
        print("VERDICT: the Starknet engine satisfies the interface and reads "
              "the real chain")
    else:
        print("SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
