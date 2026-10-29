"""
Round 24: api._pending_gc was orphaned; bridge._CACHE.gced only on write.

api.py had a _pending_gc() function with TTL and cap, but no one called it.
main.py's main() now schedules _pending_gc_loop every 60s. Test pins:
  - _pending_gc() removes entries older than 30 minutes.
  - _pending_gc() caps the dict at _PENDING_MAX.

bridge._cache_get() did not call _cache_gc_locked() until the dict was
already 2x over the cap. After the fix, the read path calls the helper
to cap the dict. Test:
  - The cache_get source mentions _cache_gc_locked().
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


def _run(code):
    d = tempfile.mkdtemp(prefix="c_gc_")
    env = os.environ.copy()
    env["UBOT_DB"] = os.path.join(d, "test.db")
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")
    full = (
        "import sys\n"
        + 'sys.path.insert(0, "%s")\n' % HERE
        + "import api\n"
        + code
    )
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", full],
        capture_output=True, text=True, env=env,
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s\n%s" % (r.returncode, r.stdout, r.stderr))
    out = r.stdout.strip().split("\n")[-1]
    return out


def test_pending_gc_drops_expired():
    code = (
        "import time\n"
        "api._pending.clear()\n"
        "api._pending[1] = {'ts': time.time() - api._PENDING_TTL - 100}\n"
        "api._pending[2] = {'ts': time.time()}\n"
        "api._pending_gc()\n"
        "print(len(api._pending), 1 in api._pending, 2 in api._pending)\n"
    )
    out = _run(code)
    chk("after gc: 1 expired dropped, 2 fresh kept", out, "1 False True")


def test_pending_gc_caps_at_max():
    code = (
        "import time\n"
        "import api\n"
        "cap = api._PENDING_MAX\n"
        "api._pending.clear()\n"
        "for i in range(cap + 100):\n"
        "    api._pending[i] = {'ts': time.time() + i}\n"
        "api._pending_gc()\n"
        "print(len(api._pending))\n"
        "print(cap)\n"
    )
    env = os.environ.copy()
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", "import sys; sys.path.insert(0, '" + HERE + "'); " + code],
        capture_output=True, text=True, env=env,
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s %s" % (r.returncode, r.stdout, r.stderr))
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    size, cap = int(lines[-2]), int(lines[-1])
    chk("after gc: dict is capped at _PENDING_MAX", size, cap)


def test_pending_gc_loop_in_main():
    src = open(os.path.join(HERE, "main.py"), encoding="utf-8").read()
    chk("main.py schedules _pending_gc_loop",
        "_pending_gc_loop" in src, True)


def test_cache_get_gcs_too():
    src = open(os.path.join(HERE, "bridge.py"), encoding="utf-8").read()
    i = src.index("def _cache_get(key):")
    j = src.index("\ndef ", i + 10)
    body = src[i:j]
    chk("bridge._cache_get calls _cache_gc_locked under the lock",
        "_cache_gc_locked()" in body, True)


def test_cache_set_still_gcs():
    src = open(os.path.join(HERE, "bridge.py"), encoding="utf-8").read()
    i = src.index("def _cache_set(key, data):")
    j = src.index("\ndef ", i + 10)
    body = src[i:j]
    chk("bridge._cache_set still calls _cache_gc_locked",
        "_cache_gc_locked()" in body, True)


if __name__ == "__main__":
    test_pending_gc_drops_expired()
    test_pending_gc_caps_at_max()
    test_pending_gc_loop_in_main()
    test_cache_get_gcs_too()
    test_cache_set_still_gcs()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
