#!/usr/bin/env python3
"""Replace the stale $100 threshold with the measured $882.

Every occurrence has to be judged, because "$100" appears in two
different senses in these documents:

  - the THRESHOLD, which is $882 and was wrongly recorded as $100
  - an ILLUSTRATION, like "a payments rail that cannot move $100 is
    not a payments rail" - which is still true, and reads better
    with a round number

So this replaces the threshold usages and leaves the illustrations
alone. Each replacement is listed, so it can be checked.

Usage: replace_threshold_100.py <file> [...]
"""
import re
import sys

# The threshold usages. Each is a specific phrase, not a bare $100.
REPLACEMENTS = [
    # OUTREACH_EMAILS.md
    ("which caps the route at $100 from",
     "which caps the route at $882 from"),
    # OUTREACH_SONIC_STARGATE.md
    ("**Stargate routes small transfers into Sonic, but it stops at $100 from\nAvalanche.**",
     "**Stargate routes small transfers into Sonic, but it stops at $882 from\nAvalanche.**"),
    ("come from — it stops at $100.",
     "come from — it stops at $882."),
    ("- **Stargate** — deployed and live, but capped at $100 from\n  Avalanche by the missing LP shares.",
     "- **Stargate** — deployed and live, but capped at $882 from\n  Avalanche by the missing LP shares."),
    ("**This is why the route caps at $100 rather than failing outright.**",
     "**This is why the route caps at $882 rather than failing outright.**"),
    ("Below roughly $100 the routing algorithm can price a small swap",
     "Below roughly $880 the routing algorithm can price a small swap"),
    ("**The pool holds $67.3K and cannot route $100 from Avalanche.**",
     "**The pool holds $67.3K and cannot route $880 from Avalanche.**"),
    # SONIC_NATIVE_VERSION.md
    ("**Stargate routes small transfers into Sonic, but it stops at $100 from\nAvalanche.**",
     "**Stargate routes small transfers into Sonic, but it stops at $882 from\nAvalanche.**"),
    ("LI.FI returns a quote at small sizes and none above $100 from Avalanche:",
     "LI.FI returns a quote at small sizes and none above $880 from Avalanche:"),
    ("reports 0.00 outstanding, so below roughly $100 the router can price",
     "reports 0.00 outstanding, so below roughly $880 the router can price"),
]

# Left alone on purpose - they are illustrations, and they read better
# with a round number:
#   "a payments rail that cannot move $100 is not a payments rail"
#   "100 USDC     -> routes, out 99.58 USDC"
#   "$100 there is enough for the routing algorithm"


def main():
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        n = 0
        for old, new in REPLACEMENTS:
            if old in src:
                src = src.replace(old, new)
                n += 1

        open(path, "w", encoding="utf-8").write(src)

        left = re.findall(r"\$100\b", src)
        print(f"  {path}: {n} replacement(s), {len(left)} mention(s) of $100 left")
        for m in re.finditer(r".{0,58}\$100\b.{0,58}", src):
            print(f"    kept: ...{m.group(0).strip()}...")


if __name__ == "__main__":
    main()
