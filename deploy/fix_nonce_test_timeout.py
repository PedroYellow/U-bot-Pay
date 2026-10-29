#!/usr/bin/env python3
"""Give the nonce test's subprocesses a timeout.

The bug

    subprocess.run([...], capture_output=True, text=True, env=env)

with NO timeout. Each of those spawns a fresh Python that imports
dataagent, which imports web3. On a loaded host that import alone
takes 78 seconds - measured - so five cases means six-plus minutes,
and with no timeout it is not six-plus minutes, it is FOREVER.

That is what made the test look like a failure:

    FAIL test_nonce_db_atomic.py

It was not failing. It was still running when the outer 280s timeout
killed it, and the runner recorded the kill as a failure.

The fix

Give every subprocess a timeout, and treat a timeout as a FAILURE with
a clear message rather than an unexplained hang. The timeout has to be
generous - the import alone is 78s under load - but it has to exist,
so a genuinely stuck subprocess is reported instead of hanging the
suite.

Usage: fix_nonce_test_timeout.py <test.py>
"""
import re
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/data/workspace/ubotpay/test_nonce_db_atomic.py"

# A generous timeout: the import of dataagent is 78s on a loaded host,
# and each case spawns one.
TIMEOUT = 300

OLD_RUN = """    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", full],
        capture_output=True, text=True, env=env,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s\\n%s" % (r.returncode, r.stdout, r.stderr))
    out = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    return out[-1] if out else \"\""""

NEW_RUN = """    # The timeout is the point of this change. Without it a stuck
    # subprocess hangs the whole suite, and the runner records the
    # eventual kill as a FAIL with no reason. Each case spawns a fresh
    # Python that imports dataagent - which imports web3 - and on a
    # loaded host that import alone takes 78 seconds.
    try:
        r = subprocess.run(
            ["/opt/ubotpay/venv/bin/python", "-c", full],
            capture_output=True, text=True, env=env,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "the case did not finish in %ss - it is hung, not wrong"
            % SUBPROCESS_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s\\n%s" % (r.returncode, r.stdout, r.stderr))
    out = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    return out[-1] if out else \"\""""

OLD_RUN2 = """        r = subprocess.run(
            ["/opt/ubotpay/venv/bin/python", "-c", full],
            capture_output=True, text=True, env=env,
        )
        if r.returncode != 0:
            raise RuntimeError("rc=%d: %s\\n%s" % (r.returncode, r.stdout, r.stderr))
        return r.stdout.strip().splitlines()[-1]"""

NEW_RUN2 = """        # The timeout is the point of this change - see _run().
        try:
            r = subprocess.run(
                ["/opt/ubotpay/venv/bin/python", "-c", full],
                capture_output=True, text=True, env=env,
                timeout=SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "the case did not finish in %ss - it is hung, not wrong"
                % SUBPROCESS_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError("rc=%d: %s\\n%s" % (r.returncode, r.stdout, r.stderr))
        return r.stdout.strip().splitlines()[-1]"""


def main():
    src = open(PATH, encoding="utf-8").read()
    n = 0

    if OLD_RUN in src:
        src = src.replace(OLD_RUN, NEW_RUN, 1)
        n += 1
        print("  _run() now has a timeout")
    else:
        print("  _run() was not found in the expected form")

    if OLD_RUN2 in src:
        src = src.replace(OLD_RUN2, NEW_RUN2, 1)
        n += 1
        print("  run_case() now has a timeout")
    else:
        print("  run_case() was not found in the expected form")

    # the constant, next to the imports
    if "SUBPROCESS_TIMEOUT" in src and "SUBPROCESS_TIMEOUT =" not in src:
        src = src.replace(
            "import subprocess",
            """import subprocess

# Each case spawns a fresh Python that imports dataagent, which
# imports web3. On a loaded host that import alone takes 78 seconds -
# measured. So the timeout is generous, but it has to EXIST: without
# it a stuck subprocess hangs the suite, and the runner records the
# eventual kill as a FAIL with no reason.
SUBPROCESS_TIMEOUT = 300""", 1)
        n += 1
        print("  the timeout constant was added")

    open(PATH, "w", encoding="utf-8").write(src)

    try:
        compile(src, PATH, "exec")
        print(f"{PATH}: {n} change(s), it parses")
    except SyntaxError as e:
        print(f"{PATH}: {n} change(s) but it does NOT parse: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
