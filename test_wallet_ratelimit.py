"""
Round 21: per-IP and per-address rate limit on /auth/wallet/nonce, per-IP on
/auth/wallet/verify. /auth/link-telegram/confirm now accepts 6 or 7 digits.
create_link_code produces 7 digits and retries on IntegrityError.
_analytics_rate_check takes a lock.

Behaviour-tested parts (the rate limit) run in a subprocess against the
workspace so the buckets do not bleed between tests. Code-inspected parts
are pure regex on api.py / core.py.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RESULTS = []


def chk(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:48} got={got!r} want={want!r}")


def _run(api_body):
    code = (
        "import sys\n"
        f"sys.path.insert(0, {HERE!r})\n"
        "import api as A\n"
        f"{api_body}\n"
    )
    # Subprocess has access to whatever env var the caller already has set;
    # the api module fails hard on missing env, but for this test we only
    # call helpers that do not require a network or the secrets.
    env = os.environ.copy()
    env.setdefault("UBOT_API_KEY", "test-only")
    env.setdefault("UBOT_JWT_SECRET", "test-only")
    env.setdefault("UBOT_DB", "/tmp/test_wallet_rl.db")
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", code],
        capture_output=True, text=True, env=env,
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(f"subprocess failed: {r.stderr}")
    return r.stdout.strip().split("\n")[-1]


def t_ip_limit_blocks_after_max():
    """From one IP, only 30 admits per 60s window. 35 from the same IP → 30."""
    body = (
        "allowed = 0\n"
        "for _ in range(35):\n"
        "    if A._wallet_nonce_ip_allow('1.2.3.4'):\n"
        "        allowed += 1\n"
        "print(allowed)\n"
    )
    out = _run(body)
    chk("first 30 calls from same IP allowed", int(out), 30)


def t_addr_limit_blocks_after_max():
    """Same wallet address caps at 10 per minute."""
    body = (
        "allowed = 0\n"
        "for _ in range(15):\n"
        "    if A._wallet_nonce_addr_allow('0xabc'):\n"
        "        allowed += 1\n"
        "print(allowed)\n"
    )
    out = _run(body)
    chk("first 10 calls for same address allowed", int(out), 10)


def t_bucket_size_bounded():
    """Bound on the size of the buckets dict, so spoofed XFF spray cannot blow
    memory. With cap 5 and 20 distinct IPs, only 5 succeed."""
    body = (
        "A._WALLET_NONCE_MAX_BUCKETS = 5\n"
        "A._WALLET_NONCE_IP_MAX = 100\n"
        "allowed = refused = 0\n"
        "for i in range(20):\n"
        "    (allowed if A._wallet_nonce_ip_allow(f'i{i}') else refused.__class__)()\n"
        "allowed = sum(1 for i in range(20) if A._wallet_nonce_ip_allow(f'j{i}') and A._WALLET_NONCE_MAX_BUCKETS < 1000)\n"
        "refused = sum(1 for i in range(20) if not A._wallet_nonce_ip_allow(f'j{i}'))\n"
        "print(allowed, refused)\n"
    )
    # simpler version
    body = (
        "A._WALLET_NONCE_MAX_BUCKETS = 5\n"
        "A._WALLET_NONCE_IP_MAX = 100\n"
        "allowed = 0; refused = 0\n"
        "for i in range(10):\n"
        "    if A._wallet_nonce_ip_allow(f'k{i}'):\n"
        "        allowed += 1\n"
        "    else:\n"
        "        refused += 1\n"
        "print(allowed, refused)\n"
    )
    out = _run(body)
    a, r = map(int, out.split())
    chk("5 unique IPs allowed until the bucket is full", a, 5)
    chk("the next 5 are refused by the size cap", r, 5)


def t_handler_calls_allow():
    src = open(os.path.join(HERE, "api.py"), encoding="utf-8").read()
    i = src.index("async def auth_wallet_nonce(")
    j = src.index("\n@app.", i)
    body = src[i:j]
    chk("nonce handler calls IP allow",
        "_wallet_nonce_ip_allow" in body, True)
    chk("nonce handler calls address allow",
        "_wallet_nonce_addr_allow" in body, True)
    chk("nonce handler raises 429 on overflow",
        "HTTPException(429" in body, True)
    i = src.index("async def auth_wallet_verify(")
    j = src.index("\n@app.", i)
    body = src[i:j]
    chk("verify handler calls IP allow",
        "_wallet_verify_ip_allow" in body, True)
    chk("verify handler raises 429 on overflow",
        "HTTPException(429" in body, True)


def t_create_link_code_7_digits():
    src = open(os.path.join(HERE, "core.py"), encoding="utf-8").read()
    i = src.index("def create_link_code(")
    j = src.index("\ndef ", i+10)
    body = src[i:j]
    chk("create_link_code uses 7 digits",
        "randbelow(10_000_000)" in body and ":07d" in body, True)
    chk("create_link_code retries on IntegrityError",
        "IntegrityError" in body, True)


def t_link_code_confirm_accepts_6_or_7():
    src = open(os.path.join(HERE, "api.py"), encoding="utf-8").read()
    # Find the next @app.<something> decorator after the start, regardless of
    # whether the regex backslashes survive the disk write.
    i = src.index("async def auth_link_telegram_confirm(")
    # Look for any of @app., @app_., @on_app., in the rest. Use a safe sentinel.
    sentinel_options = ["\n@app.", "\n@app_."]
    j = len(src)
    for s2 in sentinel_options:
        s2_real = s2.encode().decode("unicode_escape")
        try:
            j2 = src.index(s2_real, i)
            if j2 < j:
                j = j2
        except ValueError:
            pass
    body = src[i:j]
    chk("confirm accepts 6 or 7 digits",
        "len(code) not in (6, 7)" in body, True)


def t_analytics_rate_lock():
    src = open(os.path.join(HERE, "api.py"), encoding="utf-8").read()
    i = src.index("def _analytics_rate_check(")
    j = src.index("\n@app.", i)
    body = src[i:j]
    chk("analytics_rate_check uses a lock",
        "_ANALYTICS_LOCK" in body and "with _ANALYTICS_LOCK" in body, True)


def t_no_dead_deps_in_requirements():
    src = open(os.path.join(HERE, "requirements.txt"), encoding="utf-8").read().lower()
    chk("apscheduler removed", "apscheduler" not in src, True)
    chk("pyyaml removed", "pyyaml" not in src, True)
    chk("python-multipart bounded", "<0.1" in src, True)
    chk("web3 pinned to 8.0.0",
        "web3==8.0.0" in open(os.path.join(HERE, "requirements.txt")).read(),
        True)


if __name__ == "__main__":
    t_ip_limit_blocks_after_max()
    t_addr_limit_blocks_after_max()
    t_bucket_size_bounded()
    t_handler_calls_allow()
    t_create_link_code_7_digits()
    t_link_code_confirm_accepts_6_or_7()
    t_analytics_rate_lock()
    t_no_dead_deps_in_requirements()
    print("=" * 76)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
