#!/usr/bin/env python3
"""Make the audit-findings balance assertion read the engine's shape.

The assertion was:

    chk("the balance answers with a number",
        isinstance((body.get("data") or {}).get("settled"), (int, float)),
        repr((body.get("data") or {}).get("settled")))

"settled" is the Solana engine's field. The EVM engine returns
"stable_balance" and "usdc", so the lookup found nothing and the
assertion failed with None - on a balance that is present and correct.

The point of the assertion is that the balance is a NUMBER and not a
Decimal that breaks when it meets the float FX rate. So it should ask
for the figure the engine returned, whichever that is.

Usage: fix_audit_findings_balance_assertion.py <test.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/test_audit_findings.py"

src = open(path, encoding="utf-8").read()

OLD = '''        chk("the balance answers with a number",
            isinstance((body.get("data") or {}).get("settled"), (int, float)),
            repr((body.get("data") or {}).get("settled")))'''

NEW = '''        # The balance is a NUMBER, not a Decimal - the engine returns
        # money as a Decimal and the FX table is floats, and the
        # product of the two is what used to raise.
        #
        # The field name depends on the engine: "stable_balance" or
        # "usdc" on EVM, "settled" on Solana. The assertion used to
        # read only "settled", so on EVM it found nothing and failed
        # with None on a balance that was present and correct.
        _data = body.get("data") or {}
        _balance = None
        for _k in ("stable_balance", "usdc", "settled"):
            if _data.get(_k) is not None:
                _balance = _data[_k]
                break
        chk("the balance answers with a number",
            isinstance(_balance, (int, float))
            and not isinstance(_balance, bool),
            repr(_balance))'''

if OLD not in src:
    print("  the assertion was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the assertion reads the engine's own field")
