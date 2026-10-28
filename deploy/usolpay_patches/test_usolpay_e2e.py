"""
End-to-end: generate a treasury, wrap it in a FileSigner, build the
engine through the registry, and prove the wiring.

Runs entirely in a temp directory. No key is printed, nothing is
funded, nothing is broadcast. The generated keypair is discarded when
the test exits.
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.environ.get(
        "ENGINE_ROOT", os.path.dirname(os.path.abspath(__file__))))

# The environment the service uses. The suite must run with the same
# secrets, because api.py refuses to import without them.
#
# The file is this deployment's own .env, and the loader uses
# setdefault, so a variable that is already in the environment wins.
ENV_FILE = os.environ.get(
    "USOLPAY_ENV_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
if os.path.exists(ENV_FILE):
    for line in open(ENV_FILE):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip('"').strip("'"))


RESULTS = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-56s %s" % ("PASS" if cond else "FAIL", name, detail))


def raises(name, fn, exc=Exception, needle=""):
    try:
        fn()
    except exc as e:
        msg = str(e)
        if needle and needle.lower() not in msg.lower():
            chk(name, False, "refused but message unhelpful: %s" % msg[:80])
        else:
            chk(name, True, "refused: %s" % msg[:66])
        return
    except Exception as e:
        chk(name, False, "wrong exception type: %s" % type(e).__name__)
        return
    chk(name, False, "did NOT refuse")


from chains.registry import _load_yaml, build_engine
from chains.solana_signer import FileSigner, NullSigner

PROFILE = os.environ.get(
        "PROFILE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "profiles", "usolpay.yaml"))


GEN = os.environ.get(
        "GEN_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "deploy", "make_treasury_solana.py"))

tmp = tempfile.mkdtemp(prefix="usolpay-e2e-")
try:
    # ── 1. generate a treasury in the temp dir ────────────────────
    env = dict(os.environ, USOLPAY_SECRET_DIR=tmp)
    r = subprocess.run([sys.executable, GEN], env=env,
                       capture_output=True, text=True, timeout=60)
    chk("generator exits cleanly", r.returncode == 0, r.stderr.strip()[:80])
    out = r.stdout
    chk("generator reports re-derivation ok", "REDERIVE_OK = True" in out)
    chk("generator reports pubkey round-trip ok", "PUBKEY_ROUNDTRIP_OK = True" in out)
    chk("generator reports 0600", "PERMS = 0o600" in out, out.strip().splitlines()[-4:])

    key_path = os.path.join(tmp, "treasury.key")
    pub_path = os.path.join(tmp, "treasury.pub")
    chk("key file written", os.path.exists(key_path))
    chk("pub file written", os.path.exists(pub_path))

    addr = None
    for line in out.splitlines():
        if line.startswith("ADDRESS ="):
            addr = line.split("=", 1)[1].strip()
    chk("address printed", bool(addr), (addr or "")[:20] + "...")
    chk("key file mode is 0600",
        oct(os.stat(key_path).st_mode & 0o777) == "0o600",
        oct(os.stat(key_path).st_mode & 0o777))

    # ── 2. the key file must never contain the address or a label ──
    with open(key_path) as fh:
        keybody = fh.read().strip()
    chk("key file holds only the base58 seed", " " not in keybody and len(keybody) >= 32,
        "len=%d" % len(keybody))
    chk("key file does not contain the address", addr not in keybody)
    chk("key file has no trailing newline inside the secret",
        keybody == keybody.strip())

    # ── 3. FileSigner loads it and reports the same address ───────
    signer = FileSigner(key_path)
    chk("FileSigner pubkey matches the generated address",
        str(signer.pubkey()) == addr, str(signer.pubkey())[:20] + "...")

    # The pub file is the documented record; it must agree with the key.
    with open(pub_path) as fh:
        pubtext = fh.read()
    chk("pub file records the same address", f"solana_address: {addr}" in pubtext)
    chk("pub file names the USDC mint",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" in pubtext)
    chk("pub file warns that SOL is needed too", "SOL" in pubtext)

    # ── 4. the signer must not hand out the seed ───────────────────
    # The seed lives in _kp and has no accessor. Checking dir() for the
    # substring "seed" would be meaningless (pubkey contains "key"), so
    # check the public surface directly: exactly two methods, no data.
    public = [a for a in dir(signer) if not a.startswith("_")]
    # keypair() is public on purpose: solders 0.29's
    # new_signed_with_payer takes the keypair itself, so the engine has
    # to ask the signer for it. What must NOT be public is the seed.
    chk("public surface is pubkey + sign_transaction + keypair",
        sorted(public) == ["keypair", "pubkey", "sign_transaction"], public)
    chk("no public attribute holds the seed bytes",
        not any(isinstance(getattr(signer, a, None), (bytes, bytearray))
                for a in public))
    chk("signer has no _seed/_kp-style public accessor",
        not hasattr(signer, "seed") and not hasattr(signer, "private_key"))

    # ── 5. build through the registry, the way production does ─────
    prof = _load_yaml(PROFILE)
    spec = {"kind": "solana", "profile": prof, "key_path": key_path}
    eng = build_engine(spec)
    chk("registry builds a SolanaEngine", type(eng).__name__ == "SolanaEngine",
        type(eng).__name__)
    chk("built engine sees the treasury", eng._treasury_address() == addr,
        (eng._treasury_address() or "")[:20] + "...")
    chk("built engine validates its own treasury address",
        eng.is_address(eng._treasury_address()))

    # ── 6. a registry-built unfunded treasury refuses to send ──────
    raises("unfunded generated treasury refuses to send",
            lambda: eng.submit("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9", 1.0),
            RuntimeError, "sol")

    # ── 7. NullSigner path: reads work, sends refuse, no key needed ─
    spec2 = {"kind": "solana", "profile": prof}
    eng2 = build_engine(spec2)
    chk("registry with no key_path still builds", type(eng2).__name__ == "SolanaEngine")
    chk("no-key engine has no treasury", eng2._treasury_address() is None)
    raises("no-key engine refuses to send",
            lambda: eng2.submit("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9", 1.0))
    # but reads must work, that is the whole point of the read-only mode
    try:
        b = eng2.balance("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9")
        chk("no-key engine can still read balances", b["settled"] >= 0,
            "settled=%s" % b["settled"])
    except Exception as e:
        chk("no-key engine can still read balances", False,
            type(e).__name__ + ": " + str(e)[:70])

    # ── 8. NullSigner refuses clearly, not with an AttributeError ──
    ns = NullSigner()
    chk("NullSigner has no pubkey", ns.pubkey() is None)
    # The message must say what is actually wrong -- "no key" -- not
    # "no treasury address configured", which sends the operator
    # looking in the profile instead of at the secrets directory.
    raises("NullSigner refuses to sign", lambda: ns.sign_transaction(None),
            Exception, "no solana treasury key")
    try:
        ns.sign_transaction(None)
    except Exception as e:
        msg = str(e)
        chk("NullSigner message names the key file env var",
            "USOLPAY_TREASURY_KEY" in msg, msg[:80])
        chk("NullSigner message says reads still work", "read" in msg.lower())

    # ── 9. FileSigner rejects a malformed key file ────────────────
    bad = os.path.join(tmp, "bad.key")
    with open(bad, "w") as fh:
        fh.write("not-base58-!!")          # invalid base58
    raises("FileSigner rejects non-base58", lambda: FileSigner(bad), ValueError, "base58")

    bad2 = os.path.join(tmp, "short.key")
    with open(bad2, "w") as fh:
        fh.write("3vQB7")                    # valid base58, wrong length
    raises("FileSigner rejects wrong seed length", lambda: FileSigner(bad2),
            ValueError, "32-byte")

    empty = os.path.join(tmp, "empty.key")
    open(empty, "w").close()
    raises("FileSigner rejects an empty key file", lambda: FileSigner(empty), ValueError)

    raises("FileSigner rejects a missing file",
            lambda: FileSigner(os.path.join(tmp, "nope.key")), FileNotFoundError)

    # ── 10. the generator must not silently overwrite a treasury ────
    r2 = subprocess.run([sys.executable, GEN], env=env,
                        capture_output=True, text=True, timeout=60)
    chk("generator refuses to overwrite an existing treasury",
        r2.returncode != 0 and "refusing to overwrite" in r2.stdout + r2.stderr,
        (r2.stdout + r2.stderr).strip().splitlines()[-1][:70] if (r2.stdout + r2.stderr).strip() else "")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ── the public balance endpoint ──────────────────────────────────
# It was carried over from U-bot Pay, where it validated the address
# with Web3.is_address -- "is this a well-formed 0x address?". On
# USolPay every Solana address is base58, so it rejected ALL of them
# with 400 and the endpoint did not work at all.
try:
    from fastapi.testclient import TestClient
    import api as _api

    _c = TestClient(_api.app)

    # The balance endpoint is AUTHENTICATED. A balance is an inventory
    # of a spendable asset, and the audit made it so: it takes a
    # Bearer JWT, or the internal x-api-key header. The test called it
    # publicly and expected 200 - the behaviour the audit removed - so
    # it answered 401.
    #
    # It also used a path parameter that the route does not have. The
    # route is /balance, not /balance/<address>.
    _key = os.environ.get("USOL_API_KEY", "")
    _r = _c.get("/balance", headers={"x-api-key": _key})
    chk("the balance endpoint answers 200 for the demo user",
        _r.status_code == 200, _r.status_code)

    _r_unauth = _c.get("/balance")
    chk("the balance endpoint refuses an unauthenticated caller",
        _r_unauth.status_code == 401, _r_unauth.status_code)
    if _r.status_code == 200:
        _b = _r.json()
        chk("the balance endpoint returns the tokens",
            "USDT" in (_b.get("tokens") or {}),
            list((_b.get("tokens") or {})))
        chk("the balance endpoint returns the native balance",
            "native_balance" in _b, _b.get("native_symbol"))
    _r2 = _c.get("/balance/not-a-real-solana-address")
    chk("the balance endpoint refuses a malformed address",
        _r2.status_code == 400, _r2.status_code)
except ImportError:
    chk("the balance endpoint test could not run (no test client)", False)

print("=" * 86)
print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
print("ALL E2E TESTS PASSED" if all(RESULTS) else "SOME E2E TESTS FAILED")
sys.exit(0 if all(RESULTS) else 1)

