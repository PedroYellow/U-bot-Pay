#!/usr/bin/env python3
"""Watchdog for the Stargate -> Sonic routability.

Prints a single line. If the line is identical to the previous tick,
the scheduler skips the agent entirely, so a quiet week costs nothing.

The line changes when any of these change:
  - the pool's USDC balance moves by more than 5%
  - the pool's LP tokens outstanding move from zero (or back to zero)
  - Stargate becomes routable (or stops being routable)
  - the working route changes

The LP balance is the important one. The pool held ~27,339 USDC with
0.00 LP outstanding - the USDC was seeded into the contract without
anyone minting pool shares against it. That is why no route exists:
there is no counterparty to swap with and no price for the pool share.
Watching only the USDC would have said "the pool has liquidity" while
the pool had no market at all.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKER = os.path.join(HERE, "track_stargate_sonic.py")
POOL_READER = os.path.join(HERE, "read_stargate_sonic_pool.py")


def run(script, *args):
    try:
        out = subprocess.run([sys.executable, script, *args],
                             capture_output=True, text=True, timeout=180)
        return out.stdout
    except Exception as e:
        return f"ERR {type(e).__name__}: {e}"


def parse_pool(text):
    """Pull the numbers out of read_stargate_sonic_pool.py's report."""
    usdc = lp = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("USDC") and usdc is None:
            try:
                usdc = float(stripped.split()[-1].replace(",", ""))
            except ValueError:
                pass
        elif stripped.startswith("LP outstanding"):
            try:
                lp = float(stripped.split()[-1].replace(",", ""))
            except ValueError:
                pass
    return usdc, lp


def main():
    pool_text = run(POOL_READER)
    usdc, lp = parse_pool(pool_text)

    try:
        tracker = json.loads(run(TRACKER, "--json"))
    except Exception:
        tracker = {}

    first = tracker.get("stargate_first_routable_usdc")
    route = (tracker.get("working_route") or {}).get("tool")

    if first is not None:
        state = f"ROUTABLE_FROM_{int(first)}"
    else:
        state = "NOT_ROUTABLE"

    if lp is None:
        lp_str = "?"
    elif lp <= 0:
        lp_str = "0LP"
    else:
        lp_str = f"{lp:.0f}LP"

    usdc_str = f"{usdc:.0f}" if usdc is not None else "?"

    print(f"stargate-sonic: usdc={usdc_str} lp={lp_str} state={state} route={route}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
