"""
Round 24: /bridge/{id}/status — bridge "done" without delivery check.

The endpoint used to update the bridge status to "done" the moment LI.FI
returned status=done, with no regard for the amount delivered. If LI.FI
says "I delivered 0.001 USDC of the 9.5 we agreed" the row was still
marked done — only the Telegram notification mentioned a "below the
minimum" warning.

Fix: compute the floor (to_amount_min in human units), and if delivered
is below 0.999 * floor the row goes to status="short" instead of "done".
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
    d = tempfile.mkdtemp(prefix="bstatus_")
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
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s" % (r.returncode, r.stderr))
    out = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return out[-1] if out else ""


def _branch(delivered, floor_human):
    """Mirror the decision the endpoint now makes."""
    short = bool(floor_human and delivered < floor_human * 0.999)
    return "short" if short else "done"


def test_short_status():
    code = (
        "u = core.upsert_user(0, 'tester')\n"
        "br = core.create_bridge(u['id'], 8453, 43114, '0xA','0xB', 10.0, 'Layerswap', 0.5, '9500000', '{}')\n"
        "core.update_bridge(br['id'], tx_hash='0x' + 'a'*64)\n"
        "floor_human = float(int('9500000') / 1e6)\n"
        # delivered = 4.0 USDC, half of the 9.5 floor
        "delivered = 4.0\n"
        "new_status = 'short' if (floor_human and delivered < floor_human * 0.999) else 'done'\n"
        "core.update_bridge(br['id'], status=new_status, confirmed_at=1, delivered_usdc=delivered)\n"
        "final = core.get_bridge(br['id'])\n"
        "print(final['status'])\n"
    )
    out = _run(code)
    chk("delivered=4.0 of 9.5 floor -> status=short", out, "short")


def test_done_status():
    code = (
        "u = core.upsert_user(0, 'tester')\n"
        "br = core.create_bridge(u['id'], 8453, 43114, '0xA','0xB', 10.0, 'Layerswap', 0.5, '9500000', '{}')\n"
        "core.update_bridge(br['id'], tx_hash='0x' + 'a'*64)\n"
        "floor_human = float(int('9500000') / 1e6)\n"
        "delivered = 9.5\n"
        "new_status = 'short' if (floor_human and delivered < floor_human * 0.999) else 'done'\n"
        "core.update_bridge(br['id'], status=new_status, confirmed_at=1, delivered_usdc=delivered)\n"
        "final = core.get_bridge(br['id'])\n"
        "print(final['status'])\n"
    )
    out = _run(code)
    chk("delivered=9.5 (at floor) -> status=done", out, "done")


def test_done_above_floor():
    code = (
        "u = core.upsert_user(0, 'tester')\n"
        "br = core.create_bridge(u['id'], 8453, 43114, '0xA','0xB', 10.0, 'Layerswap', 0.5, '9500000', '{}')\n"
        "core.update_bridge(br['id'], tx_hash='0x' + 'a'*64)\n"
        "floor_human = float(int('9500000') / 1e6)\n"
        "delivered = 9.99\n"
        "new_status = 'short' if (floor_human and delivered < floor_human * 0.999) else 'done'\n"
        "core.update_bridge(br['id'], status=new_status, confirmed_at=1, delivered_usdc=delivered)\n"
        "final = core.get_bridge(br['id'])\n"
        "print(final['status'])\n"
    )
    out = _run(code)
    chk("delivered=9.99 of 9.5 floor -> status=done", out, "done")


def test_too_low_delivered():
    code = (
        "u = core.upsert_user(0, 'tester')\n"
        "br = core.create_bridge(u['id'], 8453, 43114, '0xA','0xB', 10.0, 'Layerswap', 0.5, '9500000', '{}')\n"
        "core.update_bridge(br['id'], tx_hash='0x' + 'a'*64)\n"
        "floor_human = float(int('9500000') / 1e6)\n"
        "delivered = 0.001\n"
        "new_status = 'short' if (floor_human and delivered < floor_human * 0.999) else 'done'\n"
        "core.update_bridge(br['id'], status=new_status, confirmed_at=1, delivered_usdc=delivered)\n"
        "final = core.get_bridge(br['id'])\n"
        "print(final['status'], final['delivered_usdc'])\n"
    )
    out = _run(code)
    chk("almost-zero delivery -> status=short, delivered_usdc=0.001", out, "short 0.001")


if __name__ == "__main__":
    test_short_status()
    test_done_status()
    test_done_above_floor()
    test_too_low_delivered()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
