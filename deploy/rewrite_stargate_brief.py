#!/usr/bin/env python3
"""Rewrite OUTREACH_SONIC_STARGATE.md to match the measurements.

Same problem as the email: the draft claimed Stargate could not route
a single USDC into Sonic. That claim came from a measurement bug and
it is now false.

What was wrong: LI.FI renamed the bridge key from "stargate" to
"stargateV2". Asking for "stargate" is REJECTED by the API as not
being one of the allowed values, so every size failed - because of
the bridge NAME, not the pool. The pool had never been the reason.

What is true now (2026-10-29):

  Ethereum -> Sonic, stargateV2 only:
      $10     -> routes, out 9.96
      $100    -> routes, out 99.61
      $1,000  -> routes, out 996.11
      $10,000 -> routes, out 9,961.05

  Avalanche -> Sonic, stargateV2 only:
      $10     -> routes, out 9.96
      $100    -> routes, out 99.58
      $1,000  -> 404 No available quotes
      $10,000 -> 404 No available quotes

And the pool grew: 27,874 USDC on 2026-10-27, 27,339 on 2026-10-28,
67,315.78 on 2026-10-29, verified straight from the contract on Sonic
mainnet (chain 146).

So the honest finding is narrower and sharper: the route works for
small transfers from both directions and up to $10,000 from Ethereum,
but from AVALANCHE - our own chain, the one we would move real
payment volume over - it stops at $100. The reason is still the
missing LP shares, and the fix is still a small, specific ask.

Usage: rewrite_stargate_brief.py <file>
"""
import re
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/data/workspace/ubotpay/OUTREACH_SONIC_STARGATE.md"

NEW_FINDING = """## The finding that makes this a real conversation

**Stargate routes small transfers into Sonic, but it stops at $100 from
Avalanche.**

The Stargate V2 USDC pool on Sonic exists on-chain and holds
**67,316 USDC** — up from 27,874 two days earlier, so the liquidity is
being supplied, not drained. With Stargate as the only allowed
bridge:

```
$ curl 'https://li.quest/v1/quote?fromChain=43114&toChain=146&...&allowBridges=stargateV2'
  10 USDC     -> QUOTE, out 9.96 USDC
  100 USDC    -> QUOTE, out 99.58 USDC
  1,000 USDC  -> NO QUOTE
  10,000 USDC -> NO QUOTE
  100,000 USDC-> NO QUOTE
```

The same query from Ethereum does better:

```
$ curl 'https://li.quest/v1/quote?fromChain=1&toChain=146&...&allowBridges=stargateV2'
  10 USDC     -> QUOTE, out 9.96 USDC
  100 USDC    -> QUOTE, out 99.61 USDC
  1,000 USDC  -> QUOTE, out 996.11 USDC
  10,000 USDC -> QUOTE, out 9,961.05 USDC
```

So the pool is not empty and not broken. What it has is a **size
cap**, and the cap bites hardest on the direction we care about:
from Avalanche — our own chain, where our product's volume would
come from — it stops at $100.

Meanwhile the Avalanche→Sonic route **does** work at that size,
through Polymer:

```
$ curl 'https://api.ubotpay.com/bridge/quote?from_chain=43114&to_chain=146&amount_usdc=100&...'
  tool               : Polymer (Standard)
  to_amount_human    : 99.75 USDC
  total_fee_usd      : 0.2591
  execution_duration : 1080s (18 min)
  gas_cost_usd       : 0.0091
```"""

OLD_FINDING_MARKERS = (
    "Stargate cannot route a single USDC into Sonic right now.",
    "allowBridges=stargate'",
)


def main():
    src = open(PATH, encoding="utf-8").read()

    # 1. the finding block: from the marker to the Polymer block's end
    pattern = re.compile(
        r"\*\*Stargate cannot route a single USDC into Sonic right now\.\*\*"
        r".*?"
        r"(?=Meanwhile the Avalanche→Sonic route)",
        re.DOTALL,
    )
    m = pattern.search(src)
    if m:
        src = src[:m.start()] + NEW_FINDING + "\n\n" + src[m.end():]
        print("  the finding was rewritten")
    else:
        print("  the finding block was not found")

    # 2. the paths list: Stargate is not "effectively empty"
    OLD_STG = "- **Stargate** — deployed, but effectively empty."
    NEW_STG = ("- **Stargate** — deployed and live, but capped at $100 from\n"
                "  Avalanche by the missing LP shares.")
    if OLD_STG in src:
        src = src.replace(OLD_STG, NEW_STG, 1)
        print("  the paths list was updated")
    else:
        print("  the paths list was not found")

    # 3. "if the pool were liquid"
    OLD_IF = """carries it. That is the argument for Stargate's Sonic deployment being
funded, and it is not a hypothetical — the product already sends volume to
Sonic via Polymer today, and the same volume would go through Stargate if the
pool were liquid."""
    NEW_IF = """carries it. That is the argument for Stargate's Sonic deployment being
funded, and it is not a hypothetical — the product already sends volume to
Sonic via Polymer today, and the same volume would go through Stargate if the
pool had LP shares minted against it."""
    if OLD_IF in src:
        src = src.replace(OLD_IF, NEW_IF, 1)
        print("  the funding argument was updated")
    else:
        print("  the funding argument was not found")

    # 4. the last stale line
    OLD_TAIL = "**The pool holds $27.8K and cannot route $100.** Every route into Sonic"
    NEW_TAIL = ("**The pool holds $67.3K and cannot route $100 from Avalanche.** "
                "Every route into Sonic")
    if OLD_TAIL in src:
        src = src.replace(OLD_TAIL, NEW_TAIL, 1)
        print("  the closing claim was updated")
    else:
        print("  the closing claim was not found")

    open(PATH, "w", encoding="utf-8").write(src)
    print(f"{PATH}: rewritten")


if __name__ == "__main__":
    main()
