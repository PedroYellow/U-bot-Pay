#!/usr/bin/env python3
"""Put the bot-flow test's contact on the user the bot acts as.

The test creates a contact for one telegram id and then sends a
message as another. core.find_contact filters on the users TABLE's id
- the internal id - so the contact has to be on the row of the
telegram id the message comes from.

The test sent as 987654322 and created the contact for 987654321, so
the lookup found nothing and the bot answered "I don't have mom in
your contacts."

The contact is now created for the id the message is sent as.

Usage: fix_bot_flow_contact_target.py <test.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/test_bot_flow.py"

src = open(path, encoding="utf-8").read()

OLD = '''    _MOM = "0x" + "11" * 20
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
            break'''

NEW = '''    # The contact has to be on the user the message is sent as.
    # core.find_contact filters on the users table's id - the internal
    # id - and the message goes out as 987654322. The contact used to
    # be created for a different id, so the lookup found nothing.
    _MOM = "0x" + "11" * 20
    try:
        _u = core.upsert_user(987654322, "probe", "en", None,
                              chain.CHAIN_ID)
        _uid = _u.get("id") if isinstance(_u, dict) else _u
        core.add_contact(_uid, "mom", "Mom", _MOM)
    except Exception:
        for _fn in ("upsert_contact", "add_contact", "create_contact"):
            _f = getattr(core, _fn, None)
            if _f is None:
                continue
            for _args in ((_uid, "mom", "Mom", _MOM),
                          (_uid, "mom", _MOM),
                          (_uid, "mom")):
                try:
                    _f(*_args)
                    break
                except TypeError:
                    continue
                except Exception:
                    break
            else:
                continue
            break'''

if OLD not in src:
    print("  the contact block was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the contact is created for the user the message is sent as")
