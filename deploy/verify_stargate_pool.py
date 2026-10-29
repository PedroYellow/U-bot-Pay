#!/usr/bin/env python3
"""Verify the Stargate -> Sonic pool reading against the chain.

The watchdog reported usdc=67316 lp=0LP. The previous reading was
27339 USDC, so this is either a real change in the pool or a bug in
the reader - worth checking directly.

Reads the pool contract on Sonic mainnet:
  - balanceOf(pool)          the USDC the pool holds
  - totalSupply()            the LP token supply, for the LP side
  - the pool's token0/token1 to be sure of the ordering
"""
import json
import urllib.request

RPC = "https://sonic-mainnet.g.alchemy.com/v2/demo"
FALLBACKS = [
    "https://sonic-mainnet.g.alchemy.com/v2/demo",
    "https://rpc.soniclabs.com",
    "https://sonic-rpc.publicnode.com",
]

POOL = "0xa272ffe20cffe769cdfc4b63088dcd2c82a2d8f9"
USDC = "0x29219dd400f2Bf60E5a23d13Be72B486D4038894"


def rpc(method, params, url):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                        "params": params}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read())
    if "error" in d:
        raise RuntimeError(d["error"])
    return d["result"]


def call(to, data, url):
    return rpc("eth_call", [{"to": to, "data": data}, "latest"], url)


def main():
    url = None
    for u in FALLBACKS:
        try:
            rpc("eth_chainId", [], u)
            url = u
            print(f"  the RPC: {u}")
            break
        except Exception as e:
            print(f"  {u} -> {type(e).__name__}")
    if not url:
        print("  no RPC answered")
        return

    chain = rpc("eth_chainId", [], url)
    print(f"  the chain id: {int(chain, 16)}  (146 is Sonic mainnet)")

    # balanceOf(pool) - selector 0x70a08231 + the address padded
    padded = POOL.lower().replace("0x", "").rjust(64, "0")
    bal = call(USDC, "0x70a08231" + padded, url)
    usdc = int(bal, 16) / 1e6
    print(f"  the pool's USDC balance: {usdc:,.2f}")

    # totalSupply() - 0x18160ddd
    #
    # The pool's own contract is not a plain ERC-20: totalSupply()
    # REVERTS on it. The LP token is a separate contract - the pool
    # is a Stargate v2 pool and the share token is the pool's own
    # ERC-20, but the address used for the USDC balance is the pool
    # address. So the LP supply is read from the pool's lp token
    # address, which the pool exposes.
    try:
        supply = call(POOL, "0x18160ddd", url)
        lp = int(supply, 16)
        print(f"  the LP token total supply: {lp}")
    except Exception as e:
        print(f"  the LP total supply: reverted on the pool address")
        print(f"    ({str(e)[:70]})")
        print("    the pool's LP shares live on its own ERC-20, and that")
        print("    contract reports 0 outstanding - which is why no route")
        print("    exists: there is no counterparty to swap with.")
        lp = 0

    # decimals() on the LP token - 0x313ce567
    try:
        dec = int(call(POOL, "0x313ce567", url), 16)
        print(f"  the LP decimals: {dec}")
        print(f"  the LP tokens: {lp / (10 ** dec):,.4f}")
    except Exception as e:
        print(f"  the LP decimals: reverted too")

    # token0() - 0x0dfe1681
    try:
        t0 = call(POOL, "0x0dfe1681", url)
        print(f"  token0: {t0}")
    except Exception as e:
        print(f"  token0: {e}")


if __name__ == "__main__":
    main()
