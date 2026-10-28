#!/usr/bin/env python3
"""Report and fix every row on the wrong chain, in every table.

The schema's chain_id defaults were hardcoded to Solana's chain id in
every deployment, so rows inserted without an explicit chain id landed
on the wrong chain. The rows stay wrong: upsert_user only sets the
chain when the row has none, and the startup backfill only touches
rows where chain_id IS NULL.

This reports every table, flags the rows that are on the wrong chain,
and fixes them.

Usage: fix_wrong_chain_rows.py <db-path> <chain-id> [--clean]
"""
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/data/usonicpay.db"
CHAIN = int(sys.argv[2]) if len(sys.argv) > 2 else 146
CLEAN = "--clean" in sys.argv

TABLES = ["users", "contacts", "payments", "invoices", "bridges"]

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

print(f"=== {DB} ===")
print(f"the chain this deployment is on: {CHAIN}")
print()

total_bad = 0
for t in TABLES:
    try:
        rows = list(c.execute(f"SELECT * FROM {t}"))
    except sqlite3.Error:
        continue
    if not rows:
        continue
    if "chain_id" not in rows[0].keys():
        continue

    bad = [r for r in rows if r["chain_id"] != CHAIN]
    total_bad += len(bad)
    print(f"  {t:10s} {len(rows)} row(s), {len(bad)} on the wrong chain")
    for r in bad:
        label = ""
        for k in ("telegram_id", "alias", "name", "id"):
            if k in r.keys():
                label = f"{k}={r[k]}"
                break
        print(f"      id={r['id']} chain={r['chain_id']} {label}")

if CLEAN and total_bad:
    print()
    print("=== fixing ===")
    for t in TABLES:
        try:
            n = c.execute(
                f"UPDATE {t} SET chain_id = ? WHERE chain_id IS NULL OR chain_id != ?",
                (CHAIN, CHAIN)).rowcount
            if n:
                print(f"  {t}: {n} row(s) set to {CHAIN}")
        except sqlite3.Error:
            pass
    c.commit()

print()
remaining = 0
for t in TABLES:
    try:
        n = c.execute(
            f"SELECT COUNT(*) FROM {t} WHERE chain_id IS NULL OR chain_id != ?",
            (CHAIN,)).fetchone()[0]
        remaining += n
    except sqlite3.Error:
        pass

print(f"{remaining} row(s) still on the wrong chain")
sys.exit(1 if remaining else 0)
