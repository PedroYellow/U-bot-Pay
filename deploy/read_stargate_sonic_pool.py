#!/usr/bin/env python3
"""Read the Stargate V2 USDC pool on Sonic, and what is around it.

The pool address comes from Stargate's own API, not from a guide:

    GET https://mainnet.stargate-api.com/v1/metadata?version=v2

which lists for chainKey "sonic":
    pool        0xA272fFe20cFfe769CdFc4b63088DCD2C82a2D8F9
    token       0x29219dd400f2Bf60E5a23d13Be72B486D4038894  (USDC, 6)
    lpToken     0x4b459Cd35839579C27Cbf0CEF3af1E47024284e7  (S*USDC, 6)
    staking     0xbbA60da06c2c5424f03f7434542280FCAd453d10
    messaging   0x2086f755a6d9254045c257ea3d382ef854849b0f

Reading the pool tells us whether the liquidity is there but the route
is broken, or the liquidity is gone. Those are different problems and
they need different answers from Stargate.

Usage: read_stargate_sonic_pool.py
"""
import json
import sys
import urllib.request

RPC = "https://rpc.soniclabs.com"

POOL = "0xA272fFe20cFfe769CdFc4b63088DCD2C82a2D8F9"
USDC = "0x29219dd400f2Bf60E5a23d13Be72B486D4038894"
LP = "0x4b459Cd35839579C27Cbf0CEF3af1E47024284e7"

# The Stargate pool's own ABI-ish calls, by selector.
#   totalSupply()                 0x18160ddd
#   balanceOf(address)            0x70a08231
#   token()                       0xfc0c546a
#   lpToken()                     0x6b1f8f4b  (StargatePool.lpToken)
#   sharedDecimals()              not a view - skip
SELECTORS = {
    "totalSupply": "0x18160ddd",
    "balanceOf": "0x70a08231",
    "token": "0xfc0c546a",
    "lpToken": "0x6b1f8f4b",
}


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "method": method,
                       "params": params, "id": 1}).encode()
    req = urllib.request.Request(
        RPC, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    if d.get("error"):
        raise RuntimeError(d["error"])
    return d.get("result")


def call(to, data):
    return rpc("eth_call", [{"to": to, "data": data}, "latest"])


def as_int(hexstr):
    if not hexstr or hexstr == "0x":
        return 0
    return int(hexstr, 16)


def token_balance(token, owner):
    data = SELECTORS["balanceOf"] + "0" * 24 + owner[2:]
    return as_int(call(token, data)) / 1e6


def native_balance(addr):
    return as_int(rpc("eth_getBalance", [addr, "latest"])) / 1e18


def addr_from_call(hexstr):
    if not hexstr or hexstr == "0x":
        return ""
    return "0x" + hexstr[-40:]


def main():
    print("=== the Stargate V2 USDC pool on Sonic ===")
    print(f"  pool     {POOL}")
    print(f"  USDC     {USDC}")
    print(f"  LP       {LP}")
    print()

    usdc = token_balance(USDC, POOL)
    lp_outstanding = token_balance(LP, POOL)
    s = native_balance(POOL)

    print("=== what the pool holds ===")
    print(f"  USDC          {usdc:>14,.2f}")
    print(f"  S (gas)       {s:>14,.2f}")
    print(f"  LP outstanding{'':1}{lp_outstanding:>14,.2f}")
    print()

    # The pool's own view of its token, read from the contract.
    try:
        t = addr_from_call(call(POOL, SELECTORS["token"]))
        print(f"  pool.token()   {t}")
        print(f"  matches USDC:  {t.lower() == USDC.lower()}")
    except Exception as e:
        print(f"  pool.token()   ERROR {str(e)[:70]}")
    try:
        l = addr_from_call(call(POOL, SELECTORS["lpToken"]))
        print(f"  pool.lpToken() {l}")
        print(f"  matches LP:    {l.lower() == LP.lower()}")
    except Exception as e:
        print(f"  pool.lpToken() ERROR {str(e)[:70]}")

    print()
    print("=== the price of the LP token ===")
    if usdc > 0 and lp_outstanding > 0:
        print(f"  {usdc / lp_outstanding:.6f} USDC per S*USDC")
    else:
        print("  (the pool holds no USDC, so there is no price)")

    print()
    print("VERDICT:", "the pool has liquidity" if usdc > 1000
          else "the pool is effectively empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
