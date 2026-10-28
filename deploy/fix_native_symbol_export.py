#!/usr/bin/env python3
"""Expose the native symbol from the chain module.

chain.py resolves its module attributes against the active engine with
PEP 562 __getattr__. The map had STABLE_SYMBOL but no NATIVE_SYMBOL,
even though the profile carries `native: S` and the EVM engine reads
it as `native_symbol`.

So anything that wanted the chain's own currency fell back to a
hardcoded "AVAX" - which is Avalanche's, and wrong everywhere else. The
balance reply on Sonic was checked for "None AVAX" while the actual
token was S.

Usage: fix_native_symbol_export.py <chain.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/chain.py"

src = open(path, encoding="utf-8").read()

# 1. the defaults map
OLD1 = '''    "USDC": None, "STABLE_SYMBOL": "USDC", "REVENUE_ADDRESS": "",'''
NEW1 = '''    "USDC": None, "STABLE_SYMBOL": "USDC", "NATIVE_SYMBOL": "ETH",
    "REVENUE_ADDRESS": "",'''

# 2. the attribute map
OLD2 = '''        attr = {"USDC": "stable_address", "STABLE_SYMBOL": "stable_symbol",
                "REVENUE_ADDRESS": "_revenue", "EXPLORER": "_explorer",'''
NEW2 = '''        attr = {"USDC": "stable_address", "STABLE_SYMBOL": "stable_symbol",
                "NATIVE_SYMBOL": "native_symbol",
                "REVENUE_ADDRESS": "_revenue", "EXPLORER": "_explorer",'''

n = 0
for old, new in ((OLD1, NEW1), (OLD2, NEW2)):
    if old in src:
        src = src.replace(old, new, 1)
        n += 1
    else:
        print(f"  anchor not found: {old.splitlines()[0][:60]}")

open(path, "w", encoding="utf-8").write(src)
print(f"{path}: {n} change(s)")
