#!/usr/bin/env bash
# Run every regression suite. No network, no chain, no API keys.
#
# Audit 20 Oct round 9: the audit work produced eleven standalone test_*.py
# scripts with no way to run them together, so "do the tests pass?" meant
# remembering eleven filenames. This runs them all and gives one answer.
#
#   ./run_tests.sh          # all suites
#   ./run_tests.sh policy   # only the ones matching "policy"
set -uo pipefail
cd "$(dirname "$0")"

FILTER="${1:-}"
PASS=0; FAIL=0; FAILED_SUITES=""

for t in test_*.py; do
  # test_brain.py is the original 15 Oct script. It reads /opt/ubotpay/.env at
  # import time, so it only runs on the VPS and dies everywhere else. It is
  # superseded by test_brain_fallback.py, which is portable and covers more.
  [ "$t" = "test_brain.py" ] && continue
  [ -n "$FILTER" ] && [[ "$t" != *"$FILTER"* ]] && continue
  out=$(python3 "$t" 2>&1)
  code=$?
  if [ $code -eq 0 ]; then
    line=$(printf '%s\n' "$out" | grep -E '^[0-9]+/[0-9]+ passed' | tail -1)
    printf '  \033[32mPASS\033[0m  %-28s %s\n' "$t" "${line:-ok}"
    PASS=$((PASS + 1))
  else
    printf '  \033[31mFAIL\033[0m  %-28s\n' "$t"
    printf '%s\n' "$out" | sed 's/^/          /'
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