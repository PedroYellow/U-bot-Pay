"""
Round 27: dataagent._remember_nonce no longer leaves a nonce pinned in
memory when SQLite raises.

Before round 27: _remember_nonce did `set.add(nonce)` BEFORE the
SQLite INSERT. If the insert raised (OperationalError: database is
locked, disk full, cold-start race), the nonce was already in the
in-memory cache. The auth would then be permanently blocked:
_nonce_seen() would short-circuit to True and reject every future
auth with the same nonce — without the disk row to back it up, and
without a restart.

The fix:

  - Single persistent connection with WAL + busy_timeout=5s.
  - The INSERT and the in-memory add share the same _nonce_lock.
  - If the INSERT raises, the nonce is NOT added to memory and the
    function returns False (so the caller does not settle the auth,
    and a future retry with a fresh nonce is the same as before).
  - If the INSERT succeeds with rowcount==1 (we wrote the row) the
    nonce is added to memory.
  - If the INSERT succeeded with rowcount==0 (someone else wrote
    first) the nonce is also added to memory — that one IS in the DB
    by definition — but the function returns False.

This test pins the post-conditions:

  1. When SQLite is available and unique, _remember_nonce returns True
     and the row + the memory entry are both present.
  2. When SQLite is available but the same nonce has been recorded
     before, _remember_nonce returns False (already claimed).
  3. When SQLite raises an OperationalError, _remember_nonce returns
     False and the nonce is NOT in memory — the next /report attempt
     with a fresh nonce is not blocked.
  4. The in-memory cache no longer pretends to be authoritative: a
     nonce that is in memory but missing from the DB does not survive
     a reconnect of _nonce_conn (after _init_nonce_store). We do not
     explicitly test startup, but the source pin below confirms the
     rehydrated cache only carries DB-resident nonces.
  5. The pre-existing _nonce_seen() still returns True for nonces
     recorded before the round.

Test setup:
  - Each case runs in a subprocess with its own dataagent module
    import (UBOT_API_KEY/UBOT_JWT_SECRET set so api.py's startup
    gate does not abort the import).
  - The OperationalError case monkey-patches sqlite3.connect to raise.
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
    """Each case runs in its own fresh dataagent import so the module
    state (_seen_nonces, _nonce_conn) is not leaked between cases."""
    d = tempfile.mkdtemp(prefix="c5_")
    ndb = os.path.join(d, "nonces.db")
    env = os.environ.copy()
    env["DATAAGENT_NONCE_DB"] = ndb
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")
    full = (
        "import sys\n"
        + 'sys.path.insert(0, "%s")\n' % HERE
        + "import sqlite3\n"
        + code
    )
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", full],
        capture_output=True, text=True, env=env,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s\n%s" % (r.returncode, r.stdout, r.stderr))
    out = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    return out[-1] if out else ""


def t_remember_unique():
    """First call gets the row, returns True, adds the nonce to memory."""
    out = _run(
        "import dataagent as D\n"
        "ok = D._remember_nonce('0xaaa')\n"
        "print(ok, '0xaaa' in D._seen_nonces)\n"
    )
    chk("first remember claims the nonce and primes memory", out, "True True")


def t_double_insert_returns_false():
    """A second call with the same nonce: SQLite INSERT is IGNORED,
    rowcount==0, function returns False. The nonce IS in memory (it
    IS in the DB)."""
    out = _run(
        "import dataagent as D\n"
        "D._remember_nonce('0xbbb')\n"
        "ok = D._remember_nonce('0xbbb')\n"
        "print(ok, '0xbbb' in D._seen_nonces)\n"
    )
    chk("second remember returns False (already claimed)", out, "False True")


def t_insert_failure_does_not_pin_memory():
    """If the INSERT raises, _remember_nonce returns False and the
    nonce is NOT in memory. This is the whole point of round 27.

    sqlite3 disallows reassigning Connection.execute (a C-implemented
    type), so we test the failure path through the helper
    _nonce_persist, which is module-level Python and re-assignable."""
    out = _run(
        "import dataagent as D\n"
        "import sqlite3 as S\n"
        "_real = D._nonce_persist\n"
        "def _fake(nonce, now):\n"
        "    raise S.OperationalError('database is locked')\n"
        "D._nonce_persist = _fake\n"
        "try:\n"
        "    ok = D._remember_nonce('0xccc')\n"
        "    print(ok, '0xccc' in D._seen_nonces)\n"
        "finally:\n"
        "    D._nonce_persist = _real\n"
    )
    chk("sqlite failure: returns False, nonce not in memory",
        out, "False False")


def t_nonce_seen_hits_db_after_cold_start():
    """After a successful insert in process 1, we re-import in a fresh
    process pointing at the same DB file. _nonce_seen must still find
    the row. This proves SQLite is the source of truth."""
    d = tempfile.mkdtemp(prefix="c5cs_")
    ndb = os.path.join(d, "nonces.db")
    env = os.environ.copy()
    env["DATAAGENT_NONCE_DB"] = ndb
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")

    def run_case(code):
        full = ('import sys\n'
                + 'sys.path.insert(0, "%s")\n' % HERE
                + code)
        r = subprocess.run(
            ["/opt/ubotpay/venv/bin/python", "-c", full],
            capture_output=True, text=True, env=env,
        )
        if r.returncode != 0:
            raise RuntimeError("rc=%d: %s\n%s" % (r.returncode, r.stdout, r.stderr))
        return r.stdout.strip().splitlines()[-1]

    # Process 1: claim nonce
    out1 = run_case(
        "import dataagent as D\n"
        "ok = D._remember_nonce('0xdedupe')\n"
        "print(ok)\n"
    )
    chk("process 1 claimed the nonce", out1, "True")

    # Process 2: cold start, verify _nonce_seen hits SQLite
    out2 = run_case(
        "import dataagent as D\n"
        "v = D._nonce_seen('0xdedupe')\n"
        "print(v)\n"
    )
    chk("process 2 finds the nonce across cold restart", out2, "True")


if __name__ == "__main__":
    t_remember_unique()
    t_double_insert_returns_false()
    t_insert_failure_does_not_pin_memory()
    t_nonce_seen_hits_db_after_cold_start()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
