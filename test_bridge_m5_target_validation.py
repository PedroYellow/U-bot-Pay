"""
Round 24: /bridge/quote M5 defense didn't run.

Pre-fix, the source checked q.get("transaction") or q.get("transaction_request"),
but q["transaction_request"] is the fromAddress string (from bridge.py:266).
The real tx dict (with to) lives under raw.transactionRequest. isinstance
on a string is always False; the validate-tx-target guard never ran. A
compromised LI.FI/Across partner could return any address and the user
would sign a USDC.transfer to that address.

After the fix, the path validates raw.transactionRequest["to"] against
the bridge-safe allowlist and rejects the quote with a 502 otherwise.
This test pins the new behaviour.
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
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", code],
        capture_output=True, text=True, env=env, cwd=HERE,
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s\n%s" % (r.returncode, r.stdout, r.stderr))
    out = r.stdout.strip().split("\n")[-1]
    return out


def test_validation_rejects_unknown_address():
    """An arbitrary to must be rejected."""
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "from api import _validate_tx_target\n"
        "print(_validate_tx_target('0xATTACKER_ADDRESS_HERE'))\n"
    )
    out = _run(code)
    chk("arbitrary address rejected", out, "False")


def test_validation_accepts_lifi_diamond():
    """The LI.FI diamond is a known bridge contract and must be accepted."""
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "from api import _validate_tx_target\n"
        "print(_validate_tx_target('0x1231DEB6f5749EF6cE6943a275A1D3E7486F4EaE'))\n"
    )
    out = _run(code)
    chk("LI.FI diamond accepted", out, "True")


def test_validation_accepts_across_spokepool():
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "from api import _validate_tx_target\n"
        "print(_validate_tx_target('0xc186fA914353c44b2E33eBE05f21846F10421Edf'))\n"
    )
    out = _run(code)
    chk("Across SpokePool accepted", out, "True")


def test_validation_case_insensitive():
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "from api import _validate_tx_target\n"
        "print(_validate_tx_target('0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae'))\n"
    )
    out = _run(code)
    chk("allowlist match is case-insensitive", out, "True")


def test_m5_uses_raw_transaction_request():
    """The fix path must read from raw.transactionRequest."""
    src = open(os.path.join(HERE, "api.py"), encoding="utf-8").read()
    i = src.index("bridge_quote(")
    j = src.index("@app.get", i)
    body = src[i:j]
    chk("api.py:bridge_quote reads raw['transactionRequest']",
        'raw.get("transactionRequest")' in body, True)


if __name__ == "__main__":
    test_validation_rejects_unknown_address()
    test_validation_accepts_lifi_diamond()
    test_validation_accepts_across_spokepool()
    test_validation_case_insensitive()
    test_m5_uses_raw_transaction_request()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
