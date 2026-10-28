#!/usr/bin/env python3
"""Read the balance in whichever shape the engine returned.

tg.py read the balance in the SOLANA shape and nothing else:

    toks = d.get("tokens") or {}
    amt = tinfo.get("settled") or 0
    ...
    native_sym = d.get("native_symbol") or "SOL"

The EVM engine returns a different shape - a flat "usdc" figure and a
"native" figure, not a "tokens" map of "settled" values. So on Sonic
the tokens map was empty, the code fell through to

    parts = [f"{d.get('settled')} {sym}"]

and the user got:

    💰 None USDC · 0.0 SOL

"None" because there is no "settled" key, and "SOL" because the
fallback symbol was Solana's.

The reply has to read whichever shape the engine gave it. The Solana
shape is kept exactly as it was - that deployment is correct - and the
EVM shape is added.

Usage: fix_tg_balance_shape.py <tg.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/tg.py"

src = open(path, encoding="utf-8").read()

OLD = '''    if res.get("action") == "balance":
        # The Solana engine returns `settled` and `native_balance`,
        # with `native_symbol` for the chain's own coin. The old
        # `usdc`/`avax` keys belong to the Avalanche deployment: reading
        # them here printed "None USDC · None AVAX" on every balance.
        sym = d.get("symbol") or "USDC"
        native_sym = d.get("native_symbol") or "SOL"
        toks = d.get("tokens") or {}

        # Put the tokens that actually hold something first. The
        # primary token can be at zero while the other one carries the
        # balance, and leading with "0 USDC" reads as "empty wallet"
        # when it is not.
        ordered = []
        for tsym, tinfo in toks.items():
            amt = tinfo.get("settled") or 0
            ordered.append((tsym, amt, bool(amt)))
        ordered.sort(key=lambda t: (not t[2], t[0]))

        parts = [f"{amt} {tsym}" for tsym, amt, _ in ordered]
        if not parts:
            parts = [f"{d.get('settled')} {sym}"]
        if d.get("native_balance") is not None:
            parts.append(f"{d.get('native_balance')} {native_sym}")
        return "💰 " + " · ".join(parts) + f"\\n`{d.get('address')}`"'''

NEW = '''    if res.get("action") == "balance":
        # The engines return the balance in different shapes, so the
        # reply reads whichever one it was given.
        #
        # The Solana engine returns a "tokens" map of "settled" values,
        # plus "native_balance" and "native_symbol". The EVM engine
        # returns a flat "usdc" figure and a "native" figure.
        #
        # Reading only the Solana shape printed "None USDC · 0.0 SOL"
        # on Sonic: the tokens map was empty, the code fell through to
        # d.get("settled") - which does not exist on EVM - and the
        # fallback symbol was Solana's.
        sym = d.get("symbol") or "USDC"

        toks = d.get("tokens") or {}
        if toks:
            native_sym = d.get("native_symbol") or "SOL"

            # Put the tokens that actually hold something first. The
            # primary token can be at zero while the other one carries
            # the balance, and leading with "0 USDC" reads as "empty
            # wallet" when it is not.
            ordered = []
            for tsym, tinfo in toks.items():
                amt = tinfo.get("settled") or 0
                ordered.append((tsym, amt, bool(amt)))
            ordered.sort(key=lambda t: (not t[2], t[0]))

            parts = [f"{amt} {tsym}" for tsym, amt, _ in ordered]
            if not parts:
                parts = [f"{d.get('settled')} {sym}"]
            if d.get("native_balance") is not None:
                parts.append(f"{d.get('native_balance')} {native_sym}")
        else:
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
                parts = [f"0 {sym}"]

        return "💰 " + " · ".join(parts) + f"\\n`{d.get('address')}`"'''

if OLD not in src:
    print("  the balance block was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the balance reply now reads whichever shape the engine returned")
