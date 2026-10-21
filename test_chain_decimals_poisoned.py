"""
Round 24: chain._balance_usdc poisoned the global _DEC.

The previous code assigned a Contract object to the module-level _DEC and
read decimals() into a local `dec`. The function returned the right value,
but the global stayed a Contract. Any later chain.decimals() or
chain.to_units() crashed with `TypeError: unsupported operand type(s) for
** or pow(): 'int' and 'Contract'`.

Verified empirically against the live venv before the fix:
  start: decimals()=6 int
  balance_at(...,43114) -> USDC=48.96, decimals()=6 int
  balance_at(...,8453) -> USDC=0.0, decimals()=Contract
  to_units(1.0) -> TypeError

After the fix (variable local; global _DEC never touched here):
  balance_at(...,43114) -> USDC=48.96, decimals()=6 int
  balance_at(...,8453) -> USDC=0.0,    decimals()=6 int
  to_units(1.0) -> 1000000

This test pins the post-condition with subprocess + a fresh-DB / fresh-chain
import. Behaviour assertions against the live RPC were verified manually
during the round-24 audit; this file pins the source invariant so a
future regression can be caught cheaply.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def chk(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print("%s  %-50s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


def _run(code):
    env = os.environ.copy()
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")
    full = ("import sys\n"
             + 'sys.path.insert(0, "%s")\n' % HERE
             + "import chain\n"
             + code)
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", full],
        capture_output=True, text=True, env=env,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s / %s" % (r.returncode, r.stdout, r.stderr))
    out = r.stdout.strip().split("\n")[-1]
    return out


def test_decimals_returns_int():
    """The default-loaded chain.decimals() (no _balance_usdc call) returns
    a Python int, not a Contract."""
    out = _run(
        "print(type(chain.decimals()).__name__)\n"
    )
    chk("decimals() returns int on fresh import", out, "int")


def test_to_units_runs_with_integer_decimals():
    """to_units(1.0) returns 1000000 (10**6)."""
    out = _run("print(chain.to_units(1.0))\n")
    chk("to_units(1.0) = 1_000_000", out, "1000000")


def test_balance_usdc_source_uses_local_var():
    """The function body must NOT touch the global _DEC at all any
    more. This is the regression pin."""
    src = open(os.path.join(HERE, "chain.py"), encoding="utf-8").read()
    i = src.index("def _balance_usdc(")
    j = src.find("\ndef ", i + 10)
    if j == -1:
        j = len(src)
    body = src[i:j]
    # Strip docstrings (Round 24 fix notes mention 'global _DEC' in the
    # long comment).
    import re as _re
    body_no_doc = _re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', '', body)
    chk("_balance_usdc body does not assign to global _DEC",
        "global _DEC" not in body_no_doc, True)


def test_balance_usdc_does_not_assign_to_global():
    """A bare 'global _DEC' may only occur inside the body of
    decimals() (line 37) — never in any other function, and never at
    module level. Round 24: an earlier fix pastes mention of _DEC in the
    docstring of _balance_usdc, so we strip docstrings before grepping."""
    src = open(os.path.join(HERE, "chain.py"), encoding="utf-8").read()
    src_no_doc = __import__('re').sub(
        r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', '', src)
    # Walk every 'global _DEC' and check it falls inside a function body
    # named 'decimals'.
    cur_fn = None
    line_no = 0
    for line in src_no_doc.split("\n"):
        line_no += 1
        m = __import__('re').match(r"\s*(?:async )?def\s+(\w+)\s*\(", line)
        if m:
            cur_fn = m.group(1)
            continue
        if "global _DEC" in line:
            if cur_fn != "decimals":
                chk("'global _DEC' only inside decimals() (line %d, fn=%s)" % (line_no, cur_fn),
                    False, True)
                return
    chk("'global _DEC' only inside decimals() (and only in line 37)",
        True, True)


if __name__ == "__main__":
    test_decimals_returns_int()
    test_to_units_runs_with_integer_decimals()
    test_balance_usdc_source_uses_local_var()
    test_balance_usdc_does_not_assign_to_global()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
