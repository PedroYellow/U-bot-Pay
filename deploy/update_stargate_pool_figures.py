#!/usr/bin/env python3
"""Update the Stargate pool figures in the outreach docs.

The pool's USDC balance moved a lot: 27,339 -> 67,315.78 USDC, read
straight from the contract on 2026-10-29 (deploy/verify_stargate_pool.py).

That number is the opening line of the outreach email and the argument
turns on it, so it cannot be stale. And the change STRENGTHENS the
argument rather than weakening it: the pool now holds 2.5x the USDC
and still has zero LP tokens outstanding, so it still cannot route.
More liquidity, still no market - that is a stronger illustration that
the problem is the missing pool shares and not a thin market.

Usage: update_stargate_pool_figures.py <file> [...]
"""
import re
import sys

USDC = "67,315.78"
USDC_SHORT = "67,316"
OLD_VALUES = ["27,874", "27,339", "27874", "27339"]

EDITS = [
    # OUTREACH_EMAILS.md - the opening claim
    (
        "currently holds **27,874 USDC**, and it cannot service a transfer in",
        f"currently holds **{USDC_SHORT} USDC**, and it cannot service a transfer in",
    ),
    (
        "`0xa272ffe20cffe769cdfc4b63088dcd2c82a2d8f9` — **27,339 USDC held**,\n"
        "read straight from the contract on 2026-10-28. It was 27,874 USDC\n"
        "the day before, so the liquidity is draining rather than being\n"
        "supplied.",
        f"`0xa272ffe20cffe769cdfc4b63088dcd2c82a2d8f9` — **{USDC} USDC held**,\n"
        "read straight from the contract on 2026-10-29. It was 27,339 USDC\n"
        "the day before, so the liquidity has actually been supplied rather\n"
        "than drained — and it still does not route.",
    ),
    (
        "tokens outstanding.** Not a thin market — no market. The 27,339 USDC\n"
        "is sitting in the contract with nothing minted against it, so there is\n"
        "no counterparty on the other side of a swap and no price for the pool\n"
        "share.",
        f"tokens outstanding.** Not a thin market — no market. The {USDC_SHORT}\n"
        "USDC is sitting in the contract with nothing minted against it, so there\n"
        "is no counterparty on the other side of a swap and no price for the\n"
        "pool share. The balance has grown to nearly two and a half times what\n"
        "it was, and the pool still cannot route — which is the clearest\n"
        "evidence that the missing piece is the pool shares, not the USDC.",
    ),
    (
        '- **Lead with the number.** "$27,874" and "no route at $100" are the',
        f'- **Lead with the number.** "${USDC_SHORT}" and "no route at $100" are the',
    ),
    # OUTREACH_SONIC_STARGATE.md
    (
        "**27,874 USDC** and LI.FI returns *\"No available quotes for the requested",
        f"**{USDC_SHORT} USDC** and LI.FI returns *\"No available quotes for the requested",
    ),
    (
        "This is the number that explains the rest. The pool holds ~27,339 USDC",
        f"This is the number that explains the rest. The pool holds ~{USDC_SHORT} USDC",
    ),
    (
        "| The Sonic pool's own state, from the contract | `deploy/read_stargate_sonic_pool.py` — 27,339 USDC, 0.00 LP outstanding |",
        f"| The Sonic pool's own state, from the contract | `deploy/read_stargate_sonic_pool.py` — {USDC_SHORT} USDC, 0.00 LP outstanding |",
    ),
    # SONIC_NATIVE_VERSION.md
    (
        "| USDC held by that pool | **27,874.19 USDC** | `balanceOf(pool)` on `0x29219dd4…` |",
        f"| USDC held by that pool | **{USDC} USDC** | `balanceOf(pool)` on `0x29219dd4…` |",
    ),
    (
        "never arrived.** That is a fair thing to raise, politely, with the $27,874",
        f"never arrived.** That is a fair thing to raise, politely, with the ${USDC_SHORT}",
    ),
]


def main():
    total = 0
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        original = src
        n = 0
        for old, new in EDITS:
            if old in src:
                src = src.replace(old, new)
                n += 1
        if src != original:
            open(path, "w", encoding="utf-8").write(src)
        print(f"  {path}: {n} replacement(s)")
        total += n

    print(f"  {total} in total")

    # report anything still stale
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        stale = [v for v in OLD_VALUES if v in src]
        if stale:
            print(f"  {path}: STILL has {stale}")


if __name__ == "__main__":
    main()
