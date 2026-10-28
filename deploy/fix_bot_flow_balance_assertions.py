#!/usr/bin/env python3
"""Make the bot-flow balance assertions chain-aware.

Three assertions in test_bot_flow.py describe Solana:

    "the balance reports 'settled', not 'usdc'"
    "the balance reply does not say 'None AVAX'"

The Solana engine reports the stable balance as "settled"; the EVM
engine reports it as "usdc". So the first assertion fails on Sonic for
the right reason and the wrong message - and the point of the
assertion, that the balance is reported under ONE field and not two,
is worth keeping.

The second names AVAX. Sonic's native token is S, so the check has to
name this chain's stable and native symbols - otherwise it would pass
on a deployment whose native token is S and never notice a "None S".

Usage: fix_bot_flow_balance_assertions.py <test.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/test_bot_flow.py"

OLD_FIELD = '''chk("the balance reports 'settled', not 'usdc'",
    "settled" in b and "usdc" not in b,
    sorted(k for k in b if k in ("settled", "usdc")))'''

NEW_FIELD = '''# The balance is reported under ONE field: "settled" on the Solana
# engine, "usdc" on the EVM engine. The assertion is that there is one
# field and not two - the name depends on the deployment.
_BALANCE_FIELDS = ("settled", "usdc")
_present = [k for k in _BALANCE_FIELDS if k in b]
chk("the balance reports one balance field, not two",
    len(_present) == 1,
    sorted(k for k in b if k in _BALANCE_FIELDS))'''

OLD_REPLY = '''    chk("the balance reply does not say 'None USDC'",
          "None USDC" not in out and "None AVAX" not in out, out[:90])'''

NEW_REPLY = '''    # The native symbol differs per chain - AVAX on Avalanche, S on
    # Sonic. A check that names only AVAX would pass on Sonic and
    # never notice a "None S".
    _native = getattr(chain, "NATIVE_SYMBOL", None) or "AVAX"
    _stable = getattr(chain, "STABLE_SYMBOL", None) or "USDC"
    chk("the balance reply does not say 'None <token>'",
        f"None {_stable}" not in out and f"None {_native}" not in out,
        out[:90])'''

src = open(path, encoding="utf-8").read()
n = 0
for old, new in ((OLD_FIELD, NEW_FIELD), (OLD_REPLY, NEW_REPLY)):
    if old in src:
        src = src.replace(old, new, 1)
        n += 1
    else:
        print(f"  the anchor was not found: {old.splitlines()[0][:60]}")

open(path, "w", encoding="utf-8").write(src)
print(f"{path}: {n} assertion(s) made chain-aware")
