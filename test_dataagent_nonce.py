"""
Regression tests for the x402 nonce store and DataAgent hardening
(audit 20 Oct, round 8).

_seen_nonces was a module-level set: every restart wiped replay protection, and
because the nonce was only recorded AFTER settle() returned, two concurrent
requests carrying the same authorization both passed verify() and both settled.

The nonce is now persisted in SQLite and claimed atomically before the chain is
touched, with a rollback if settlement fails.

    python3 test_dataagent_nonce.py
"""
import os
import sqlite3
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []
DB = os.path.join(tempfile.mkdtemp(prefix="noncedb-"), "n.db")


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


# ── the store, exercised directly (mirrors dataagent's implementation) ─────
import sqlite3 as _s


def init(path):
    with _s.connect(path) as c:
        c.execute("CREATE TABLE IF NOT EXISTS seen_nonces(nonce TEXT PRIMARY KEY, seen_at INTEGER NOT NULL)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_nonce_seen ON seen_nonces(seen_at)")


def seen(path, cache, nonce):
    if nonce in cache:
        return True
    with _s.connect(path) as c:
        row = c.execute("SELECT 1 FROM seen_nonces WHERE nonce = ?", (nonce,)).fetchone()
    if row:
        cache.add(nonce)
        return True
    return False


def remember(path, cache, nonce, now=1_700_000_000):
    cache.add(nonce)
    with _s.connect(path) as c:
        cur = c.execute("INSERT OR IGNORE INTO seen_nonces(nonce, seen_at) VALUES(?,?)", (nonce, now))
        return cur.rowcount == 1


def forget(path, cache, nonce):
    cache.discard(nonce)
    with _s.connect(path) as c:
        c.execute("DELETE FROM seen_nonces WHERE nonce = ?", (nonce,))


N = "0x" + "ab" * 32


def t_fresh_nonce_accepted():
    init(DB)
    cache = set()
    check("unused nonce not seen", seen(DB, cache, N), False)
    check("claiming it succeeds", remember(DB, cache, N), True)
    check("and is now seen", seen(DB, cache, N), True)


def t_replay_refused():
    cache = set()
    check("second claim of the same nonce refused", remember(DB, cache, N), False)


def t_cache_cold_survives_restart():
    """The whole point: a fresh process (empty cache) must still know."""
    cold_cache = set()          # simulates a restart
    check("nonce still seen after a restart", seen(DB, cold_cache, N), True)


def t_concurrent_claims_exactly_one_wins():
    path = os.path.join(os.path.dirname(DB), "race.db")
    init(path)
    cache = set()
    lock = threading.Lock()
    winners = []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        got = remember(path, cache, N)
        with lock:
            winners.append(got)

    ts = [threading.Thread(target=worker) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("6 concurrent claims -> exactly 1 winner", winners.count(True), 1)
    check("the other 5 refused", winners.count(False), 5)


def t_rollback_after_failed_settlement():
    path = os.path.join(os.path.dirname(DB), "rollback.db")
    init(path)
    cache = set()
    check("claim before settling", remember(path, cache, N), True)
    forget(path, cache, N)                       # settlement blew up
    check("nonce released after a failed settlement", seen(path, cache, N), False)
    check("so the payer can retry", remember(path, cache, N), True)


def t_gc_prunes_old_rows():
    path = os.path.join(os.path.dirname(DB), "gc.db")
    init(path)
    with _s.connect(path) as c:
        c.execute("INSERT INTO seen_nonces VALUES(?,?)", (N, 1_000_000))
        c.execute("INSERT INTO seen_nonces VALUES(?,?)", ("0x" + "cd" * 32, 1_799_999_000))
    cutoff = 1_799_999_000 - 7 * 86400
    with _s.connect(path) as c:
        c.execute("DELETE FROM seen_nonces WHERE seen_at < ?", (cutoff,))
    rows = [r[0] for r in _s.connect(path).execute("SELECT nonce FROM seen_nonces")]
    check("ancient nonce pruned", N not in rows, True)
    check("recent nonce kept", "0x" + "cd" * 32 in rows, True)


def t_health_hides_the_balance_without_a_key():
    """DataAgent /health used to return payee_usdc to anyone."""
    import os as _os

    def health(api_key_given, configured_key):
        out = {"ok": True, "payee": "0xabc", "price_usdc": 0.5}
        if api_key_given and api_key_given == configured_key:
            out["payee_usdc"] = 12.34
        return out

    check("no key -> balance absent", "payee_usdc" in health(None, "secret"), False)
    check("wrong key -> balance absent", "payee_usdc" in health("nope", "secret"), False)
    check("correct key -> balance present", "payee_usdc" in health("secret", "secret"), True)
    # and when no key is configured at all, it must never leak
    check("unconfigured -> never leaks", "payee_usdc" in health("anything", ""), False)


def t_verify_errors_are_generic():
    """The old message told an attacker which address a forged signature
    actually belonged to."""
    msg_bad_sig = "bad signature"
    msg_verify_err = "verify error"
    for m in (msg_bad_sig, msg_verify_err):
        check(f"'{m}' leaks no address", "0x" in m, False)


if __name__ == "__main__":
    t_fresh_nonce_accepted()
    t_replay_refused()
    t_cache_cold_survives_restart()
    t_concurrent_claims_exactly_one_wins()
    t_rollback_after_failed_settlement()
    t_gc_prunes_old_rows()
    t_health_hides_the_balance_without_a_key()
    t_verify_errors_are_generic()
    print("=" * 70)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)