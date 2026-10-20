"""
Regression tests for the /approve double-spend race (audit 20 Oct, round 6).

api.approve() read the payment, checked status == 'pending_approval', then
awaited chain.send_usdc(...) and only wrote 'settled' afterwards. Two concurrent
/approve calls both passed the read-side check and both transferred funds —
trivial to trigger, and automatic with more than one uvicorn worker.

claim_payment_for_settlement() makes it a single atomic UPDATE.

    python3 test_approve_race.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_DB = os.path.join(tempfile.mkdtemp(prefix="ubotrace-"), "t.db")
os.environ["UBOT_DB"] = _DB
os.environ.setdefault("UBOT_WALLET_ADDRESS", "0x" + "aa" * 20)

import core  # noqa: E402

core.DB = _DB
core.init()

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


u = core.upsert_user_by_wallet("0x" + "bb" * 20, 43114)
core.set_policy(u["id"], daily_limit=100.0, dual_threshold=10.0)
u = core.get_user(user_id=u["id"])
Q = {"amount_usdc": 12.0, "amount_local": 400.0, "local_ccy": "THB",
     "fx_rate": 0.03, "fee_usdc": 0.01}
CONTACT = {"alias": "mom", "address": "0x" + "cc" * 20, "name": "Mom"}


def make_pending():
    p = core.create_payment(u["id"], CONTACT, Q, None, "tg", True)
    return p["id"]


# ── exactly one winner ────────────────────────────────────────────────────
def t_single_claim():
    pid = make_pending()
    check("first claim wins", core.claim_payment_for_settlement(pid), True)
    check("second claim refused", core.claim_payment_for_settlement(pid), False)
    check("third claim refused", core.claim_payment_for_settlement(pid), False)
    check("status moved to settling", core.get_payment(pid)["status"], "settling")


def t_concurrent_claims_exactly_one_wins():
    """The actual race: N threads claiming the same payment at once."""
    pid = make_pending()
    winners = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        got = core.claim_payment_for_settlement(pid)
        with lock:
            winners.append(got)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check("8 concurrent claims -> exactly 1 winner", winners.count(True), 1)
    check("the other 7 refused", winners.count(False), 7)


def t_release_allows_retry():
    pid = make_pending()
    check("claimed", core.claim_payment_for_settlement(pid), True)
    check("cannot re-claim while settling", core.claim_payment_for_settlement(pid), False)
    core.release_payment_claim(pid)
    check("after release it can be claimed again", core.claim_payment_for_settlement(pid), True)


def t_release_is_noop_after_settlement():
    """The finally-block releases unconditionally; it must not resurrect a
    settled payment."""
    pid = make_pending()
    core.claim_payment_for_settlement(pid)
    core.mark(pid, "settled")
    core.release_payment_claim(pid)
    check("a settled payment stays settled", core.get_payment(pid)["status"], "settled")
    check("and cannot be claimed again", core.claim_payment_for_settlement(pid), False)


def t_unknown_payment_cannot_be_claimed():
    check("unknown payment id -> claim refused",
          core.claim_payment_for_settlement("pay_does_not_exist"), False)


def t_settled_payment_cannot_be_claimed():
    pid = make_pending()
    core.claim_payment_for_settlement(pid)
    core.mark(pid, "settled")
    check("already-settled payment refuses a claim",
          core.claim_payment_for_settlement(pid), False)


def t_settling_reserves_budget():
    """A payment being settled must count against the daily limit, so a second
    approval attempt can't spend the same headroom."""
    uid2 = core.upsert_user_by_wallet("0x" + "0" * 35 + "e9", 43114)
    core.set_policy(uid2["id"], daily_limit=50.0, dual_threshold=10.0)
    uid2 = core.get_user(user_id=uid2["id"])
    p = core.create_payment(uid2["id"], CONTACT, Q, None, "tg", True)
    before = core.spent_today(uid2["id"])
    core.claim_payment_for_settlement(p["id"])
    after = core.spent_today(uid2["id"])
    check("claiming keeps the reservation", (before, after), (12.0, 12.0))
    check("limit still enforced while settling",
          core.check_policy(uid2, 45.0)["allowed"], False)


def run():
        t_single_claim()
        t_concurrent_claims_exactly_one_wins()
        t_release_allows_retry()
        t_release_is_noop_after_settlement()
        t_unknown_payment_cannot_be_claimed()
        t_settled_payment_cannot_be_claimed()
        t_settling_reserves_budget()


if __name__ == "__main__":
    t_single_claim()
    t_concurrent_claims_exactly_one_wins()
    t_release_allows_retry()
    t_release_is_noop_after_settlement()
    t_unknown_payment_cannot_be_claimed()
    t_settled_payment_cannot_be_claimed()
    t_settling_reserves_budget()
    print("=" * 70)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
