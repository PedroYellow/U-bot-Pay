#!/usr/bin/env python3
"""Replace the hardcoded chain-id defaults with the engine's own.

The Solana deployment hardcoded its chain id in about a dozen places
as a default argument:

    def resolve_user(user_key, chain_id: int = 1151111081099710)
    chain_id: int = Field(default=1151111081099710, ...)

A Sonic deployment inherited every one of them, so its API reported
chain_id 1151111081099710 while actually settling on 146. The engine
already knows the right answer - chain.CHAIN_ID resolves against the
active profile - so the defaults should come from there.

Usage: fix_chain_id_defaults.py <tree-dir>
"""
import os
import re
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay"
SOLANA = "1151111081099710"

targets = []
for dirpath, dirnames, files in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in ("venv", "__pycache__", ".git")]
    for fn in files:
        if fn.endswith(".py"):
            targets.append(os.path.join(dirpath, fn))

total = 0
for path in targets:
    src = open(path, encoding="utf-8").read()
    if SOLANA not in src:
        continue
    out = src
    # a default argument:  = 1151111081099710
    out = re.sub(r"(=\s*)" + SOLANA + r"\b", r"\1chain.CHAIN_ID", out)
    # a dict value:      "chain_id": 1151111081099710,
    out = re.sub(r"([\"']chain_id[\"']\s*:\s*)" + SOLANA + r"\b",
                 r"\1chain.CHAIN_ID", out)
    if out != src:
        open(path, "w", encoding="utf-8").write(out)
        n = len(re.findall(r"chain\.CHAIN_ID", out)) - len(re.findall(r"chain\.CHAIN_ID", src))
        total += n
        print(f"{path.replace(root.rstrip('/') + '/', '')}: {n} default(s)")

print(f"{total} chain-id default(s) now come from the engine")
