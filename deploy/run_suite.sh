#!/bin/sh
# Run a deployment's test suite and report the tally.
#
# Usage: run_suite.sh <deployment-dir> <venv-python> [timeout-seconds]
#
# THE TIMEOUT MUST BE LARGER THAN THE TESTS' OWN
#
# The tests that spawn a subprocess give it 300 seconds
# (SUBPROCESS_TIMEOUT). Those subprocesses import the application, and
# on a loaded host the import alone takes 78 seconds - so a test with
# five of them needs about 390 seconds.
#
# So the per-suite timeout here has to be LARGER than 300, or the
# outer timeout kills the test before its own timeout can report what
# happened. That is not a hypothetical: with a 200-second outer
# timeout, test_nonce_db_atomic.py and test_seed_for_idempotent.py
# were reported as FAILING - with an empty log - while the venv they
# ran in was perfectly fine.
#
# The failure looked exactly like a broken test and it was not one.
# Nothing in the log said so, because there was no log: the outer
# timeout killed the process before it could print anything.
#
# So: the default is 900, comfortably above the worst case. And a
# suite that IS genuinely broken still fails - the runner reports the
# exit code, not the timeout.
#
# The runner in the deployment - run_tests.sh - has NO per-suite
# timeout at all, on purpose. The tests' own timeouts are the
# control, and one timeout in one place is easier to reason about than
# two.
DEPLOY="$1"
PY="$2"
TMO="${3:-900}"

if [ -z "$DEPLOY" ] || [ -z "$PY" ]; then
    echo "usage: $0 <deployment-dir> <venv-python> [timeout-seconds]" >&2
    exit 2
fi

cd "$DEPLOY" || exit 1

P=0
F=0
for t in test_*.py; do
    [ -e "$t" ] || continue
    printf '  %-42s ' "$t"
    timeout "$TMO" "$PY" "$t" >/tmp/_suite_one.log 2>&1
    rc=$?
    if [ $rc -eq 0 ]; then
        echo "PASS"
        P=$((P + 1))
    else
        if [ $rc -eq 124 ]; then
            # 124 is the timeout's own exit code. Say so, because
            # "FAIL" with an empty log is what made this look like a
            # broken test.
            echo "TIMEOUT after ${TMO}s"
        else
            echo "FAIL"
        fi
        F=$((F + 1))
        grep -E '^FAIL|Error' /tmp/_suite_one.log 2>/dev/null | head -2 | sed 's/^/      /'
    fi
done
echo "  --> $P passed, $F failed"
[ "$F" -eq 0 ]
