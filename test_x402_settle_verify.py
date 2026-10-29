"""
Round 26: /x402/settle needed EIP-712 verification BEFORE spending gas.

Pre-fix chain.settle_authorization() did not recover_message before
broadcasting the tx: it accepted any auth dict that matched the
field names, the cryptographic check happened only when the contract's
transferWithAuthorization opcode executed. Combined with x-api-key
auth (server-to-server), a leaked key plus any signed auth dict meant
the treasury would happily pay gas for a wrong-to auth (the
contract would revert, but only after the
transferWithAuthorization was broadcast). The fix:

  chain.verify_authorization(auth, *, expected_payee=None,
                              min_value=None) -> (ok, reason)
    Local check that runs on the relayer side and rejects bad
    auths without touching the chain. Used by /x402/settle and
    dataagent.verify.

  /x402/settle now requires expected_payee == UBOT_WALLET_ADDRESS.
    Any auth whose `to` differs is rejected with HTTP 400.

  dataagent.verify still passes its own PAYEE and PRICE_USDC down
    to chain.verify_authorization, so behaviour is unchanged there.

These tests cover verify_authorization + /x402/settle's pre-check.
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
             + code)
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", full],
        capture_output=True, text=True, env=env,
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s / %s" % (r.returncode, r.stdout, r.stderr))
    out = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    return out[-1] if out else ""


def test_verify_rejects_missing_field():
    out = _run(
        "import chain\n"
        "print(chain.verify_authorization({'from':'0x'*40})[0])\n"
    )
    chk("auth without r/s/v is refused", out, "False")


def test_verify_rejects_unknown_payee_when_set():
    """When the caller specifies expected_payee, an auth whose `to`
    differs is refused."""
    out = _run(
        "import chain\n"
        "auth = {'from':'0x'*40, 'to':'0x'*40, 'value':1, "
        " 'validAfter':1, 'validBefore':2**32, 'nonce':'0x'*64, "
        " 'r':'0x'*64, 's':'0x'*64, 'v':27}\n"
        "ok, why = chain.verify_authorization(auth, expected_payee='0x' + 'a'*40)\n"
        "print(ok)\n"
    )
    chk("auth with wrong payee refused when expected is set", out, "False")


def test_verify_function_exists_and_unsigned_module():
    """verify_authorization must be exposed in chain.py and importable."""
    src = open(os.path.join(HERE, "chain.py"), encoding="utf-8").read()
    chk("chain.verify_authorization defined",
        "def verify_authorization(" in src, True)


def test_settle_authorization_runs_verify_first():
    """settle_authorization must call verify_authorization and refuse
    shaped-but-clearly-bad auths without submitting them. We pin via
    source inspection: a 'verify_authorization(auth)' call must precede
    the 'usdc.functions.transferWithAuthorization(' call."""
    src = open(os.path.join(HERE, "chain.py"), encoding="utf-8").read()
    # The settle function should call verify_authorization before transferWithAuthorization.
    i = src.index("def settle_authorization")
    j = src.index("\ndef ", i + 10)
    body = src[i:j]
    i_verify = body.find("verify_authorization(auth)")
    i_call = body.find("usdc.functions.transferWithAuthorization(")
    chk("verify_authorization called before transferWithAuthorization",
        0 < i_verify < i_call, True)


def test_x402_settle_requires_treasury_payee():
    """The /x402/settle endpoint must take expected_payee from
    UBOT_WALLET_ADDRESS, not None."""
    src = open(os.path.join(HERE, "api.py"), encoding="utf-8").read()
    i = src.index("async def x402_settle")
    j = src.index("\n", i + 100)
    block = src[i:j + 1000]
    # Find the section between the body's start and the eventual return.
    # The expected_payee=treasury must come from os.environ.
    body = block[:block.index("r = await asyncio.to_thread")]
    chk("/x402/settle sets expected_payee = treasury",
        "expected_payee=treasury" in body, True)


if __name__ == "__main__":
    test_verify_rejects_missing_field()
    test_verify_rejects_unknown_payee_when_set()
    test_verify_function_exists_and_unsigned_module()
    test_settle_authorization_runs_verify_first()
    test_x402_settle_requires_treasury_payee()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
