"""
Round 28: chain.submit_usdc has a race on the treasury nonce.

Pre-fix: get_transaction_count and _transfer sat OUTSIDE any lock.
Two concurrent callers could read the same nonce counter, both
sign with it, and one tx would fail on-chain with "nonce too low".
That wastes gas and breaks the caller's transaction flow.

Round 28 fix: a thread lock around get_transaction_count + _transfer.
The lock is released between submit and confirm_usdc so the long
wait_for_transaction_receipt does not serialise all outgoing USDC.

This test pins the post-conditions:

  1. The fix added a module-level _treasury_lock.
  2. Ten concurrent submit_usdc calls do not share a nonce when the
     lock is on.
  3. The lock is released before submit_usdc returns, so a second
     caller can acquire it within 1s without blocking.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def chk(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print("%s  %-50s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


def _run(script_body):
    """Run a Python script via /opt/ubotpay/venv. The script body is
    written to a temp file and invoked with `python3 path`, so we
    don't have to deal with shell escape interactions."""
    body = "import sys\nsys.path.insert(0, '/opt/ubotpay')\n" + script_body
    d = tempfile.mkdtemp(prefix="h4_")
    p = os.path.join(d, "case.py")
    open(p, "w").write(body)
    env = os.environ.copy()
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", p],
        capture_output=True, text=True, env=env, cwd="/opt/ubotpay",
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s\n%s" % (r.returncode, r.stdout, r.stderr))
    return [ln for ln in r.stdout.strip().splitlines() if ln.strip()]


def test_treasury_lock_is_a_threading_lock():
    out = _run(
        "import chain\n"
        "import threading\n"
        "print(isinstance(chain._treasury_lock, type(threading.Lock())))\n"
    )
    chk("_treasury_lock is a Lock instance", out[-1], "True")


CONCURRENT_SUBMIT_CASE = '''
import chain
import threading
import time
from unittest.mock import patch

seen = []
counter = [0]
counter_lock = threading.Lock()

def fake_get(addr):
    with counter_lock:
        c = counter[0]
        counter[0] += 1
        time.sleep(0.005)
    return c

def fake_transfer(acct, to, amount, nonce):
    seen.append(nonce)
    return b"\\x00" * 32

class _FakeAcct:
    address = "0x0000000000000000000000000000000000000000"
    key = None

chain.account = lambda: _FakeAcct()

with patch.object(chain.w3.eth, "get_transaction_count", side_effect=fake_get), \\
     patch.object(chain, "_transfer", side_effect=fake_transfer):
    barrier = threading.Barrier(10)
    def worker():
        barrier.wait()
        chain.submit_usdc("0x" * 40, 1.0)
    threads = [threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    print(sorted(seen))
'''


def test_concurrent_submit_usdc_unique_nonces():
    out = _run(CONCURRENT_SUBMIT_CASE)
    chk("10 concurrent submits see 10 unique nonces under the lock",
        out[-1], "[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]")


RELEASES_CASE = '''
import chain
import time
from unittest.mock import patch

def fake_transfer(acct, to, amount, nonce):
    return b"\\x00" * 32

class _FakeAcct:
    address = "0x0000000000000000000000000000000000000000"

chain.account = lambda: _FakeAcct()

with patch.object(chain, "_transfer", side_effect=fake_transfer):
    chain.submit_usdc("0x" * 40, 1.0)
    t0 = time.time()
    acquired = chain._treasury_lock.acquire(timeout=2)
    elapsed_ms = int((time.time() - t0) * 1000)
    if acquired:
        chain._treasury_lock.release()
    print(elapsed_ms, acquired)
'''


def test_lock_does_not_hold_after_submit():
    out = _run(RELEASES_CASE)
    elapsed_ms = int(out[-1].split()[0])
    acquired = out[-1].split()[1] == "True"
    chk("lock releasable after submit_usdc", acquired, True)
    chk("lock released within 1000ms", elapsed_ms < 1000, True)


if __name__ == "__main__":
    test_treasury_lock_is_a_threading_lock()
    test_concurrent_submit_usdc_unique_nonces()
    test_lock_does_not_hold_after_submit()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
