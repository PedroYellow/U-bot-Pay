"""
Regression tests for the push-subscription hijack (audit 20 Oct, round 6).

push_subscriptions has UNIQUE(endpoint). save_push_subscription matched on
endpoint alone, so any authenticated caller could post a known push endpoint
and silently take the row over — the UPDATE rewrote user_id and the previous
owner stopped receiving notifications. A cross-user write.

Runs against a real SQLite DB.

    python3 test_push_hijack.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_DB = os.path.join(tempfile.mkdtemp(prefix="ubotpush-"), "t.db")
os.environ["UBOT_DB"] = _DB
os.environ.setdefault("UBOT_WALLET_ADDRESS", "0x" + "aa" * 20)

import core  # noqa: E402

core.DB = _DB
core.init()

RESULTS = []
EP_A = "https://push.example.com/sub/A"
EP_B = "https://push.example.com/sub/B"


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


def owner_of(endpoint):
    with core.conn() as c:
        r = c.execute("SELECT user_id FROM push_subscriptions WHERE endpoint = ?",
                      (endpoint,)).fetchone()
    return r["user_id"] if r else None


# ── normal use ────────────────────────────────────────────────────────────
id_a = core.save_push_subscription(1, EP_A, "k1", "a1")
check("first subscription is created", id_a is not None, True)
check("owner recorded as user 1", owner_of(EP_A), 1)

# same user refreshing their own subscription must keep working
id_a2 = core.save_push_subscription(1, EP_A, "k1-rotated", "a2")
check("same user can refresh their own subscription", id_a2, id_a)
check("still owned by user 1", owner_of(EP_A), 1)

# ── the hijack ────────────────────────────────────────────────────────────
try:
    core.save_push_subscription(999, EP_A, "attacker-key", "attacker-auth")
    check("cannot steal another user's endpoint", False, True)
except ValueError as e:
    check("cannot steal another user's endpoint", "another account" in str(e), True)
check("ownership unchanged after the attempt", owner_of(EP_A), 1)

# the attacker's keys must not have overwritten the victim's
with core.conn() as c:
    r = dict(c.execute("SELECT p256dh, auth FROM push_subscriptions WHERE endpoint = ?",
                       (EP_A,)).fetchone())
check("victim's p256dh intact", r["p256dh"], "k1-rotated")
check("victim's auth intact", r["auth"], "a2")

# ── a different endpoint for a different user is fine ─────────────────────
id_b = core.save_push_subscription(999, EP_B, "k2", "b1")
check("a second user can subscribe normally", id_b is not None, True)
check("second endpoint owned by user 999", owner_of(EP_B), 999)

# ── no cross-contamination ────────────────────────────────────────────────
subs_1 = core.get_push_subscriptions_for_user(1)
subs_999 = core.get_push_subscriptions_for_user(999)
check("user 1 sees only their own", [s["endpoint"] for s in subs_1], [EP_A])
check("user 999 sees only their own", [s["endpoint"] for s in subs_999], [EP_B])

# ── empty / hostile endpoints ─────────────────────────────────────────────
for bad in ("", "not-a-url", "javascript:alert(1)", "x" * 5000):
    try:
        core.save_push_subscription(1, bad, "k", "a")
        stored = owner_of(bad)
        check(f"endpoint {bad[:20]!r} stored only under user 1", stored, 1)
    except Exception as e:
        check(f"endpoint {bad[:20]!r} rejected cleanly", type(e).__name__ in
              ("ValueError", "sqlite3.IntegrityError", "OperationalError"), True)

print("=" * 70)
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
sys.exit(0 if all(RESULTS) else 1)