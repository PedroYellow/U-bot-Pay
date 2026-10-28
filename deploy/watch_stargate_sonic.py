#!/usr/bin/env python3
"""Watchdog for the Stargate -> Sonic routability.

Prints a single line. If the line is identical to the previous tick, the
scheduler skips the agent entirely, so a quiet week costs nothing.

The line changes when any of these change:
  - the pool balance moves by more than 5%
  - Stargate becomes routable (or stops being routable)
  - the working route changes
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKER = os.path.join(HERE, "track_stargate_sonic.py")

try:
    out = subprocess.run([sys.executable, TRACKER, "--json"],
                         capture_output=True, text=True, timeout=180)
    d = json.loads(out.stdout)
except Exception as e:
    # a transient network failure must not look like a change
    print(f"stargate-sonic: unavailable ({type(e).__name__})")
    sys.exit(0)

pool = d.get("pool_usdc")
first = d.get("stargate_first_routable_usdc")
route = (d.get("working_route") or {}).get("tool")

if first is not None:
    state = f"ROUTABLE_FROM_{int(first)}"
else:
    state = "NOT_ROUTABLE"

pool_str = f"{pool:.0f}" if isinstance(pool, (int, float)) else "?"
print(f"stargate-sonic: pool={pool_str}USDC state={state} route={route}")
