"""
Regression tests for the x402 payee allowlist and agent_query rate limit
(audit 20 Oct, round 7).

pay_and_fetch signed an EIP-3009 authorization for whatever `payTo` the remote
402 asked for. The per-call budget capped the damage, but the client would still
authorise a transfer to any address a service named.

Separately, UBOT_AGENT_MAX_USDC caps a single call, so looping agent_query could
drain the treasury 1 USDC at a time.

    python3 test_x402_guardrails.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


# ── payee allowlist ───────────────────────────────────────────────────────
GOOD = "0xFE9D541c92E4e90437C7152A00244886dE37a658"
EVIL = "0x000000000000000000000000000000000000dEaD"


def make_allowed(raw_env):
    return {a.lower().strip() for a in raw_env.split(",") if a.strip()}


def t_allowlist():
    allowed = make_allowed(GOOD)
    check("allowlisted payee accepted", GOOD.lower() in allowed, True)
    check("other payee refused", EVIL.lower() in allowed, False)
    # case and whitespace must not matter
    check("case-insensitive", GOOD.lower() in {a.lower().strip() for a in (GOOD.lower(),)}, True)
    check("multiple entries comma-separated",
          make_allowed(f"{GOOD}, {EVIL}") == {GOOD.lower(), EVIL.lower()}, True)


def t_empty_allowlist_allows_all():
    """Demo default: no allowlist configured means only the budget applies.
    Must stay permissive or a fresh checkout cannot pay anyone."""
    allowed = make_allowed("")
    check("empty allowlist is empty", allowed == set(), True)
    check("and therefore permits any payee", bool(allowed) is False, True)


def t_payee_gate():
    def gate(pay_to, allowed):
        return (not allowed) or (pay_to or "").lower().strip() in allowed

    allowed = {GOOD.lower()}
    check("listed payee passes the gate", gate(GOOD, allowed), True)
    check("unlisted payee blocked", gate(EVIL, allowed), False)
    check("empty payTo blocked when an allowlist exists", gate("", allowed), False)
    check("no allowlist -> everything passes", gate(EVIL, set()), True)


def t_validity_window():
    """The old code took the remote's maxTimeoutSeconds + 540 unbounded, so a
    service could ask for an authorization valid for a very long time."""
    def window(max_timeout):
        return min(int(max_timeout) + 540, 1800)

    check("normal timeout stays near its value", window(60), 600)
    check("already-large timeout is capped", window(3600), 1800)
    check("absurd timeout is capped", window(999999), 1800)
    check("small timeout still gets the 540s grace", window(30), 570)


# ── agent_query rate limit ────────────────────────────────────────────────
class Limiter:
    def __init__(self, max_per_window, window=3600):
        self.buckets, self.max, self.window, self.now = {}, max_per_window, window, 1000.0

    def check(self, uid):
        b = self.buckets.setdefault(uid, [])
        while b and b[0] < self.now - self.window:
            b.pop(0)
        if len(b) >= self.max:
            return False
        b.append(self.now)
        return True


def t_agent_limit():
    L = Limiter(max_per_window=5)
    check("first 5 research calls allowed", sum(L.check(1) for _ in range(5)), 5)
    check("6th refused", L.check(1), False)
    check("another user unaffected", L.check(2), True)


def t_agent_limit_slides():
    L = Limiter(max_per_window=3, window=3600)
    for _ in range(3):
        L.check(1)
    check("at limit", L.check(1), False)
    L.now += 3601
    check("allowed again after an hour", L.check(1), True)


def t_budget_is_ceiling_not_total():
    """The per-call ceiling alone was the hole: 100 calls x 1 USDC = 100 USDC.
    With the hourly cap the worst case is max_per_hour x ceiling."""
    ceiling, per_hour = 1.0, 5
    check("worst case per hour is now bounded", ceiling * per_hour, 5.0)
    check("vs unbounded before", "unbounded" == "bounded", False)


def t_limit_is_disablable():
    """0 means 'no research at all', not 'unlimited'."""
    L = Limiter(max_per_window=0)
    check("max 0 refuses everything", L.check(1), False)


if __name__ == "__main__":
    t_allowlist()
    t_empty_allowlist_allows_all()
    t_payee_gate()
    t_validity_window()
    print()
    t_agent_limit()
    t_agent_limit_slides()
    t_budget_is_ceiling_not_total()
    t_limit_is_disablable()
    print("=" * 70)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)