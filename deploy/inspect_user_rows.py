#!/usr/bin/env python3
"""Report and clean the Sonic deployment's user rows.

upsert_user only sets the chain when the row has none:

    if not r["chain_id"]:
        c.execute("UPDATE users SET chain_id=? WHERE id=?", ...)

That is correct - a user who already has a chain keeps it, so
re-resolving from the demo wallet cannot move them to another chain.

But it means a row written with the WRONG chain id can never be
corrected by upsert_user. The rows left over from the Solana era are
exactly that: the test user kept chain id 1151111081099710 in a
deployment on 146, and every run of the test failed on it.

This reports every row, flags the ones whose chain is not this
deployment's, and removes the ones that are TEST rows - a telegram id
in the test range, or a name starting with "probe" or "wallet:".

Usage: inspect_user_rows.py <db-path> <chain-id> [--clean]
"""
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/data/usonicpay.db"
CHAIN = int(sys.argv[2]) if len(sys.argv) > 2 else 146
CLEAN = "--clean" in sys.argv

# The telegram ids the whitelabel tests use.
TEST_TG = {987654322, 778000111, 778000222, 1151111081099710}


def is_test_row(r):
    return (r["telegram_id"] in TEST_TG
            or (r["name"] or "").startswith("probe")
            or (r["name"] or "").startswith("wallet:")
            or (r["name"] or "").startswith("audit"))


c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

rows = list(c.execute(
    "SELECT id, telegram_id, name, address, chain_id FROM users"))

print(f"=== {DB} ===")
print(f"the chain this deployment is on: {CHAIN}")
print(f"{len(rows)} user row(s)")
print()

for r in rows:
    wrong = r["chain_id"] != CHAIN
    flag = "  <- WRONG CHAIN" if wrong else ""
    test = "  (test row)" if is_test_row(r) else ""
    print(f"  id={r['id']:<4} tg={r['telegram_id']:<14} "
          f"chain={r['chain_id']:<16} {r['name']}{flag}{test}")

wrong_rows = [r for r in rows if r["chain_id"] != CHAIN]
if wrong_rows:
    print()
    print(f"=== {len(wrong_rows)} row(s) on the wrong chain ===")
    for r in wrong_rows:
        print(f"  id={r['id']} tg={r['telegram_id']} "
              f"chain={r['chain_id']} -> {CHAIN}")

if CLEAN:
    print()
    print("=== cleaning the test rows ===")
    n = 0
    for r in rows:
        if is_test_row(r):
            c.execute("DELETE FROM users WHERE id=?", (r["id"],))
            print(f"  removed id={r['id']} tg={r['telegram_id']} {r['name']}")
            n += 1
    c.commit()
    print(f"  {n} row(s) removed")

    print()
    print("=== after ===")
    for r in c.execute("SELECT id, telegram_id, name, chain_id FROM users"):
        print(f"  id={r['id']:<4} tg={r['telegram_id']:<14} "
              f"chain={r['chain_id']:<16} {r['name']}")

print()
remaining_wrong = c.execute(
    "SELECT COUNT(*) FROM users WHERE chain_id IS NULL OR chain_id != ?",
    (CHAIN,)).fetchone()[0]
print(f"{remaining_wrong} user row(s) still on the wrong chain")
sys.exit(1 if remaining_wrong else 0)
