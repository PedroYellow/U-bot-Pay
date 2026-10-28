#!/usr/bin/env python3
"""Give the contacts table the deployment's chain id, and write it.

Two hardcoded Solana chain ids in the contacts code:

    CREATE TABLE IF NOT EXISTS contacts(
      ...
      chain_id INTEGER DEFAULT 1151111081099710, ...)

    def add_contact(user_id, alias, name, address, country="", is_agent=0):
        c.execute("INSERT OR REPLACE INTO contacts(user_id,alias,name,address,country,is_agent) ...")

The schema default was Solana's, and add_contact did not write the
column at all - it relied on that default. So every contact created
in a whitelabel got Solana's chain id.

It does not look wrong. The contact is found, the balance works, the
payment flows - but the row is on the wrong chain, and the unique
index is on (user_id, alias, chain_id), so the same alias on the
right chain is a DIFFERENT row.

The same hardcoded id was in the schema of the other tables, so those
are checked too.

Usage: fix_contacts_chain_id.py <core.py> <chain-id>
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/core.py"
chain_id = sys.argv[2] if len(sys.argv) > 2 else "146"

src = open(path, encoding="utf-8").read()
n = 0

# 1. the schema defaults
def fix_schema(m):
    global n
    n += 1
    return m.group(1) + f"chain_id INTEGER DEFAULT {chain_id}"

src = re.sub(r"(chain_id INTEGER DEFAULT )\1151111081099710",
             fix_schema, src)

# 2. add_contact writes the chain id
OLD = '''def add_contact(user_id, alias, name, address, country="", is_agent=0):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO contacts(user_id,alias,name,address,country,is_agent) VALUES(?,?,?,?,?,?)",
                  (user_id, alias.lower(), name, address, country, is_agent))'''

NEW = '''def add_contact(user_id, alias, name, address, country="", is_agent=0,
                chain_id: int = chain.CHAIN_ID):
    """Create or replace a contact.

    The chain id is written explicitly. It used to be left out of the
    INSERT and taken from the schema's DEFAULT - and that DEFAULT was
    Solana's chain id, hardcoded, in every deployment. So every contact
    in a whitelabel was on the wrong chain, and the unique index on
    (user_id, alias, chain_id) made the same alias on the right chain
    a different row.
    """
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO contacts(user_id,alias,name,address,chain_id,country,is_agent) VALUES(?,?,?,?,?,?,?)",
                  (user_id, alias.lower(), name, address, chain_id,
                   country, is_agent))'''

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    n += 1
else:
    print("  add_contact was not found in the expected form")

open(path, "w", encoding="utf-8").write(src)
print(f"{path}: {n} change(s)")
print()
print("the remaining hardcoded Solana chain ids:")
for i, line in enumerate(src.split("\n"), 1):
    if "1151111081099710" in line:
        print(f"  {i}: {line.strip()[:100]}")
