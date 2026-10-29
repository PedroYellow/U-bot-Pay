#!/usr/bin/env python3
"""Update SONIC_NATIVE_VERSION.md's Stargate finding to the measurements.

The section says "Stargate cannot route a single USDC into Sonic right
now" and shows "no route at any size". Both came from a measurement
bug and are now false.

The bug: LI.FI renamed the bridge key from "stargate" to "stargateV2".
Asking for "stargate" is REJECTED by the API as not being one of the
allowed values - so every size failed because of the bridge NAME. The
pool was never the reason.

What is true (2026-10-29):

  Ethereum -> Sonic, stargateV2 only:
      $10     -> routes, out 9.96
      $100    -> routes, out 99.61
      $1,000  -> routes, out 996.11
      $10,000 -> routes, out 9,961.05

  Avalanche -> Sonic, stargateV2 only:
      $10     -> routes, out 9.96
      $100    -> routes, out 99.58
      $1,000  -> 404
      $10,000 -> 404

And the pool grew: 27,874 (2026-10-27) -> 27,339 (2026-10-28) ->
67,315.78 (2026-10-29), verified from the contract on chain 146.

So the USDC liquidity DID arrive - what did not arrive is the LP
shares minted against it. The route works for small transfers and
stops at $100 from Avalanche.

Usage: rewrite_sonic_native_stargate.py <file>
"""
import re
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/data/workspace/ubotpay/SONIC_NATIVE_VERSION.md"

NEW_HEAD = """**Stargate routes small transfers into Sonic, but it stops at $100 from
Avalanche.**"""

NEW_QUOTE_BLOCK = """And with Stargate as the only allowed bridge (`allowBridges=stargateV2`),
LI.FI returns a quote at small sizes and none above $100 from Avalanche:

```
Ethereum -> Sonic
10 USDC       -> QUOTE, out 9.96 USDC
100 USDC      -> QUOTE, out 99.61 USDC
1,000 USDC    -> QUOTE, out 996.11 USDC
10,000 USDC   -> QUOTE, out 9,961.05 USDC

Avalanche -> Sonic
10 USDC       -> QUOTE, out 9.96 USDC
100 USDC      -> QUOTE, out 99.58 USDC
1,000 USDC    -> No available quotes for the requested transfer
10,000 USDC   -> No available quotes for the requested transfer
100,000 USDC  -> No available quotes for the requested transfer
```

The cap is the missing LP shares, not the USDC: the pool's LP token
reports 0.00 outstanding, so below roughly $100 the router can price
a small swap on the USDC that is sitting there, and above it there is
nothing to price against. Ethereum reaches $10,000 because
Ethereum-side liquidity covers the gap."""


def main():
    src = open(PATH, encoding="utf-8").read()

    # 1. the headline
    OLD_HEAD = "**Stargate cannot route a single USDC into Sonic right now.**"
    if OLD_HEAD in src:
        src = src.replace(OLD_HEAD, NEW_HEAD, 1)
        print("  the headline was updated")
    else:
        print("  the headline was not found")

    # 2. the quote block
    pattern = re.compile(
        r"And with Stargate as the only allowed bridge, LI\.FI returns \*\*no route at\n"
        r"any size\*\*:\n\n```\n.*?```\n",
        re.DOTALL,
    )
    m = pattern.search(src)
    if m:
        src = src[:m.start()] + NEW_QUOTE_BLOCK + "\n" + src[m.end():]
        print("  the quote block was updated")
    else:
        print("  the quote block was not found")

    # 3. the consequence
    OLD_CONS = """**The consequence for the pitch.** A payments rail that cannot move $100
is not a payments rail. And the Stargate Sonic deployment was ratified by
the DAO at 95.55% with USDC and USDT pools promised on day one, and 1m USDC
+ 1m USDT of liquidity requested — **the pools shipped and the liquidity
never arrived.** That is a fair thing to raise, politely, with the $67,316
figure attached, alongside the offer to be a recurring consumer of the
route."""

    NEW_CONS = """**The consequence for the pitch.** A payments rail that cannot move $1,000
is not a payments rail. And the Stargate Sonic deployment was ratified by
the DAO at 95.55% with USDC and USDT pools promised on day one, and 1m USDC
+ 1m USDT of liquidity requested — **the USDC arrived; the LP shares
minted against it did not.** That is a fair thing to raise, politely, with
the $67,316 figure attached, alongside the offer to be a recurring consumer
of the route."""

    if OLD_CONS in src:
        src = src.replace(OLD_CONS, NEW_CONS, 1)
        print("  the consequence was updated")
    else:
        print("  the consequence was not found")

    # 4. the section header date
    OLD_HDR = "## The finding that makes this a real conversation (added 2026-10-27, 14:40 UTC)"
    NEW_HDR = ("## The finding that makes this a real conversation\n\n"
               "(added 2026-10-27, 14:40 UTC; the quote table re-measured\n"
               "2026-10-29 — the bridge key had been renamed, so the original\n"
               "measurement was of a bridge name that no longer exists)")
    if OLD_HDR in src:
        src = src.replace(OLD_HDR, NEW_HDR, 1)
        print("  the header was updated")
    else:
        print("  the header was not found")

    open(PATH, "w", encoding="utf-8").write(src)
    print(f"{PATH}: rewritten")


if __name__ == "__main__":
    main()
