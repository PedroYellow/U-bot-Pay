"""
Round 23: consume_link_code — column fix + TOCTOU.

Pre-fix this function looked at the wrong column (`id` instead of `code`,
which is the PK) so every call raised `OperationalError: no such column:
id`. After the column fix the function still had a TOCTOU window between
SELECT used_at IS NULL and the UPDATE: two concurrent callers could both
pass the SELECT and both call UPDATE, so each got a row back and the last
stamping won. The fix is to claim the row with a CAS, atomic
UPDATE ... WHERE used_at IS NULL AND expires_at >= ?, and only return
the user when rowcount == 1.
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
    d = tempfile.mkdtemp(prefix="link_")
    env = os.environ.copy()
    env["UBOT_DB"] = os.path.join(d, "test.db")
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")
    full = (
        "import sys, json\n"
        + 'sys.path.insert(0, "%s")\n' % HERE
        + "import core\n"
        + "core.init()\n"
        + code
    )
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", full],
        capture_output=True, text=True, env=env,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s" % (r.returncode, r.stderr))
    out = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return out[-1] if out else ""


def t_round_trip():
    out = _run(
        "u = core.upsert_user(0, 'tester')\n"
        "code = core.create_link_code(u['id'])\n"
        "got = core.consume_link_code(code, 12345)\n"
        "print(bool(got))\n"
    )
    chk("consume_link_code returns the user", out, "True")


def t_cannot_double_consume():
    out = _run(
        "u = core.upsert_user(0, 'tester')\n"
        "code = core.create_link_code(u['id'])\n"
        "_ = core.consume_link_code(code, 12345)\n"
        "again = core.consume_link_code(code, 99999)\n"
        "print(again is None)\n"
    )
    chk("second consume returns None", out, "True")


def t_unknown_code():
    out = _run(
        "res = core.consume_link_code('9999999', 1)\n"
        "print(res is None)\n"
    )
    chk("unknown code returns None", out, "True")


def t_id_not_in_table():
    out = _run(
        "with core.conn() as c:\n"
        "    cols = [r['name'] for r in c.execute(\"PRAGMA table_info('link_codes')\")]\n"
        "print('id' in cols)\n"
    )
    chk("link_codes has no `id` column", out, "False")


def t_select_uses_code_column():
    """The SELECT statement must be by `code`, not `id`."""
    src = open(os.path.join(HERE, "core.py")).read()
    has_bad = "SELECT id, user_id" in src and "FROM link_codes" in src
    chk("no SELECT id FROM link_codes in core.py", not has_bad, True)


def t_expired_returns_none():
    out = _run(
        "u = core.upsert_user(0, 'tester')\n"
        "code = core.create_link_code(u['id'], ttl_seconds=-10)\n"
        "res = core.consume_link_code(code, 12345)\n"
        "print(res is None)\n"
    )
    chk("expired code returns None", out, "True")


def t_toctou_concurrent_only_one_wins():
    """Spawn 50 threads racing on the same code. Exactly one returns the
    user. Without the CAS the loop would let everyone through."""
    python_script = (
        "import sys, os, tempfile, threading\n"
        + 'sys.path.insert(0, "%s")\n' % HERE
        + 'os.environ["UBOT_DB"] = tempfile.mkdtemp(prefix="t23_") + os.sep + "x.db"\n'
        + "import core\n"
        + "core.init()\n"
        + "u = core.upsert_user(0, 'tester')\n"
        + "code = core.create_link_code(u['id'])\n"
        + "results = []\n"
        + "barrier = threading.Barrier(50)\n"
        + "def go():\n"
        + "    barrier.wait()\n"
        + "    r = core.consume_link_code(code, threading.get_ident())\n"
        + "    results.append(r is not None)\n"
        + "threads = [threading.Thread(target=go) for _ in range(50)]\n"
        + "[t.start() for t in threads]\n"
        + "[t.join() for t in threads]\n"
        + "print(sum(1 for w in results if w))\n"
    )
    out = _run(python_script)
    chk("50 concurrent consumers -> exactly 1 winner", int(out), 1)


if __name__ == "__main__":
    t_round_trip()
    t_cannot_double_consume()
    t_unknown_code()
    t_id_not_in_table()
    t_select_uses_code_column()
    t_expired_returns_none()
    t_toctou_concurrent_only_one_wins()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
