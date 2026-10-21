"""
Regression test for the energy-fee policy bypass (audit 20 Oct round 20).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:54} got={got!r} want={want!r}")


def handler_source(name):
    src = open(os.path.join(HERE, "api.py"), encoding="utf-8").read()
    i = src.index("async def " + name + "(")
    j = src.index("\n@app.", i)
    return src[i:j]


def t_check_policy_decides_for_the_energy_fee():
    """The check_policy function applies the daily limit and the dual threshold.
    If it does not, the energy fee would be a free side door past the guard
    every other transfer goes through."""
    import core as _core  # noqa: E402 — imports it
    import tempfile
    db_dir = tempfile.mkdtemp(prefix="ep_")
    os.environ["UBOT_DB"] = os.path.join(db_dir, "test.db")
    _core.init()
    u = _core.upsert_user(0, "t")
    _core.set_policy(u["id"], daily_limit=40.0, dual_threshold=10.0)

    small = _core.check_policy(u, 5.0)
    check("a small fee is allowed", small["allowed"], True)
    check("does not need approval", small["needs_approval"], False)
    check("daily_limit is reported", small["daily_limit"], 40.0)

    above_threshold = _core.check_policy(u, 12.0)
    check("above dual threshold: needs_approval", above_threshold["needs_approval"], True)

    over_cap = _core.check_policy(u, 999.0)
    check("above daily cap: rejected", over_cap["allowed"], False)
    check("reason mentions the limit", "exceeded" in over_cap["reason"], True)


def t_endpoint_calls_check_policy():
    body = handler_source("energy_order")
    check("energy_order calls check_policy",
          "core.check_policy(user, fee)" in body, True)
    check("and the result gates payment creation",
          body.index("core.check_policy(") < body.index("core.create_payment("), True)
    check("needs_approval is forwarded from the policy",
          "needs_approval" in body, True)
    check("a blocked fee returns 402", "HTTPException(402" in body, True)


def t_endpoint_does_not_force_needs_approval_false():
    body = handler_source("energy_order")
    check("the hardcoded False is gone", '"energy", False)' not in body, True)
    check("the policy value is forwarded", "needs_approval" in body, True)


if __name__ == "__main__":
    t_check_policy_decides_for_the_energy_fee()
    t_endpoint_calls_check_policy()
    t_endpoint_does_not_force_needs_approval_false()
    print("=" * 76)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
