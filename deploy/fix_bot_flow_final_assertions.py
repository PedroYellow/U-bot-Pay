#!/usr/bin/env python3
"""Fix the last three Solana-shaped assertions in the bot-flow test.

1. The test passes the Solana chain id to upsert_user explicitly:

       core.upsert_user(987654322, "probe", "en", None, 1151111081099710)

   so the user record has Solana's id, and then asserts it equals
   chain.CHAIN_ID. It should pass this deployment's chain id.

2. The "the balance reply does not say 'None <token>'" assertion. The
   reply it got was "You have 0.0 USDC, about 0.0 USD." - which does
   NOT contain "None USDC", so the assertion should have passed. The
   problem is the f-string: it was built as f"None {_stable}" where
   _stable is "USDC" - that is right - but the check ran against the
   wrong reply. The balance reply it inspected was a different one.

   The assertion is kept but made explicit: the reply must contain a
   number and must not contain "None".

3. The confirmation test needs a contact named "mom". The Solana
   deployment's database has one; a fresh deployment does not. The
   test creates its own contact, so it does not depend on the
   database's history.

Usage: fix_bot_flow_final_assertions.py <test.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/test_bot_flow.py"

src = open(path, encoding="utf-8").read()
n = 0

# 1. the chain id passed to upsert_user
OLD1 = '''    u = core.upsert_user(987654322, "probe", "en", None, 1151111081099710)'''
NEW1 = '''    # This deployment's chain id, not Solana's. The test passed the
    # Solana id explicitly, so the user record had the wrong chain.
    u = core.upsert_user(987654322, "probe", "en", None, chain.CHAIN_ID)'''

# 2. the balance reply assertion
OLD2 = '''    # The native symbol differs per chain - AVAX on Avalanche, S on
    # Sonic. A check that names only AVAX would pass on Sonic and
    # never notice a "None S".
    _native = getattr(chain, "NATIVE_SYMBOL", None) or "AVAX"
    _stable = getattr(chain, "STABLE_SYMBOL", None) or "USDC"
    chk("the balance reply does not say 'None <token>'",
        f"None {_stable}" not in out and f"None {_native}" not in out,
        out[:90])'''

NEW2 = '''    # The reply must carry a real number, and must not carry the
    # string "None". The native symbol differs per chain - AVAX on
    # Avalanche, S on Sonic - so a check that named only one of them
    # would pass on the other chain and never notice a "None S".
    _native = getattr(chain, "NATIVE_SYMBOL", None) or "AVAX"
    _stable = getattr(chain, "STABLE_SYMBOL", None) or "USDC"
    chk("the balance reply carries a number, not 'None'",
        "None" not in out
        and any(ch.isdigit() for ch in out)
        and f"None {_stable}" not in out
        and f"None {_native}" not in out,
        out[:90])'''

for old, new in ((OLD1, NEW1), (OLD2, NEW2)):
    if old in src:
        src = src.replace(old, new, 1)
        n += 1
    else:
        print(f"  anchor not found: {old.splitlines()[0][:60]}")

# 3. the "mom" contact - the test creates its own, so it does not
#    depend on the database's history.
OLD3 = '''    out = run_bot("manda 2 USDT a mom")'''
NEW3 = '''    # The contact the confirmation asks about. The Solana deployment's
    # database had a "mom"; a fresh deployment does not, and the
    # confirmation then stops at "who should I send it to?". The test
    # creates its own contact so it does not depend on history.
    try:
        core.upsert_contact(987654322, "mom", "0x" + "11" * 20,
                            chain.CHAIN_ID)
    except Exception:
        for _fn in ("upsert_contact", "add_contact", "create_contact"):
            _f = getattr(core, _fn, None)
            if _f is None:
                continue
            try:
                _f(987654322, "mom", "0x" + "11" * 20, chain.CHAIN_ID)
                break
            except TypeError:
                try:
                    _f(987654322, "mom", "0x" + "11" * 20)
                    break
                except Exception:
                    continue
            except Exception:
                continue
    out = run_bot("manda 2 USDT a mom")'''

if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1)
    n += 1
else:
    print(f"  anchor not found: {OLD3[:60]}")

open(path, "w", encoding="utf-8").write(src)
print(f"{path}: {n} change(s)")
