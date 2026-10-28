#!/usr/bin/env python3
"""Two remaining bot-flow assertions on an EVM chain.

## the token with a balance is named first

The Solana path sorts the tokens so the one holding something comes
first, and the test asserts that. The EVM path did not sort - it
appended the stable figure and then the native figure, in that order.
With the stable at zero and the native holding the balance, the
reply led with "0 USDC", which reads as an empty wallet when it is
not.

## the confirmation names the token the user asked for

The test sends "manda 2 USDT a mom" and expects the confirmation to
name USDT. The bot asked "who should I send it to?" because there is
no contact called "mom" - the Solana deployment's database had one and
a fresh deployment does not.

The test creates its own contact, so it does not depend on the
database's history. The contact API here is:

    add_contact(user_id, alias, name, address, country="", is_agent=0)

Usage: fix_bot_flow_evm_remaining.py <test.py> <tg.py>
"""
import sys

test_path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/test_bot_flow.py"
tg_path = sys.argv[2] if len(sys.argv) > 2 else "/opt/usonicpay/tg.py"

# ── 1. the EVM path sorts by balance too ──────────────────────────
tg = open(tg_path, encoding="utf-8").read()

OLD_TG = '''            parts = []

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

NEW_TG = '''            # The same rule as the Solana path: the token that holds
            # something comes first. With the stable at zero and the
            # native holding the balance, listing the stable first
            # reads as an empty wallet when it is not.
            figures = []
            stable = d.get("stable_balance")
            if stable is None:
                stable = d.get("usdc")
            if stable is None:
                stable = d.get("settled")
            if stable is not None:
                figures.append((sym, stable))

            native = d.get("native_balance")
            if native is not None:
                figures.append((native_sym, native))

            figures.sort(key=lambda t: (not bool(t[1]), t[0]))
            parts = [f"{amt} {tsym}" for tsym, amt in figures]
            if not parts:
                parts = [f"0 {sym}"]'''

if OLD_TG in tg:
    tg = tg.replace(OLD_TG, NEW_TG, 1)
    open(tg_path, "w", encoding="utf-8").write(tg)
    print(f"{tg_path}: the EVM path sorts the tokens by balance")
else:
    print(f"{tg_path}: the EVM balance block was not found")

# ── 2. the test creates its own "mom" contact ────────────────────
src = open(test_path, encoding="utf-8").read()

OLD_TEST = '''    # The contact the confirmation asks about. The Solana deployment's
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

NEW_TEST = '''    # The contact the confirmation asks about. The Solana deployment's
    # database had a "mom"; a fresh deployment does not, and the
    # confirmation then stops at "who should I send it to?". The test
    # creates its own contact so it does not depend on the database's
    # history.
    _MOM = "0x" + "11" * 20
    try:
        _u = core.upsert_user(987654321, "probe-bot", "en", None,
                              chain.CHAIN_ID)
        _uid = _u.get("id") if isinstance(_u, dict) else _u
        core.add_contact(_uid, "mom", "Mom", _MOM)
    except Exception:
        for _fn in ("upsert_contact", "add_contact", "create_contact"):
            _f = getattr(core, _fn, None)
            if _f is None:
                continue
            for _args in ((987654321, "mom", "Mom", _MOM),
                          (987654321, "mom", _MOM),
                          (987654321, "mom")):
                try:
                    _f(*_args)
                    break
                except TypeError:
                    continue
                except Exception:
                    break
            else:
                continue
            break
    out = run_bot("manda 2 USDT a mom")'''

if OLD_TEST in src:
    src = src.replace(OLD_TEST, NEW_TEST, 1)
    open(test_path, "w", encoding="utf-8").write(src)
    print(f"{test_path}: the test creates its own contact")
else:
    print(f"{test_path}: the contact block was not found")
