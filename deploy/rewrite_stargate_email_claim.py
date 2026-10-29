#!/usr/bin/env python3
"""Rewrite the Stargate outreach email's claim block to match reality.

The draft said the route was dead at every size. That is no longer
true, and the email's whole argument rested on it.

What the measurements actually show now (2026-10-29):

  Ethereum -> Sonic, stargateV2 only:
      $10     -> OK, out 9.96
      $100    -> OK, out 99.61
      $1,000  -> OK, out 996.11
      $10,000 -> OK, out 9,961.05

  Avalanche -> Sonic, stargateV2 only:
      $10     -> OK, out 9.96
      $100    -> OK, out 99.58
      $1,000  -> 404 No available quotes
      $10,000 -> 404 No available quotes

Two things changed, and the first one was a measurement bug:

1. The bridge key. LI.FI renamed it from "stargate" to "stargateV2".
   Asking for "stargate" is REJECTED by the API as not being one of
   the allowed values - so the earlier measurement was of a bridge
   name that does not exist, not of the pool. Every size failed for
   that reason, and the pool was never the reason.

2. The pool grew. It held 27,874 USDC on 2026-10-27, 27,339 on
   2026-10-28, and 67,315.78 on 2026-10-29 - verified straight from
   the contract on Sonic mainnet (chain 146). So the liquidity was
   being supplied, not drained.

So the honest claim is narrower and stronger: Stargate routes small
transfers from BOTH directions, and from Ethereum it routes up to at
least $10,000. What it does not do is route $1,000+ into Sonic FROM
Avalanche - which is the direction our own product uses, and the one
that matters.

The email is rewritten around that. It no longer says "no route at any
size", because that is false and the Stargate team can disprove it in
one API call - which would end the conversation and make the rest of
it worthless.

Usage: rewrite_stargate_email_claim.py <file>
"""
import re
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/data/workspace/ubotpay/OUTREACH_EMAILS.md"

NEW_CLAIM = """**The route works — but only where the pool has shares to trade.**
With Stargate as the only allowed bridge, from both directions:

```
Ethereum → Sonic
  10 USDC      -> routes, out 9.96 USDC
  100 USDC     -> routes, out 99.61 USDC
  1,000 USDC   -> routes, out 996.11 USDC
  10,000 USDC  -> routes, out 9,961.05 USDC

Avalanche → Sonic
  10 USDC      -> routes, out 9.96 USDC
  100 USDC     -> routes, out 99.58 USDC
  1,000 USDC   -> No available quotes for the requested transfer
  10,000 USDC  -> No available quotes for the requested transfer
```

So the pool is not dead, and I want to be precise about what we are
asking, because the difference is the whole point.

**The pool's USDC balance is 67,315.78** — up from 27,874 two days
ago, read straight from the contract on Sonic mainnet. The liquidity
has been growing, and small transfers do clear in both directions.

**But the pool's LP tokens report zero outstanding.** The USDC is
sitting in the contract with nothing minted against it. That is why
the route survives only while the trade is small: below roughly
$100 there is enough for the routing algorithm to price a swap on its
own, and above it there is no counterparty on the other side and no
price for the pool share to quote against.

From Ethereum the route holds to $10,000 — Ethereum-side liquidity
covers the gap. From Avalanche, our own chain, it stops at $100, and
that is where we would be moving real payment volume.

So the request is not "make the pool work" — it works. It is to get
**LP minted against the USDC already in the contract**, so the route
stops being size-limited. The USDC alone is not a route; it is
collateral waiting for someone to mint against it."""


def main():
    src = open(PATH, encoding="utf-8").read()

    # The block runs from "But the number that matters" to the end of
    # the fenced quote block. Matched with a regex so a reflowed
    # paragraph does not break it.
    pattern = re.compile(
        r"But the number that matters.*?"
        r"```\n.*?```\n",
        re.DOTALL,
    )

    m = pattern.search(src)
    if not m:
        print("  the claim block was not found")
        sys.exit(1)

    old = m.group(0)
    print(f"  replacing {len(old)} chars")

    src = src[:m.start()] + NEW_CLAIM + "\n" + src[m.end():]

    # The opening paragraph said "it cannot service a transfer in
    # practice" - true only above $100 from Avalanche.
    OLD_OPEN = """promised on day one, and the contracts are deployed. But the USDC pool
currently holds **67,316 USDC**, and it cannot service a transfer in
practice. I wanted to share that with a concrete number, because we are
already routing volume into Sonic and would rather it went through you."""

    NEW_OPEN = """promised on day one, and the contracts are deployed. But the USDC pool
currently holds **67,316 USDC** and its LP tokens report zero
outstanding, so the route through it stops working above about $100
from Avalanche. I wanted to share that with a concrete number, because
we are already routing volume into Sonic and would rather it went
through you."""

    if OLD_OPEN in src:
        src = src.replace(OLD_OPEN, NEW_OPEN, 1)
        print("  the opening was updated")
    else:
        print("  the opening was not found in the expected form")

    # The ask.
    OLD_ASK = """1. **A liquidity proposal for the Sonic USDC pool** at a size that can
   service real payment volume rather than a few hundred thousand. The
   original proposal asked for 1m USDC and 1m USDT to be migrated; that
   would change the picture entirely. Concretely: whoever supplies the
   USDC also needs to mint the pool's LP tokens against it. The USDC
   alone is not a route — there is nothing to trade it for."""

    NEW_ASK = """1. **LP minted against the USDC already in the pool.** This is the
   specific, small ask. The 67,315.78 USDC is there; it just has no
   pool shares minted against it, which caps the route at $100 from
   Avalanche. The original proposal asked for 1m USDC and 1m USDT to
   be migrated — that would change the picture entirely — but the LP
   shares are the part that decides whether the route works at
   volume."""

    if OLD_ASK in src:
        src = src.replace(OLD_ASK, NEW_ASK, 1)
        print("  the ask was updated")
    else:
        print("  the ask was not found in the expected form")

    open(PATH, "w", encoding="utf-8").write(src)
    print(f"{PATH}: rewritten")


if __name__ == "__main__":
    main()
