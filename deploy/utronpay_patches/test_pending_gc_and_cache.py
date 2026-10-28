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

# The environment the service uses. The suite runs code in a
# subprocess, so the deployment's secrets have to be in the environment
# BEFORE the first subprocess starts - api.py refuses to import without
# them.
#
# The file is this deployment's own .env, and the loader uses
# setdefault, so a variable that is already in the environment wins.
ENV_FILE = os.environ.get(
    "UTRONPAY_ENV_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
if os.path.exists(ENV_FILE):
    for line in open(ENV_FILE):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip('"').strip("'"))
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# This deployment's env prefix, from its own .env.
_DEPLOY_PREFIX = os.environ.get("DEPLOY_PREFIX", "UTRON").strip().upper()
RESULTS = []


def chk(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print("%s  %-50s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


def _run(code):
    d = tempfile.mkdtemp(prefix="c_gc_")
    env = os.environ.copy()
    env["UBOT_DB"] = os.path.join(d, "test.db")
    # This deployment's own variable names. The test set the MAIN
    # deployment's - UBOT_API_KEY - but the deployment's api.py asks
    # for its own prefix, so the subprocess started without the
    # variable the module wanted.
    env.setdefault(f"{_DEPLOY_PREFIX}_API_KEY", "x")
    env.setdefault(f"{_DEPLOY_PREFIX}_JWT_SECRET", "x")
    full = (
        "import _env_shim\n"
        "import sys\n"
        + 'sys.path.insert(0, "%s")\n' % HERE
        + "import api\n"
        + code
    )
    r = subprocess.run(
        [os.path.join(HERE, "venv", "bin", "python"), "-c", full],
        capture_output=True, text=True, env=env,
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
        [os.path.join(HERE, "venv", "bin", "python"), "-c", "import _env_shim\nimport sys; sys.path.insert(0, '" + HERE + "'); " + code],
        capture_output=True, text=True, env=env,
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
