"""
Round 23: tg.py:on_start used to overwrite users.address with the demo wallet on
every /start, overwrite mom/vietsupply/... contacts, and overwrite the user's
own daily_limit and dual_threshold with demo defaults. The fix makes
_seed_for idempotent and on_start passes address=None.
"""
import json
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
    """Run a Python code fragment in a fresh DB under /opt/ubotpay/venv."""
    d = tempfile.mkdtemp(prefix="r23_")
    env = os.environ.copy()
    env["UBOT_DB"] = os.path.join(d, "test.db")
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")
    init = (
        "import sys, json\n"
        + 'sys.path.insert(0, "' + HERE + '")\n'
        + "import core, api, tg\n"
        + "core.init()\n"
    )
    full = init + code
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", full],
        capture_output=True, text=True, env=env,
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s" % (r.returncode, r.stderr))
    out = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return out[-1] if out else ""


CODE_FIRST = (
    "u = core.upsert_user(0, 'Demo User', 'en', None)\n"
    "api._seed_for(u['id'])\n"
    "print(u['address'])\n"
)


def t_first_start_does_not_set_address():
    out = _run(CODE_FIRST)
    chk("first /start leaves address=None", out, "None")


CODE_KEEP_ADDR = (
    "u = core.upsert_user(0, 'Real User', 'en', None)\n"
    "api._seed_for(u['id'])\n"
    "with core.conn() as c:\n"
    "    c.execute('UPDATE users SET address=? WHERE id=?', ('0xREAL_WALLET_42', u['id']))\n"
    "api._seed_for(u['id'])\n"
    "with core.conn() as c:\n"
    "    row = c.execute('SELECT address FROM users WHERE id=?', (u['id'],)).fetchone()\n"
    "print(row['address'])\n"
)


def t_real_address_kept_across_start():
    out = _run(CODE_KEEP_ADDR)
    chk("second /start keeps the real address", out, "0xREAL_WALLET_42")


CODE_KEEP_POLICY = (
    "u = core.upsert_user(0, 'Policy User', 'en', None)\n"
    "api._seed_for(u['id'])\n"
    "core.set_policy(u['id'], daily_limit=200, dual_threshold=5)\n"
    "api._seed_for(u['id'])\n"
    "with core.conn() as c:\n"
    "    row = c.execute('SELECT daily_limit, dual_threshold FROM users WHERE id=?', (u['id'],)).fetchone()\n"
    "print(row['daily_limit'], row['dual_threshold'])\n"
)


def t_user_policy_kept_across_start():
    out = _run(CODE_KEEP_POLICY)
    chk("user policy kept across /start", out, "200.0 5.0")


CODE_KEEP_CONTACT = (
    "u = core.upsert_user(0, 'Mom User', 'en', None)\n"
    "core.add_contact(u['id'], 'mom', 'Real Mom', '0xCOMPANY', 'ES')\n"
    "api._seed_for(u['id'])\n"
    'with core.conn() as c:\n'
    '    row = c.execute("SELECT address FROM contacts WHERE user_id=? AND alias=' + "'" + "mom'" + '", (u[\'id\'],)).fetchone()\n'
    "print(row['address'])\n"
)


def t_real_contact_kept_across_start():
    out = _run(CODE_KEEP_CONTACT)
    chk("user mom contact kept across /start", out, "0xCOMPANY")


CODE_NONE_NO_OVERWRITE = (
    "u = core.upsert_user(12345, 'First', 'en', None)\n"
    "with core.conn() as c:\n"
    "    c.execute('UPDATE users SET address=? WHERE id=?', ('0xLINKED_WALLET', u['id']))\n"
    "u2 = core.upsert_user(12345, 'First', 'en', None)\n"
    "print(u2['address'])\n"
)


def t_address_arg_none_does_not_overwrite():
    out = _run(CODE_NONE_NO_OVERWRITE)
    chk("upsert_user(..., None) leaves address", out, "0xLINKED_WALLET")


if __name__ == "__main__":
    t_first_start_does_not_set_address()
    t_real_address_kept_across_start()
    t_user_policy_kept_across_start()
    t_real_contact_kept_across_start()
    t_address_arg_none_does_not_overwrite()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
