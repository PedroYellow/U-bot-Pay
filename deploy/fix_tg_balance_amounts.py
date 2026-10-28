#!/usr/bin/env python3
"""Read the EVM balance's two figures from the right keys.

The EVM engine's balance is:

    {"address": ..., "native": "S", "native_balance": 0.0,
     "chain": "Sonic", "chain_id": 146,
     "usdc": 0.0, "stable_balance": 0.0}

Note that "native" is the SYMBOL and "native_balance" is the amount.
The first version of the fix took "native" as the amount, so the reply
read "S S" - the symbol twice.

The "usdc" key is also a figure, not a symbol: "usdc" holds the
amount and "stable_symbol" holds the name. And "stable_balance" is
the same amount under a clearer name.

Usage: fix_tg_balance_amounts.py <tg.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/tg.py"

src = open(path, encoding="utf-8").read()

OLD = '''        else:
            # The EVM shape: a flat stable figure and a flat native one.
            native_sym = d.get("native_symbol") or "S"
            parts = []
            stable = d.get("usdc")
            if stable is None:
                stable = d.get("settled")
            if stable is not None:
                parts.append(f"{stable} {sym}")
            native = d.get("native")
            if native is None:
                native = d.get("native_balance")
            if native is not None:
                parts.append(f"{native} {native_sym}")
            if not parts:
                parts = [f"0 {sym}"]'''

NEW = '''        else:
            # The EVM shape. The keys are easy to get wrong: "native"
            # is the SYMBOL and "native_balance" is the amount, and
            # "usdc" holds the amount while the name is in
            # "stable_symbol". Taking "native" as the amount printed
            # the symbol twice - "S S".
            native_sym = d.get("native_symbol") or d.get("native") or "S"
            if native_sym and not str(native_sym).isalpha():
                # "native" held an amount, so it is not the symbol.
                native_sym = "S"
            parts = []

            stable = d.get("stable_balance")
            if stable is None:
                stable = d.get("usdc")
            if stable is None:
                stable = d.get("settled")
            if stable is not None:
                parts.append(f"{stable} {sym}")

            native = d.get("native_balance")
            if native is not None:
                parts.append(f"{native} {native_sym}")

            if not parts:
                parts = [f"0 {sym}"]'''

if OLD not in src:
    print("  the EVM balance block was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the EVM balance figures now come from the right keys")
