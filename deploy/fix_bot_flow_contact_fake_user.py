#!/usr/bin/env python3
"""Put the bot-flow test's contact on the fake user the bot acts as.

The test's fake telegram user has id 424242 - the demo user, whose
users.id is 1. core.find_contact filters on the users table's id, so
the contact has to be on THAT row.

The test was creating it for 987654322 (internal id 5), a user the
bot never sends anything as. The lookup found nothing and the bot
answered "I don't have mom in your contacts."

The contact now goes on the fake user's own row, looked up the same
way the API looks it up.

Usage: fix_bot_flow_contact_fake_user.py <test.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/test_bot_flow.py"

src = open(path, encoding="utf-8").read()

OLD = '''    try:
        _u = core.upsert_user(987654322, "probe", "en", None,
                              chain.CHAIN_ID)
        _uid = _u.get("id") if isinstance(_u, dict) else _u
        core.add_contact(_uid, "mom", "Mom", _MOM)'''

NEW = '''    try:
        # The fake telegram user is 424242 - the demo user. The bot
        # sends as THAT user, so the contact belongs on that user's
        # row. The test created it for a different telegram id, so the
        # lookup found nothing.
        _u = core.upsert_user(FakeUser.id, "Demo User", "es", None,
                              chain.CHAIN_ID)
        _uid = _u.get("id") if isinstance(_u, dict) else _u
        core.add_contact(_uid, "mom", "Mom", _MOM)'''

if OLD not in src:
    print("  the contact block was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the contact is created for the fake user the bot sends as")
