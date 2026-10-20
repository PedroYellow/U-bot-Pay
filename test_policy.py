"""
Regression tests for the money controls (audit 20 Oct 2026, third round).

Two bugs fixed here:
  1. `user.get('daily_limit') or 1e12` turned an explicit 0 — the most
     restrictive setting — into a trillion USDC. Same shape on
     `dual_threshold = 0`, which disabled co-signing entirely.
  2. `spent_today` counted only status='settled', so several payments could be
     quoted against a limit that looked untouched, and `/approve` settled them
     without re-checking the policy.

Runs against a real throwaway SQLite DB — no network, no chain.

    python3 test_policy.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Point core at a scratch DB before it is imported.
_DB = os.path.join(tempfile.mkdtemp(prefix="ubotpay-test-"), "t.db")
os.environ["UBOT_DB"] = _DB
os.environ.setdefault("UBOT_WALLET_ADDRESS", "0x" + "aa" * 20)

import core  # noqa: E402

core.DB = _DB
core.init()

# ── fixtures ──────────────────────────────────────────────────────────────
def mkuser(limit, threshold, name="t"):
    return core.upsert_user(None, name, "en", "0x" + "bb" * 20)


def add_payment(uid, amount, status="settled"):
    pid = "pay_test_" + core.secrets.token_hex(4)
    with core.conn() as c:
        c.execute(
            "INSERT INTO payments(id,user_id,contact_alias,to_address,amount_usdc,"
            "amount_local,local_ccy,fx_rate,fee_usdc,status,channel,needs_approval,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, uid, "a", "0x" + "cc" * 20, amount, amount, "USDC", 1.0, 0.0,
             status, "test", 0, int(core.time.time())),
        )
    return pid


RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


# ── 1. daily_limit = 0 must BLOCK, not allow ───────────────────────────────
u0 = core.upsert_user_by_wallet("0x" + "0" * 35 + "d0", 43114)
core.set_policy(u0["id"], daily_limit=0.0, dual_threshold=10.0)
u0 = core.get_user(user_id=u0["id"])          # re-read: set_policy writes to the DB
check("daily_limit=0 blocks any send",
      core.check_policy(u0, 1.0)["allowed"], False)
check("daily_limit=0 blocks even 0.01",
      core.check_policy(u0, 0.01)["allowed"], False)
# and the error message is actually useful
print(f"      reason: {core.check_policy(u0, 1.0)['reason']!r}")

# ── 2. daily_limit normal value still works ───────────────────────────────
u = core.upsert_user_by_wallet("0x" + "0" * 35 + "a1", 43114)
core.set_policy(u["id"], daily_limit=40.0, dual_threshold=10.0)
u = core.get_user(user_id=u["id"])
check("under limit -> allowed", core.check_policy(u, 5.0)["allowed"], True)
check("over limit -> blocked", core.check_policy(u, 41.0)["allowed"], False)
check("exactly at limit -> allowed", core.check_policy(u, 40.0)["allowed"], True)
check("one over limit -> blocked", core.check_policy(u, 40.01)["allowed"], False)

# ── 3. dual_threshold = 0 must REQUIRE a co-signer, not skip it ───────────
u0t = core.upsert_user_by_wallet("0x" + "0" * 35 + "b2", 43114)
core.set_policy(u0t["id"], daily_limit=100.0, dual_threshold=0.0)
u0t = core.get_user(user_id=u0t["id"])
check("dual_threshold=0 requires approval for any amount",
      core.check_policy(u0t, 0.01)["needs_approval"], True)
check("dual_threshold=0 requires approval even for 0.00",
      core.check_policy(u0t, 0.0)["needs_approval"], True)

# ── 4. dual_threshold normal ──────────────────────────────────────────────
u = core.get_user(user_id=u["id"])
check("9 USDC under threshold -> no approval", core.check_policy(u, 9.0)["needs_approval"], False)
check("10 USDC at threshold -> approval", core.check_policy(u, 10.0)["needs_approval"], True)
check("25 USDC over threshold -> approval", core.check_policy(u, 25.0)["needs_approval"], True)

# ── 5. pending payments RESERVE budget ────────────────────────────────────
# The core of the /approve bypass: three payments quoted while the total
# looked like zero must not all clear the cap.
ub = core.upsert_user_by_wallet("0x" + "0" * 35 + "c3", 43114)
core.set_policy(ub["id"], daily_limit=40.0, dual_threshold=10.0)
ub = core.get_user(user_id=ub["id"])
p1 = add_payment(ub["id"], 30.0, status="pending_approval")
check("pending payment counts as committed", core.spent_today(ub["id"]), 30.0)
check("only 10 USDC left after a pending 30", core.check_policy(ub, 11.0)["allowed"], False)
check("exactly 10 USDC still fits", core.check_policy(ub, 10.0)["allowed"], True)
check("settled-only view ignores pending",
      core.spent_today(ub["id"], include_reserved=False), 0.0)

# ── 6. the /approve re-check shape ────────────────────────────────────────
# /approve calls check_policy(user, 0.0): "given what is already committed
# today, is there still room for the payment sitting in pending_approval?"
check("approve re-check (30 of 40 committed) -> still room",
      core.check_policy(ub, 0.0)["allowed"], True)

# The bypass this closes: three payments quoted while the total looked like
# zero. Each one reserved at quote time, so approving them all must not pass.
ub2 = core.upsert_user_by_wallet("0x" + "0" * 35 + "e5", 43114)
core.set_policy(ub2["id"], daily_limit=40.0, dual_threshold=10.0)
ub2 = core.get_user(user_id=ub2["id"])
for _ in range(3):
    add_payment(ub2["id"], 12.0, status="pending_approval")
check("three 12 USDC pendings reserve 36 of 40",
      core.spent_today(ub2["id"]), 36.0)
check("approve re-check passes while under the cap",
      core.check_policy(ub2, 0.0)["allowed"], True)
# A fourth would push it over, and must be refused at quote time.
check("a 4th pending of 12 does not fit",
      core.check_policy(ub2, 12.0)["allowed"], False)

# exclude_payment_id is still available for other callers, but /approve must
# not rely on it — subtracting the pending amount would reopen the bypass.
check("exclude drops the payment from the sum",
      core.check_policy(ub, 30.0, exclude_payment_id=p1)["allowed"], True)

# ── 7. failed/rejected payments do NOT reserve ────────────────────────────
for st in ("failed", "rejected", "expired_policy"):
    add_payment(ub["id"], 999.0, status=st)
check("failed/rejected do not count toward the cap",
      core.spent_today(ub["id"]), 30.0)

# ── 8. daily window uses local midnight, not UTC ──────────────────────────
# UBOT_DAY_TZ_OFFSET defaults to 7 (Bangkok). A payment created just before
# local midnight must count as "today" even if UTC already rolled over.
import datetime  # noqa: E402
check("day start is aligned to the configured offset",
      core._day_start(0) % 86400, 0)
# The day must roll over at local midnight, i.e. 17:00 UTC for +07:00.
start = core._day_start(7)
start_utc = datetime.datetime.fromtimestamp(start, datetime.timezone.utc)
check("Bangkok day rolls over at 17:00 UTC", start_utc.hour, 17)
check("Bangkok day start has 00 minutes", start_utc.minute, 0)
check("UTC day rolls over at 00:00",
      datetime.datetime.fromtimestamp(core._day_start(0), datetime.timezone.utc).hour, 0)
# And a payment from 30 minutes ago still falls inside the window.
check("a payment 30 min ago is inside today",
      core.time.time() - 1800 >= start, True)
print(f"      day start = {start_utc:%Y-%m-%d %H:%M} UTC (= 00:00 Bangkok)")

# ── 9. None policy falls back to an operator default, not infinity ────────
core.upsert_user_by_wallet("0x" + "0" * 35 + "d4", 43114)
un = core.get_user_by_wallet("0x" + "0" * 35 + "d4", 43114)
un.pop("daily_limit", None)
check("None daily_limit -> uses UBOT_DEFAULT_DAILY_LIMIT, not 1e12",
      core._daily_limit(un) < 1e6, True)
print(f"      default limit = {core._daily_limit(un)}")

print("=" * 70)
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
sys.exit(0 if all(RESULTS) else 1)