#!/usr/bin/env python3
"""Give the bot-flow test a proper contact, and drop the broken one.

The contact the test needs was created by an earlier attempt with the
wrong signature, so the row reads:

    alias='mom', name='0x1111111111111111111111111111111111111111'

The name is the address. find_contact matches on the alias OR the
name, so it still finds it - but a contact whose name is a hex
address is not what the test means, and the row is a leftover from a
failed attempt rather than something the test creates.

This removes the malformed contacts and creates the one the test
needs, with a real name, for the user the bot acts as.

Usage: fix_bot_flow_contact.py <db-path> <user-id> <alias> <address>
"""
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/data/usonicpay.db"
USER_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 987654322
ALIAS = sys.argv[3] if len(sys.argv) > 3 else "mom"
ADDRESS = sys.argv[4] if len(sys.argv) > 4 else ("0x" + "11" * 20)
CHAIN = 146

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

print(f"=== {DB} ===")

# the malformed ones: the name is a hex address
malformed = [r["id"] for r in c.execute("SELECT id, name FROM contacts")
             if (r["name"] or "").startswith("0x")
             and len(r["name"] or "") >= 40]
for cid in malformed:
    c.execute("DELETE FROM contacts WHERE id=?", (cid,))
    print(f"  removed the malformed contact id={cid}")

# the one the test needs
c.execute("DELETE FROM contacts WHERE user_id=? AND alias=?",
          (USER_ID, ALIAS))
c.execute(
    "INSERT INTO contacts(user_id,alias,name,address,chain_id,country,is_agent) "
    "VALUES(?,?,?,?,?,?,0)",
    (USER_ID, ALIAS, ALIAS.capitalize(), ADDRESS, CHAIN, ""))
c.commit()

print()
print("=== the contacts now ===")
for r in c.execute("SELECT id,user_id,alias,name,chain_id FROM contacts"):
    print(f"  id={r['id']} user={r['user_id']} alias={r['alias']} "
          f"name={r['name']} chain={r['chain_id']}")
