"""
Regression tests for stranded settlement claims (audit 20 Oct round 12).

claim_payment_for_settlement() moves a payment to 'settling' before the chain
call, and release_payment_claim() hands it back in a `finally`. That covers an
exception, but not a process that stops existing: SIGKILL, an OOM kill, or a
systemd restart between the claim and the settle leaves the payment in 'settling'
with nothing left to release it.

The consequences are not cosmetic:

  - spent_today() counts 'settling' as committed budget, so the user's daily limit
    stays debited by money that will never be spent.
  - claim_payment_for_settlement() refuses to re-claim it, because its WHERE
    requires status='pending_approval'.

Reproduced before the fix: 50 USDC out of a 100 limit reserved permanently,
recoverable only by editing the database by hand.

    python3 test_settling_reaper.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


def fresh_core():
    """A core module pointed at a new DB, with no background reaper running."""
    os.environ["UBOT_DB"] = tempfile.mktemp(suffix=".db")
    for mod in ("core",):
        sys.modules.pop(mod, None)
    import core
    core.init()
    return core


def make_payment(core, user, amount=50.0):
    contact = {"alias": "bob", "address": "0x" + "11" * 20}
    q = {"amount_usdc": amount, "amount_local": amount,
         "local_ccy": "USDC", "fx_rate": 1.0}
    p = core.create_payment(user["id"], contact, q, "memo", "telegram", True)
    return p["id"] if isinstance(p, dict) else p


def t_claim_is_atomic_under_concurrency():
    """Re-confirmed here so the reaper cannot regress the guarantee it depends on."""
    import threading
    core = fresh_core()
    u = core.upsert_user(111, "tester")
    core.set_policy(u["id"], daily_limit=100.0)
    pid = make_payment(core, u)
    core.mark(pid, "pending_approval")

    wins = []
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        if core.claim_payment_for_settlement(pid):
            wins.append(1)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("only one of four concurrent claims wins", len(wins), 1)


def t_claimed_at_is_stamped():
    core = fresh_core()
    u = core.upsert_user(112, "tester")
    pid = make_payment(core, u)
    core.mark(pid, "pending_approval")
    core.claim_payment_for_settlement(pid)
    check("claimed_at is set on claim", core.get_payment(pid)["claimed_at"] is not None, True)


def t_stranded_claim_holds_budget_then_is_reaped():
    """The bug, then the fix."""
    core = fresh_core()
    u = core.upsert_user(113, "tester")
    core.set_policy(u["id"], daily_limit=100.0)
    uid = u["id"]
    pid = make_payment(core, u, amount=50.0)
    core.mark(pid, "pending_approval")
    check("claim is taken", core.claim_payment_for_settlement(pid), True)

    check("stranded claim reserves budget", core.spent_today(uid), 50.0)
    check("payment is settling", core.get_payment(pid)["status"], "settling")

    # The process would have died right here, before mark('settled').
    check("still stuck", core.get_payment(pid)["status"], "settling")
    check("and cannot be re-claimed", core.claim_payment_for_settlement(pid), False)

    # The reaper only touches claims older than max_age_s, so a fresh stuck claim
    # is left alone: reaping immediately would interrupt a settlement in flight.
    check("fresh stuck claim is not reaped", core.reap_stale_settling_claims(300), [])

    # Age it past the threshold, as five minutes would.
    with core.conn() as c:
        c.execute("UPDATE payments SET claimed_at = claimed_at - 600 WHERE id = ?", (pid,))
    reaped = core.reap_stale_settling_claims(300)

    check("stale claim is reaped", reaped, [pid])
    check("payment is approvable again", core.get_payment(pid)["status"], "pending_approval")
    check("claimed_at cleared", core.get_payment(pid)["claimed_at"], None)
    check("and it can now be claimed", core.claim_payment_for_settlement(pid), True)


def t_budget_is_released():
    """The user-visible symptom.

    Note what reaping does and does not do to the budget: a reaped payment goes
    back to pending_approval, which is *also* a reserving status — that is by
    design, so N payments cannot all be quoted against a limit of one. So the
    budget is not freed by reaping; what reaping restores is the user's ability to
    act on that payment at all. It was previously stuck in a state that could
    neither be approved again nor released.
    """
    core = fresh_core()
    u = core.upsert_user(114, "tester")
    core.set_policy(u["id"], daily_limit=100.0)
    uid = u["id"]
    pid = make_payment(core, u, amount=50.0)
    core.mark(pid, "pending_approval")
    check("claim is taken", core.claim_payment_for_settlement(pid), True)

    check("cannot settle a second time while stuck",
          core.claim_payment_for_settlement(pid), False)

    with core.conn() as c:
        c.execute("UPDATE payments SET claimed_at = claimed_at - 600 WHERE id = ?", (pid,))
    core.reap_stale_settling_claims(300)

    check("payment is approvable again", core.get_payment(pid)["status"], "pending_approval")
    check("and can be claimed for settlement",
          core.claim_payment_for_settlement(pid), True)

    # Once it genuinely settles the money is spent, and the budget moves on.
    core.mark(pid, "settled", tx="0xfeed")
    check("settled payment still counts", core.spent_today(uid), 50.0)
    check("status is settled", core.get_payment(pid)["status"], "settled")


def t_reaping_does_not_double_count():
    """Reaping must not change the amount committed, or the daily limit would
    drift on every reaper tick."""
    core = fresh_core()
    u = core.upsert_user(117, "tester")
    core.set_policy(u["id"], daily_limit=1000.0)
    uid = u["id"]
    pid = make_payment(core, u, amount=50.0)
    core.mark(pid, "pending_approval")
    core.claim_payment_for_settlement(pid)
    before = core.spent_today(uid)

    with core.conn() as c:
        c.execute("UPDATE payments SET claimed_at = claimed_at - 600 WHERE id = ?", (pid,))
    core.reap_stale_settling_claims(300)
    check("committed amount unchanged by reaping", core.spent_today(uid), before)


def t_settled_payments_are_never_reaped():
    """The reaper must not touch a payment that actually went through."""
    core = fresh_core()
    u = core.upsert_user(115, "tester")
    pid = make_payment(core, u)
    core.mark(pid, "pending_approval")
    core.claim_payment_for_settlement(pid)
    core.mark(pid, "settled", tx="0xabc")
    with core.conn() as c:
        c.execute("UPDATE payments SET claimed_at = claimed_at - 9999 WHERE id = ?", (pid,))

    check("nothing reaped", core.reap_stale_settling_claims(300), [])
    check("still settled", core.get_payment(pid)["status"], "settled")
    check("tx preserved", core.get_payment(pid)["tx"], "0xabc")


def t_in_flight_settlement_is_not_interrupted():
    """A slow RPC must not have its payment yanked back mid-transfer."""
    core = fresh_core()
    u = core.upsert_user(116, "tester")
    pid = make_payment(core, u)
    core.mark(pid, "pending_approval")
    core.claim_payment_for_settlement(pid)
    # claimed_at is "now", so it is well inside the 300s window.
    check("young claim survives", core.reap_stale_settling_claims(300), [])
    check("still settling", core.get_payment(pid)["status"], "settling")


def t_reaper_is_started_once_and_is_idempotent():
    core = fresh_core()
    core.start_settling_reaper(interval_s=3600, max_age_s=3600)
    first = core._REAPER
    check("reaper thread is alive", first.is_alive(), True)
    check("thread is a daemon", first.daemon, True)
    core.start_settling_reaper(interval_s=3600, max_age_s=3600)
    check("starting twice does not spawn a second thread", core._REAPER is first, True)


def t_reaper_loop_survives_an_error():
    """A reaper that dies on one bad row stops recovering anything, silently."""
    import inspect
    src = inspect.getsource(core_module.reap_stale_settling_claims)
    check("reap function exists", bool(src), True)
    loop = inspect.getsource(core_module._reaper_loop)
    check("loop catches exceptions", "except Exception" in loop, True)
    check("and continues rather than returning", "while True" in loop, True)


if __name__ == "__main__":
    import core as core_module
    t_claim_is_atomic_under_concurrency()
    t_claimed_at_is_stamped()
    t_stranded_claim_holds_budget_then_is_reaped()
    t_budget_is_released()
    t_reaping_does_not_double_count()
    t_settled_payments_are_never_reaped()
    t_in_flight_settlement_is_not_interrupted()
    t_reaper_is_started_once_and_is_idempotent()
    t_reaper_loop_survives_an_error()
    print("=" * 78)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
