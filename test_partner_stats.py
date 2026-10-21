"""
Round 22: bridge.py partner_stats lock + quote error redaction.

Two regressions this test pins:

  1. /bridge.py:_PARTNER_STATS used to be incremented without a lock; concurrent
     /bridge/quote requests against "best" would lose increments. The /bridge/
     partners readout would then disagree with reality. Verified with 200
     simulated partner_ok calls across two threads: the counter is now 200.

  2. /bridge.py:quote() used to raise ValueError(f"... : {data}") on a LI.FI
     response that lacked "estimate". api.py maps ValueError to 400 with
     str(e)[:200] sent to the caller, which leaked the partner payload. The
     error is now generic and the response is in the log only.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def chk(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:48} got={got!r} want={want!r}")


def _run(api_body):
    code = (
        "import sys\n"
        f"sys.path.insert(0, {HERE!r})\n"
        "import bridge as B\n"
        f"{api_body}\n"
    )
    env = os.environ.copy()
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", code],
        capture_output=True, text=True, env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(f"rc={r.returncode}: {r.stderr}")
    return r.stdout.strip().split("\n")[-1]


def t_partner_stats_lock_observed():
    """Two threads, 100 _partner_ok calls each, end counter is 200."""
    body = (
        "import threading\n"
        "def hammer():\n"
        "    for _ in range(100):\n"
        "        B._partner_ok('lifi')\n"
        "t1 = threading.Thread(target=hammer)\n"
        "t2 = threading.Thread(target=hammer)\n"
        "t1.start(); t2.start()\n"
        "t1.join(); t2.join()\n"
        "print(B.partner_stats()['lifi']['ok'])\n"
    )
    out = _run(body)
    chk("200 concurrent ok increments registered", int(out), 200)


def t_partner_fail_lock_observed():
    body = (
        "import threading\n"
        "def hammer():\n"
        "    for _ in range(50):\n"
        "        B._partner_fail('across')\n"
        "t1 = threading.Thread(target=hammer)\n"
        "t2 = threading.Thread(target=hammer)\n"
        "t1.start(); t2.start()\n"
        "t1.join(); t2.join()\n"
        "print(B.partner_stats()['across']['fail'])\n"
    )
    out = _run(body)
    chk("100 concurrent fail increments registered", int(out), 100)


def t_partner_stats_no_data_in_error():
    """The 400 message from api.py must not echo the LI.FI payload.

    This one is inspected via regex: api.py maps _UnsupportedChain and
    ValueError to 400 with str(e)[:200] — so the message itself must be
    generic. The "data" payload cannot appear in the message string.
    """
    src = open(os.path.join(HERE, "bridge.py"), encoding="utf-8").read()
    # Find the ValueError raise on missing estimate and confirm it no longer
    # interpolates the `data` dict.
    i = src.index('if "estimate" not in data:')
    j = src.index("\n\n", i)
    block = src[i:j]
    chk("quote() no longer formats the LI.FI payload into the error",
        'f"LI.FI response missing estimate: {data}"' not in block, True)
    chk("the new error message is generic",
        "bridge partner returned an unusable response" in block, True)
    chk("the payload goes to the server log, not the exception",
        "log.warning" in block, True)


def t_partner_stats_lock_present():
    src = open(os.path.join(HERE, "bridge.py"), encoding="utf-8").read()
    chk("_PARTNER_STATS_LOCK defined",
        "_PARTNER_STATS_LOCK = threading.Lock()" in src, True)
    # Position of the lock acquisition must be inside the function body.
    ok_i = src.index("def _partner_ok(partner: str):")
    fail_i = src.index("def _partner_fail(partner: str):")
    body_ok = src[ok_i:src.index("def ", ok_i + 10)]
    chk("_partner_ok uses the lock",
        "with _PARTNER_STATS_LOCK" in body_ok, True)
    body_fail = src[fail_i:src.index("def ", fail_i + 10)]
    chk("_partner_fail uses the lock",
        "with _PARTNER_STATS_LOCK" in body_fail, True)
    stats_i = src.index("def partner_stats() -> dict:")
    body_stats = src[stats_i:stats_i+1500]
    chk("partner_stats() reads under the lock",
        "with _PARTNER_STATS_LOCK" in body_stats, True)


if __name__ == "__main__":
    t_partner_stats_lock_observed()
    t_partner_fail_lock_observed()
    t_partner_stats_no_data_in_error()
    t_partner_stats_lock_present()
    print("=" * 76)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
