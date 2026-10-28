#!/usr/bin/env python3
"""Fix the rows left over from the SQL chain-id bug.

The env-prefix rewrite put the literal text `chain.CHAIN_ID` inside SQL
string literals, so the statements that backfilled the chain id failed
with "no such column". The rows were left with NULL - or, where the
default came from somewhere else, with the OLD chain id.

Those rows are wrong and the startup backfill cannot fix them: it only
touches rows where chain_id IS NULL, and it is wrapped in try/except so
it fails silently.

This reports what is wrong and fixes it, so the deployment's data
matches the chain it is actually on.

Usage: fix_stale_chain_rows.py <db-path> <chain-id>
"""
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/data/usonicpay.db"
CHAIN = int(sys.argv[2]) if len(sys.argv) > 2 else 146

# The chain ids that are wrong for this deployment: NULL (the backfill
# never ran) and the old Solana id (the rewrite changed the default but
# the rows kept the old value).
STALE = [None, 1151111081099710]

TABLES = ["users", "contacts", "payments", "invoices"]

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

print(f"=== {DB} ===")
print(f"the chain this deployment is on: {CHAIN}")
print()

fixed = 0
for t in TABLES:
    try:
        total = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except sqlite3.Error:
        continue

    bad = []
    for stale in STALE:
        q = f"SELECT COUNT(*) FROM {t} WHERE chain_id IS NULL" if stale is None \
            else f"SELECT COUNT(*) FROM {t} WHERE chain_id = ?"
        args = () if stale is None else (stale,)
        try:
            n = c.execute(q, args).fetchone()[0]
        except sqlite3.Error:
            continue
        if n:
            bad.append((stale, n))

    ids = [r[0] for r in c.execute(f"SELECT DISTINCT chain_id FROM {t}")]
    if not bad:
        print(f"  {t:10s} {total} rows, chain ids {ids}  - correct")
        continue

    for stale, n in bad:
        where = "chain_id IS NULL" if stale is None else f"chain_id = {stale}"
        c.execute(f"UPDATE {t} SET chain_id = ? WHERE {where}", (CHAIN,))
        print(f"  {t:10s} {total} rows, fixed {n} with {where} -> {CHAIN}")
        fixed += n

c.commit()

print()
print("=== after ===")
for t in TABLES:
    try:
        ids = [r[0] for r in c.execute(f"SELECT DISTINCT chain_id FROM {t}")]
        nulls = c.execute(f"SELECT COUNT(*) FROM {t} WHERE chain_id IS NULL").fetchone()[0]
        print(f"  {t:10s} chain ids {ids}, {nulls} NULL")
    except sqlite3.Error:
        pass

print()
print(f"{fixed} row(s) fixed")
sys.exit(0 if fixed else 1)
