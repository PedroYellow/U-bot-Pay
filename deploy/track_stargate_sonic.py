#!/usr/bin/env python3
"""Track whether Stargate can actually route into Sonic.

Why this exists
---------------
Stargate's Sonic deployment was ratified by the DAO with USDC and USDT
pools promised on day one, and 1m + 1m of liquidity requested. The pools
shipped. The liquidity did not.

As of 2026-10-29 the USDC pool holds 67,315 USDC - up from 27,874 on
2026-10-27 and 27,339 on 2026-10-28, so the liquidity is being
supplied rather than drained - and LI.FI still returns "No available
quotes for the requested transfer" when Stargate is the only allowed
bridge, at every size from 10 to 100,000 USDC. The pool grew by two
and a half times and still cannot route, because the problem is the
missing pool shares and not the USDC: the pool's LP tokens report
zero outstanding, so there is no counterparty to swap with.

Meanwhile the Avalanche -> Sonic route works, through Polymer, and that
is the route our own product uses.

This script measures that, so the claim in the pitch to Stargate is a
measurement rather than an assertion, and so we notice the moment it
changes.

What it checks
--------------
  1. USDC held by the Stargate V2 USDC pool on Sonic, read from the
     contract
  2. whether LI.FI can route a quote through Stargate alone, at several
     sizes
  3. the working route for comparison, and what it costs

Usage:
    python3 track_stargate_sonic.py          # one report
    python3 track_stargate_sonic.py --json   # machine-readable

The pool address is read from the chain, not hardcoded from a website:
the Stargate V2 USDC pool on Sonic is
0xa272ffe20cffe769cdfc4b63088dcd2c82a2d8f9 and the USDC token is
0x29219dd400f2Bf60E5a23d13Be72B486D4038894 (Circle's own registry
lists this as the Sonic mainnet USDC).
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

SONIC_RPC = "https://rpc.soniclabs.com"
SONIC_CHAIN_ID = 146
STARGATE_USDC_POOL = "0xa272ffe20cffe769cdfc4b63088dcd2c82a2d8f9"
SONIC_USDC = "0x29219dd400f2Bf60E5a23d13Be72B486D4038894"
AVAX_CHAIN_ID = 43114
AVAX_USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
SAMPLE_ADDRESS = "0x713261F2151a759d9D935e6De2DB1d63A98176f6"
LIFI_QUOTE = "https://li.quest/v1/quote"
LIFI_TOOLS = "https://li.quest/v1/tools"
# The sizes to probe.
#
# The coarse set - 10, 100, 1,000, 10,000, 100,000 - is not enough to
# see WHERE the route stops. With it, the first size that fails is
# 1,000, so the threshold looks like $100, and that went into the
# outreach email as the headline claim.
#
# The real threshold from Avalanche is $882. The finer sizes below
# bracket it: 500 and 750 route, 1,000 does not. So the tracker can
# say "somewhere between 750 and 1,000" rather than guessing.
#
# The threshold itself is found exactly by deploy/find_stargate_threshold.py,
# which binary searches between the last size that worked and the
# first that did not.
SIZES_USDC = [10, 100, 500, 750, 1_000, 5_000, 10_000, 100_000]

# LI.FI's bridge key for Stargate V2.
#
# It used to be "stargate". It is now "stargateV2", and asking for
# "stargate" is REJECTED by the API as not being one of the allowed
# values - so a tracker still using the old name measures a bridge
# that does not exist and reports "not routable" for a reason that has
# nothing to do with the pool.
#
# So the name is READ from LI.FI's own tool list rather than
# hardcoded. If LI.FI renames it again, the tracker follows.
def _resolve_stargate_bridge():
    try:
        req = urllib.request.Request(
            LIFI_TOOLS,
            headers={"User-Agent": "ubotpay-stargate-tracker/1.0",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as r:
            tools = json.load(r).get("tools", [])
        keys = [str(t.get("key") or "") for t in tools]
        for candidate in ("stargateV2", "stargateV2Bus", "stargate"):
            if candidate in keys:
                return candidate
    except Exception:
        pass
    return "stargateV2"


STARGATE_BRIDGE = _resolve_stargate_bridge()


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "method": method,
                        "params": params, "id": 1}).encode()
    req = urllib.request.Request(
        SONIC_RPC, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    if d.get("error"):
        raise RuntimeError(d["error"])
    return d.get("result")


def call(to, data):
    return rpc("eth_call", [{"to": to, "data": data}, "latest"])


def to_int(hexstr):
    """Parse a quantity that may come back as a hex string or a number.

    eth_call results are hex strings; the LI.FI JSON API returns plain
    integers for amounts. Both appear in this file, so accept both.
    """
    if hexstr is None:
        return None
    if isinstance(hexstr, int):
        return hexstr
    if isinstance(hexstr, str):
        s = hexstr.strip()
        if not s:
            return None
        if s.startswith("0x") or s.startswith("0X"):
            try:
                return int(s, 16)
            except ValueError:
                return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


def usdc(amount_raw):
    """A USDC amount in 6 decimals, from either representation."""
    n = to_int(amount_raw)
    return None if n is None else n / 10**6


def pool_usdc():
    """USDC held by the Stargate V2 pool, read from the token contract."""
    data = "0x70a08231" + "0" * 24 + STARGATE_USDC_POOL[2:]
    return usdc(call(SONIC_USDC, data))


def lifi_quote(amount_usdc, allow_bridges=None):
    params = {
        "fromChain": AVAX_CHAIN_ID,
        "toChain": SONIC_CHAIN_ID,
        "fromToken": AVAX_USDC,
        "toToken": SONIC_USDC,
        "fromAmount": str(int(amount_usdc * 1e6)),
        "fromAddress": SAMPLE_ADDRESS,
    }
    if allow_bridges:
        params["allowBridges"] = allow_bridges
    url = f"{LIFI_QUOTE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "ubotpay-stargate-tracker/1.0",
                      "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)


def probe():
    out = {
        "sonic_chain_id": None,
        "pool_usdc": None,
        "stargate_routable": {},
        "stargate_first_routable_usdc": None,
        "working_route": None,
    }
    try:
        out["sonic_chain_id"] = to_int(rpc("eth_chainId", []))
    except Exception as e:
        out["error"] = f"chain id: {type(e).__name__}: {e}"
        return out

    try:
        out["pool_usdc"] = pool_usdc()
    except Exception as e:
        out["error"] = f"pool: {type(e).__name__}: {e}"

    for amt in SIZES_USDC:
        try:
            # LI.FI renamed the bridge. The key used to be "stargate";
            # it is now "stargateV2" - and asking for "stargate" is
            # REJECTED by the API as not being one of the allowed
            # values. So the tracker was measuring a bridge name that
            # does not exist, and reporting "not routable" for a reason
            # that had nothing to do with the pool.
            #
            # The name comes from LI.FI's own tool list, so it is read
            # from there rather than hardcoded:
            #   https://li.quest/v1/tools -> the key is "stargateV2"
            q = lifi_quote(amt, allow_bridges=STARGATE_BRIDGE)
            est = q.get("estimate", {})
            out["stargate_routable"][str(amt)] = {
                "ok": True,
                "tool": q.get("tool"),
                "out_usdc": usdc(est.get("toAmount")),
            }
            if out["stargate_first_routable_usdc"] is None:
                out["stargate_first_routable_usdc"] = amt
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read()[:160].decode("utf-8", "replace")
            except Exception:
                pass
            out["stargate_routable"][str(amt)] = {"ok": False, "http": e.code,
                                                  "detail": body}
        except Exception as e:
            out["stargate_routable"][str(amt)] = {"ok": False,
                                                  "error": f"{type(e).__name__}: {e}"}

    try:
        q = lifi_quote(100)
        est = q.get("estimate", {})
        out["working_route"] = {
            "tool": q.get("tool"),
            "out_usdc": usdc(est.get("toAmount")),
            "fee_costs": est.get("feeCosts"),
        }
    except Exception as e:
        out["working_route"] = {"error": f"{type(e).__name__}: {e}"}

    out["routable"] = out["stargate_first_routable_usdc"] is not None
    return out


def main():
    data = probe()
    if "--json" in sys.argv:
        print(json.dumps(data, indent=2))
        return

    print("═" * 64)
    print("  Stargate → Sonic  ·  routability report")
    print("═" * 64)
    print(f"  Sonic chain id        : {data.get('sonic_chain_id')}")
    pool = data.get("pool_usdc")
    print(f"  Stargate USDC pool    : "
          f"{pool:,.2f} USDC" if pool is not None else "  (unreadable)")
    print(f"  pool address          : {STARGATE_USDC_POOL}")
    print()
    print("  Can LI.FI route through Stargate alone?")
    for amt in SIZES_USDC:
        r = data["stargate_routable"].get(str(amt), {})
        if r.get("ok"):
            print(f"    {amt:>9,} USDC  →  YES  via {r.get('tool')}  "
                  f"(out {r.get('out_usdc'):,.2f})")
        else:
            detail = r.get("detail") or r.get("error") or ""
            print(f"    {amt:>9,} USDC  →  no   {str(detail)[:58]}")
    print()
    wr = data.get("working_route") or {}
    if "tool" in wr:
        print(f"  Working route (any bridge): {wr['tool']}  "
              f"100 → {wr.get('out_usdc')} USDC")
    print()
    if data.get("routable"):
        print(f"  ✅  STARGAYTE IS ROUTABLE from "
              f"{data['stargate_first_routable_usdc']} USDC up.")
        print("      Point the bridge at it and tell Stargate the date.")
    else:
        print("  ❌  Stargate cannot route into Sonic at any tested size.")
        print("      The pool is deployed but not funded. This is the lever.")
    if data.get("error"):
        print(f"\n  ⚠  {data['error']}")
    print("═" * 64)


if __name__ == "__main__":
    main()
