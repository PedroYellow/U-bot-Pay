#!/usr/bin/env python3
"""Report the users in a deployment's database, and flag test leftovers.

A wallet-ratelimit test used to point at the LIVE database: it set
the DB path with `setdefault`, which does not set a variable that is
already set - and UBOT_DB was already set, in the deployment's .env
and in the service's own environment.

So the test created users and recorded rate-limit state in production.
This reports what is there, and which rows look like test leftovers.

Usage: inspect_db_users.py <db-path> [--json]
"""
import json
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/opt/ubotpay/ubotpay.db"

# Names a test would use. The ratelimit test creates wallets named
# after the thing it is testing.
TEST_MARKERS = ("test", "probe", "rl_", "wallet_rl", "aud")

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

try:
    rows = list(c.execute(
        "SELECT telegram_id, name, address, chain_id, daily_limit, "
        "created_at FROM users ORDER BY id"))
except sqlite3.Error as e:
    print(f"  {DB}: {e}")
    sys.exit(1)

out = []
for r in rows:
    out.append({
        "telegram_id": r["telegram_id"],
        "name": r["name"],
        "address": r["address"],
        "chain_id": r["chain_id"],
        "daily_limit": r["daily_limit"],
        "created_at": r["created_at"],
    })

if "--json" in sys.argv:
    print(json.dumps(out, indent=2))
    sys.exit(0)

print(f"=== {DB} ===")
print(f"  {len(out)} user row(s)")
print()

suspects = []
for u in out:
    name = (u["name"] or "").lower()
    tg = str(u["telegram_id"] or "")
    flagged = any(m in name for m in TEST_MARKERS)
    if flagged:
        suspects.append(u)

    addr = u["address"] or ""
    print(f"  tg={tg:<18} name={str(u['name'])[:30]:<30} "
          f"chain={u['chain_id']} addr={addr[:20]}")

print()
if suspects:
    print(f"=== {len(suspects)} row(s) look like test leftovers ===")
    for u in suspects:
        print(f"  tg={u['telegram_id']} name={u['name']!r} "
              f"created={u['created_at']}")
else:
    print("no rows look like test leftovers")
