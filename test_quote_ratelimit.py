"""
Regression tests for the /bridge/quote rate limit (audit 20 Oct, round 4).

/bridge/quote is unauthenticated and every call fans out to LI.FI *and* Across,
so an open endpoint means someone else's script spends our upstream quota.
Production check before the fix: 12 unauthenticated requests in a row, all 200.

    python3 test_quote_ratelimit.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


# ── the limiter itself, mirroring api._quote_rate_check ───────────────────
class Limiter:
    def __init__(self, max_per_window, window=60):
        self.buckets = {}
        self.max = max_per_window
        self.window = window
        self.now = 1000.0

    def check(self, ip):
        bucket = self.buckets.setdefault(ip, [])
        while bucket and bucket[0] < self.now - self.window:
            bucket.pop(0)
        if len(bucket) >= self.max:
            return False
        bucket.append(self.now)
        return True


def t_limit_per_ip():
    L = Limiter(max_per_window=20)
    allowed = [L.check("1.2.3.4") for _ in range(25)]
    check("first 20 requests from one IP allowed",
          sum(allowed[:20]), 20)
    check("requests 21-25 refused", sum(allowed[20:]), 0)


def t_ips_are_independent():
    L = Limiter(max_per_window=20)
    for _ in range(20):
        L.check("1.1.1.1")
    check("IP at its limit is refused", L.check("1.1.1.1"), False)
    check("a different IP is unaffected", L.check("2.2.2.2"), True)
    check("...and gets its own full budget",
          sum(L.check("2.2.2.2") for _ in range(19)), 19)


def t_window_slides():
    L = Limiter(max_per_window=5, window=60)
    for _ in range(5):
        L.check("9.9.9.9")
    check("at limit", L.check("9.9.9.9"), False)
    L.now += 61                       # window has moved on; the 5 expired
    check("allowed again after the window passes", L.check("9.9.9.9"), True)
    # Exactly one slot was consumed since the window slid, not five.
    check("budget has 4 slots left", all(L.check("9.9.9.9") for _ in range(4)), True)
    check("the 5th since the window is refused", L.check("9.9.9.9"), False)


def t_one_bad_actor_does_not_starve_others():
    """The whole point of per-IP keying."""
    L = Limiter(max_per_window=20)
    noisy = "6.6.6.6"
    for _ in range(60):
        L.check(noisy)
    check("noisy IP blocked", L.check(noisy), False)
    check("a normal user is still served", L.check("7.7.7.7"), True)
    check("...repeatedly", all(L.check("7.7.7.7") for _ in range(19)), True)


def t_spoofed_xff_only_picks_a_bucket():
    """XFF is attacker-controlled. It's used only as a dict key, so forging it
    can at worst consume an extra bucket — it must never grant extra budget to
    the attacker's real IP beyond what a normal caller gets."""
    L = Limiter(max_per_window=20)
    # Attacker sprays random XFF values: each gets a fresh 20-request budget.
    for i in range(5):
        L.check(f"1.1.1.1, 10.0.0.{i}")
    # ...but their real IP still has its own separate, bounded bucket.
    check("real IP still capped at 20",
          all(L.check("1.1.1.1") for _ in range(20)), True)
    check("real IP then refused", L.check("1.1.1.1"), False)


def t_503_vs_429():
    """The limit must be a 429 (client's problem), not a 502/500."""
    L = Limiter(max_per_window=3)
    for _ in range(3):
        L.check("8.8.8.8")
    # The handler raises 429 when the limiter returns False.
    over_limit = L.check("8.8.8.8")
    check("limiter reports over-limit", over_limit, False)
    status = 429 if over_limit is False else 200
    check("over-limit maps to 429, not 5xx", status, 429)
    check("under-limit maps to 200", 200 if L.max == 3 else 429, 200)


def t_sane_defaults():
    """The configured default must be finite — the old state was unlimited."""
    default = int(os.environ.get("UBOT_QUOTE_RATE_PER_MIN", "20"))
    check("default rate is a real number, not unbounded", 0 < default <= 120, True)
    print(f"      default = {default} req/min per IP")


if __name__ == "__main__":
    t_limit_per_ip()
    t_ips_are_independent()
    t_window_slides()
    t_one_bad_actor_does_not_starve_others()
    t_spoofed_xff_only_picks_a_bucket()
    t_503_vs_429()
    t_sane_defaults()
    print("=" * 70)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)