#!/usr/bin/env python3
"""Correct the size cap in the Stargate outreach email.

The email said the route "stops at $100" from Avalanche. It does not.
The real threshold, found by binary search between the last size that
worked and the first that did not, is $882:

    $100  -> QUOTE, out 99.58
    $200  -> QUOTE, out 199.16
    $300  -> QUOTE, out 298.74
    $500  -> QUOTE, out 497.89
    $750  -> QUOTE, out 746.84
    $882  -> QUOTE, out 878.54      <- the largest that routes
    $1,000 -> no quote

Where $100 came from: the tracker probed 10, 100, 1,000, 10,000 and
100,000. The first size that failed was 1,000, so "stops at $100" was
an inference from coarse sizes, not a measurement. The sizes were too
coarse to see the real edge.

Why it matters more than a typo

$100 is the number the email leads with, and it is the one claim that
is checkable in a single API call. If the Stargate team runs the
query at $500 and gets a quote, the email's central claim is false -
and everything else in it stops being worth reading.

The honest claim is STRONGER, not weaker: the route works to $882
from Avalanche and to $10,000+ from Ethereum, and the pool has 67,336
USDC in it with zero LP shares minted against it. The cap is real and
it is caused by exactly one missing thing.

So this replaces $100 with the measured threshold everywhere, and
corrects the direction of the ask: at $882 the route is not close to
being useless for a payment rail, it is being limited by a single
missing contract.

Usage: fix_stargate_threshold_claim.py <file> [...]
"""
import re
import sys

OLD_CLAIM = """**The pool's USDC balance is 67,335.87** — up from 27,874 two days
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
that is where we would be moving real payment volume."""

NEW_CLAIM = """**The pool's USDC balance is 67,335.87** — up from 27,874 two days
ago, read straight from the contract on Sonic mainnet. The liquidity
has been growing, and transfers do clear in both directions.

**But the pool's LP tokens report zero outstanding.** The USDC is
sitting in the contract with nothing minted against it. That is why
the route survives only while the trade is small: below roughly $880
there is enough for the routing algorithm to price a swap on its own,
and above it there is no counterparty on the other side and no price
for the pool share to quote against.

From Avalanche — our own chain, where our payment volume would come
from — the largest transfer that routes is **$882** (it returns 878.54
USDC), and $1,000 gets no quote. From Ethereum the route holds past
$10,000, because Ethereum-side liquidity covers the gap that the
Sonic-side missing shares leave open.

So the cap is not a general smallness: it is one missing contract,
and it puts a ceiling on exactly the direction we need."""


def main():
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        n = 0

        if OLD_CLAIM in src:
            src = src.replace(OLD_CLAIM, NEW_CLAIM, 1)
            n += 1

        # the other places the number appears
        src = src.replace(
            "so the route through it stops working above about $100\nfrom Avalanche.",
            "so the route through it stops working above about $880\nfrom Avalanche.")
        src = src.replace(
            "**Subject:** Sonic USDC pool: LP shares missing, route caps at $100 from Avalanche",
            "**Subject:** Sonic USDC pool: LP shares missing, route caps at $882 from Avalanche")
        src = src.replace(
            '"$67,336" and "no route above $100 from',
            '"$67,336" and "no route above $880 from')
        src = src.replace(
            "route caps at $100 from Avalanche",
            "route caps at $882 from Avalanche")
        src = src.replace(
            "it stops at $100, and\nthat is where we would be moving real payment volume.",
            "it stops at $882, and\nthat is where we would be moving real payment volume.")
        src = src.replace(
            "no route at $100",
            "no route above $880")

        open(path, "w", encoding="utf-8").write(src)

        # report what is left
        stale = re.findall(r"\$100\b", src)
        print(f"  {path}: rewritten ({n} block(s)), "
              f"{len(stale)} mention(s) of $100 left")

        for m in re.finditer(r".{0,60}\$100\b.{0,60}", src):
            print(f"    ...{m.group(0).strip()}...")


if __name__ == "__main__":
    main()
