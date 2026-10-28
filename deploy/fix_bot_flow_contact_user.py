#!/usr/bin/env python3
"""Put the bot-flow test's contact on the user the bot acts as.

core.find_contact(uid, ...) filters on the users TABLE's id - the
internal id - not the telegram id. api.py does:

    uid = user["id"]
    contact = core.find_contact(uid, it["recipient"])

The test created the contact under the TELEGRAM id, so the lookup
found nothing and the bot answered "I don't have mom in your
contacts."

The test now resolves the user the same way the API does and puts the
contact on that row's id.

Usage: fix_bot_flow_contact_user.py <db-path> <telegram-id> <alias>
"""
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/data/usonicpay.db"
TG_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 987654322
ALIAS = sys.argv[3] if len(sys.argv) > 3 else "mom"
ADDRESS = "0x" + "11" * 20
CHAIN = 146

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

row = c.execute("SELECT id, telegram_id FROM users WHERE telegram_id=?",
                (TG_ID,)).fetchone()
if row is None:
    print(f"  no user with telegram_id {TG_ID}")
    sys.exit(1)

uid = row["id"]
print(f"=== {DB} ===")
print(f"  telegram {TG_ID} -> users.id {uid}")

# the contact belongs on the internal id
c.execute("DELETE FROM contacts WHERE user_id=? AND alias=?", (uid, ALIAS))
c.execute(
    "INSERT INTO contacts(user_id,alias,name,address,chain_id,country,is_agent) "
    "VALUES(?,?,?,?,?,?,0)",
    (uid, ALIAS, ALIAS.capitalize(), ADDRESS, CHAIN, ""))
c.commit()

print()
print("=== the contacts now ===")
for r in c.execute("SELECT id,user_id,alias,name,chain_id FROM contacts"):
    print(f"  id={r['id']} user={r['user_id']} alias={r['alias']} "
          f"name={r['name']} chain={r['chain_id']}")
