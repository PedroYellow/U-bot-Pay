#!/usr/bin/env python3
"""Refresh the Stargate pool figure in the outreach docs.

The number moves. It was 27,874 on 2026-10-27, 27,339 on 2026-10-28,
67,315.78 on 2026-10-29 morning, and 67,335.87 by the afternoon of the
same day. So a figure written into an email goes stale within hours.

The fix

Update it to the CURRENT reading, and put the date next to it. A
number without a date is a number nobody can check, and the whole
point of the email is that it is checkable.

The measurement comes from deploy/verify_stargate_pool.py, which
reads the contract directly. So the figure does not depend on the
watchdog's parsing.

Usage: refresh_stargate_pool_figure.py <file> [...]
"""
import re
import subprocess
import sys

OLD = [
    "67,316",
    "67,315.78",
    "67,336",
    "67,335.87",
]

DATE = "2026-10-29"


def current_pool():
    """Read the pool balance from the chain."""
    out = subprocess.run(
        ["python3", "deploy/verify_stargate_pool.py"],
        capture_output=True, text=True, timeout=180, cwd=".")
    m = re.search(r"the pool's USDC balance:\s*([\d,]+\.\d+)",
                  out.stdout)
    if not m:
        return None
    return m.group(1)


def main():
    pool = current_pool()
    if not pool:
        print("  the pool could not be read - nothing was changed")
        sys.exit(1)

    short = f"{float(pool.replace(',', '')):,.0f}"
    print(f"  the pool, read from the contract: {pool} (${short})")

    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        before = src

        # the figure
        src = re.sub(r"67,31[56]\.78", pool, src)
        src = re.sub(r"67,316", short, src)
        src = re.sub(r"67,335\.87", pool, src)
        src = re.sub(r"\$67,336", f"${short}", src)

        # the date, so the number is checkable
        src = src.replace(
            f"**{pool} USDC**",
            f"**{pool} USDC**")
        if f"read straight from the contract on {DATE}" not in src:
            src = src.replace(
                f"read straight from the contract on 2026-10-29",
                f"read straight from the contract on {DATE}")

        if src != before:
            open(path, "w", encoding="utf-8").write(src)
            print(f"  {path}: updated")
        else:
            print(f"  {path}: already at {pool}")


if __name__ == "__main__":
    main()
