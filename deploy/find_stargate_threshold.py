#!/usr/bin/env python3
"""Find where the Stargate route from Avalanche actually stops.

The tracker probed 10, 100, 1,000, 10,000 and 100,000 USDC. The
first size that failed was 1,000, so the route looked like it "stopped
at $100" - and that went into the outreach email.

But the real threshold is between $750 and $1,000:

    $100  -> QUOTE, out 99.58
    $200  -> QUOTE, out 199.16
    $300  -> QUOTE, out 298.74
    $500  -> QUOTE, out 497.89
    $750  -> QUOTE, out 746.84
    $1,000 -> no quote

So the route works to $750, not to $100. "Stops at $100" is wrong by
7x, and it is the number the email leads with - the person reading it
would check with one API call and find the email overstated the
problem.

The cause is the same one that made the whole "no route at any size"
claim false: the sizes were too coarse. A binary search between the
last size that worked and the first that failed finds the real
threshold, and it is cheap - about ten extra quotes.

This reports the threshold, and the quote at it.

Usage: find_stargate_threshold.py [from-chain] [from-token]
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

LIFI_QUOTE = "https://li.quest/v1/quote"
BRIDGE = "stargateV2"
SONIC_USDC = "0x29219dd400f2Bf60E5a23d13Be72B486D4038894"
AVAX_USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
SAMPLE = "0x713261F2151a759d9D935e6De2DB1d63A98176f6"


def quote(chain, token, amount):
    p = {"fromChain": chain, "toChain": 146,
         "fromToken": token, "toToken": SONIC_USDC,
         "fromAmount": str(int(amount * 1e6)), "fromAddress": SAMPLE,
         "allowBridges": BRIDGE}
    url = f"{LIFI_QUOTE}?{urllib.parse.urlencode(p)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "ubotpay-stargate-tracker/1.0",
        "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            d = json.load(r)
            est = d.get("estimate", {})
            to = est.get("toAmount")
            return True, (int(to) / 1e6 if to else None)
    except urllib.error.HTTPError:
        return False, None
    except Exception:
        return False, None


def find_threshold(chain, token):
    """The largest amount that routes, and the smallest that does not."""
    lo, hi = 1.0, 10000.0
    # the largest amount that routes
    ok, _ = quote(chain, token, lo)
    if not ok:
        return None, None
    while hi - lo > 1.0:
        mid = (lo + hi) / 2
        ok, _ = quote(chain, token, mid)
        if ok:
            lo = mid
        else:
            hi = mid
    good, out = quote(chain, token, lo)
    return lo, out


def main():
    chain = int(sys.argv[1]) if len(sys.argv) > 1 else 43114
    token = sys.argv[2] if len(sys.argv) > 2 else AVAX_USDC
    name = "Avalanche" if chain == 43114 else f"chain {chain}"

    print(f"  {name} -> Sonic, stargateV2 only")
    print("  the coarse sizes:")
    for a in (10, 100, 500, 750, 1000, 5000, 10000):
        ok, out = quote(chain, token, a)
        print(f"    ${a:>7,} -> {'QUOTE, out %.2f' % out if ok else 'no quote'}")

    print()
    print("  the binary search:")
    threshold, out = find_threshold(chain, token)
    if threshold is None:
        print("    nothing routes, even $1")
    else:
        print(f"    the largest amount that routes: ${threshold:,.0f}")
        print(f"    it returns {out:,.2f} USDC")
        nxt = threshold * 1.25
        ok, _ = quote(chain, token, nxt)
        print(f"    at ${nxt:,.0f}: {'still routes' if ok else 'no quote'}")


if __name__ == "__main__":
    main()
