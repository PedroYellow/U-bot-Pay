#!/usr/bin/env bash
# Run every regression suite in this deployment. One answer.
#
#   ./run_tests.sh          # all suites
#   ./run_tests.sh policy   # only the ones matching "policy"
#
# The main deployment's runner hardcoded /opt/ubotpay/venv, so it could
# not be copied to a fork - and the three whitelabels had no runner at
# all, so "do the tests pass?" meant running thirty-nine filenames by
# hand. That is how the drift went unnoticed: a suite that had been
# pointing at another deployment's venv for weeks still "passed",
# because nobody ran the whole set in one command.
#
# So this runner is PORTABLE:
#
#   - the interpreter is THIS deployment's venv, found relative to the
#     script, not hardcoded
#   - the suites that cannot apply to this deployment live in a
#     tests-<something>-not-applicable/ directory and are not run -
#     they are listed, so they are visible rather than forgotten
#   - a suite that times out is reported as a failure, not skipped
cd "$(dirname "$0")"

# `set -o pipefail` is a bash option. Run under dash - which is what
# `sh` is on Debian - it dies with:
#
#     set: Illegal option -o pipefail
#
# which says nothing about the real problem. The runner has a bash
# shebang, so ./run_tests.sh works; only `sh run_tests.sh` breaks.
# This says so plainly.
#
# The check has to come BEFORE `set -o pipefail`, or dash dies on the
# option before it ever reaches the message.
if [ -z "${BASH_VERSION:-}" ]; then
    echo "This runner needs bash, not sh."
    echo "  run it as:  ./run_tests.sh"
    echo "  or:         bash run_tests.sh"
    exit 2
fi

set -uo pipefail

FILTER="${1:-}"
PASS=0; FAIL=0; FAILED_SUITES=""

# This deployment's own interpreter. The venv is where fastapi / httpx /
# tronpy / web3 live, and WHICH of those depends on the chain - so the
# right interpreter is the one next to this script.
if [ -x "./venv/bin/python" ]; then
  PY="./venv/bin/python"
elif [ -x "/opt/$(basename "$PWD")/venv/bin/python" ]; then
  PY="/opt/$(basename "$PWD")/venv/bin/python"
else
  PY="python3"
fi

# The suites that live in a directory rather than beside the runner
# cannot apply to this deployment. They are named, so they are visible.
echo "── not applicable here ──"
for d in tests-*/; do
  [ -d "$d" ] || continue
  n=$(ls "$d" 2>/dev/null | grep -c '^test_.*\.py$' || true)
  [ "$n" -gt 0 ] && echo "  $d ($n suites)"
done
echo

for t in test_*.py; do
  [ -e "$t" ] || continue
  [ -n "$FILTER" ] && [[ "$t" != *"$FILTER"* ]] && continue

  out=$("$PY" "$t" 2>&1)
  code=$?
  if [ $code -eq 0 ]; then
    line=$(printf '%s\n' "$out" | grep -E '^[0-9]+/[0-9]+ passed' | tail -1)
    printf '  \033[32mPASS\033[0m  %-42s %s\n' "$t" "${line:-ok}"
    PASS=$((PASS + 1))
  else
    printf '  \033[31mFAIL\033[0m  %-42s\n' "$t"
    printf '%s\n' "$out" | tail -8 | sed 's/^/          /'
    FAIL=$((FAIL + 1))
    FAILED_SUITES="$FAILED_SUITES $t"
  fi
done

echo
echo "─────────────────────────────────────────"
if [ $FAIL -eq 0 ]; then
  printf '\033[32m%d suites passed\033[0m\n' "$PASS"
  exit 0
fi
printf '\033[31m%d passed, %d failed:\033[0m%s\n' "$PASS" "$FAIL" "$FAILED_SUITES"
exit 1
