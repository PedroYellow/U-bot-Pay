"""
Round 25: reaper for stale pending/pending_approval payments.

After _execute attaches the tx_hash to the row before confirm_usdc, the
process can still die between attach and confirm. Without a reaper those
rows are stuck on the user's daily limit forever. This test pins the
reaper behaviour against a fake receipt:

  - tx + receipt status=1 -> status -> settled.
  - tx + receipt status=0 -> status -> failed.
  - tx + receipt None      -> row untouched (next tick will re-check).
  - no tx, even stale       -> row untouched (this reaper does not own it).

Test for the simpler C-2 reaper: reap_stale_settling_claims with the same
input layout.

The actual w3.eth.get_transaction_receipt is replaced with a fake, so the
test runs without a live RPC. We also stub `import chain as _chain` inside
the function by patching core.reap_stale_pending_payments's import; since
the function imports inside the function, we just need a chain module with
the `w3.eth.get_transaction_receipt` returning our test fixture.
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
    d = tempfile.mkdtemp(prefix="reaper_")
    env = os.environ.copy()
    env["UBOT_DB"] = os.path.join(d, "test.db")
    env.setdefault("UBOT_API_KEY", "x")
    env.setdefault("UBOT_JWT_SECRET", "x")
    full = (
        "import sys\n"
        + 'sys.path.insert(0, "%s")\n' % HERE
        + code
    )
    r = subprocess.run(
        ["/opt/ubotpay/venv/bin/python", "-c", full],
        capture_output=True, text=True, env=env,
    )
    if r.returncode != 0:
        raise RuntimeError("rc=%d: %s\n%s" % (r.returncode, r.stdout, r.stderr))
    return r.stdout.strip().splitlines()


def test_attach_payment_tx_writes_hash():
    code = (
        "import core\n"
        "core.init()\n"
        "u = core.upsert_user(0, 't')\n"
        "row = core.create_payment(u['id'], {'alias':'mom','name':'Mom','address':'0xA'}, "
        "{'amount_usdc':1.0,'total_usdc':1.0,'fx_rate':1.0,'fee_usdc':0.0,'amount_local':1.0,'local_ccy':'USDC'}, "
        "'m', 'voice', False)\n"
        "core.attach_payment_tx(row['id'], '0xabc123')\n"
        "after = core.get_payment(row['id'])\n"
        "print(after['tx'])\n"
    )
    out = _run(code)
    chk("attach_payment_tx writes tx_hash", out[-1], "0xabc123")


def test_pending_reaper_settles_on_receipt_status_1():
    code = (
        "import sys, types, importlib\n"
        "sys.path.insert(0, '.')\n"
        "import core\n"
        "core.init()\n"
        "u = core.upsert_user(0, 't')\n"
        "row = core.create_payment(u['id'], {'alias':'mom','name':'Mom','address':'0xA'}, "
        "{'amount_usdc':1.0,'total_usdc':1.0,'fx_rate':1.0,'fee_usdc':0.0,'amount_local':1.0,'local_ccy':'USDC'}, "
        "'m', 'voice', False)\n"
        "core.attach_payment_tx(row['id'], '0xdeadbeef')\n"
        # Force created_at older than the reaper cutoff (>=30 min).\n"
        "with core.conn() as c:\n"
        "    c.execute(\"UPDATE payments SET created_at = strftime('%s','now') - 7200 WHERE id=?\", (row['id'],))\n"
        # Inject a fake `chain` module with w3.eth.get_transaction_receipt returning a mock with status=1.\n"
        "class MockReceipt:\n"
        "    status = 1\n"
        "class MockW3:\n"
        "    class eth:\n"
        "        @staticmethod\n"
        "        def get_transaction_receipt(tx):\n"
        "            return MockReceipt()\n"
        "fake_chain = types.ModuleType('chain')\n"
        "fake_chain.w3 = MockW3()\n"
        "sys.modules['chain'] = fake_chain\n"
        "ids = core.reap_stale_pending_payments(max_age_s=10)\n"
        "after = core.get_payment(row['id'])\n"
        "print(after['status'])\n"
        "print(len(ids))\n"
    )
    out = _run(code)
    chk("receipt status=1 -> pending -> settled", out[-2], "settled")
    chk("reaper returns the moved ids", out[-1], "1")


def test_pending_reaper_marks_failed_on_status_0():
    code = (
        "import sys, types\n"
        "sys.path.insert(0, '.')\n"
        "import core\n"
        "core.init()\n"
        "u = core.upsert_user(0, 't')\n"
        "row = core.create_payment(u['id'], {'alias':'mom','name':'Mom','address':'0xA'}, "
        "{'amount_usdc':1.0,'total_usdc':1.0,'fx_rate':1.0,'fee_usdc':0.0,'amount_local':1.0,'local_ccy':'USDC'}, "
        "'m', 'voice', False)\n"
        "core.attach_payment_tx(row['id'], '0xdeadbeef')\n"
        "with core.conn() as c:\n"
        "    c.execute(\"UPDATE payments SET created_at = strftime('%s','now') - 7200 WHERE id=?\", (row['id'],))\n"
        "class MockReceipt:\n"
        "    status = 0\n"
        "class MockW3:\n"
        "    class eth:\n"
        "        @staticmethod\n"
        "        def get_transaction_receipt(tx):\n"
        "            return MockReceipt()\n"
        "fake_chain = types.ModuleType('chain')\n"
        "fake_chain.w3 = MockW3()\n"
        "sys.modules['chain'] = fake_chain\n"
        "core.reap_stale_pending_payments(max_age_s=10)\n"
        "after = core.get_payment(row['id'])\n"
        "print(after['status'])\n"
    )
    out = _run(code)
    chk("receipt status=0 -> pending -> failed", out[-1], "failed")


def test_pending_reaper_leaves_pending_when_no_receipt():
    code = (
        "import sys, types\n"
        "sys.path.insert(0, '.')\n"
        "import core\n"
        "core.init()\n"
        "u = core.upsert_user(0, 't')\n"
        "row = core.create_payment(u['id'], {'alias':'mom','name':'Mom','address':'0xA'}, "
        "{'amount_usdc':1.0,'total_usdc':1.0,'fx_rate':1.0,'fee_usdc':0.0,'amount_local':1.0,'local_ccy':'USDC'}, "
        "'m', 'voice', False)\n"

        "core.attach_payment_tx(row['id'], '0xpending_tx')\n"
        "with core.conn() as c:\n"
        "    c.execute(\"UPDATE payments SET created_at = strftime('%s','now') - 7200 WHERE id=?\", (row['id'],))\n"
        "class MockW3:\n"
        "    class eth:\n"
        "        @staticmethod\n"
        "        def get_transaction_receipt(tx):\n"
        "            return None\n"
        "fake_chain = types.ModuleType('chain')\n"
        "fake_chain.w3 = MockW3()\n"
        "sys.modules['chain'] = fake_chain\n"
        "core.reap_stale_pending_payments(max_age_s=10)\n"
        "after = core.get_payment(row['id'])\n"
        "print(after['status'])\n"
    )
    out = _run(code)
    chk("receipt None -> leave alone for next tick", out[-1], "pending")


def test_settling_reaper_settles_on_status_1_with_tx():
    code = (
        "import sys, types\n"
        "sys.path.insert(0, '.')\n"
        "import core\n"
        "core.init()\n"
        "u = core.upsert_user(0, 't')\n"
        "row = core.create_payment(u['id'], {'alias':'mom','name':'Mom','address':'0xA'}, "
        "{'amount_usdc':1.0,'total_usdc':1.0,'fx_rate':1.0,'fee_usdc':0.0,'amount_local':1.0,'local_ccy':'USDC'}, "
        "'m', 'voice', True)\n"

        "core.claim_payment_for_settlement(row['id'])\n"
        "core.attach_payment_tx(row['id'], '0xmined_tx')\n"
        "with core.conn() as c:\n"
        "    c.execute(\"UPDATE payments SET claimed_at = strftime('%s','now') - 7200 WHERE id=?\", (row['id'],))\n"
        "class MockReceipt:\n"
        "    status = 1\n"
        "class MockW3:\n"
        "    class eth:\n"
        "        @staticmethod\n"
        "        def get_transaction_receipt(tx):\n"
        "            return MockReceipt()\n"
        "fake_chain = types.ModuleType('chain')\n"
        "fake_chain.w3 = MockW3()\n"
        "sys.modules['chain'] = fake_chain\n"
        "core.reap_stale_settling_claims(max_age_s=10)\n"
        "after = core.get_payment(row['id'])\n"
        "print(after['status'])\n"
    )
    out = _run(code)
    chk("settling reaper: tx + status=1 -> settled", out[-1], "settled")


def test_settling_reaper_recycles_no_tx_to_pending():
    """A settling row that never received a tx_hash (process died before
    send_raw_transaction even) must go back to pending_approval so a
    second /approve attempts a fresh send."""
    code = (
        "import sys, types\n"
        "sys.path.insert(0, '.')\n"
        "import core\n"
        "core.init()\n"
        "u = core.upsert_user(0, 't')\n"
        "row = core.create_payment(u['id'], {'alias':'mom','name':'Mom','address':'0xA'}, "
        "{'amount_usdc':1.0,'total_usdc':1.0,'fx_rate':1.0,'fee_usdc':0.0,'amount_local':1.0,'local_ccy':'USDC'}, "
        "'m', 'voice', True)\n"
        "core.claim_payment_for_settlement(row['id'])\n"
        # No tx_hash attached.\n"
        "with core.conn() as c:\n"
        "    c.execute(\"UPDATE payments SET claimed_at = strftime('%s','now') - 7200 WHERE id=?\", (row['id'],))\n"
        "# Empty chain module (the reaper shouldn't ask anything when tx IS NULL).\n"
        "fake_chain = types.ModuleType('chain')\n"
        "fake_chain.w3 = object()\n"
        "sys.modules['chain'] = fake_chain\n"
        "core.reap_stale_settling_claims(max_age_s=10)\n"
        "after = core.get_payment(row['id'])\n"
        "print(after['status'])\n"
    )
    out = _run(code)
    chk("settling with no tx -> recycled to pending_approval",
        out[-1], "pending_approval")


if __name__ == "__main__":
    test_attach_payment_tx_writes_hash()
    test_pending_reaper_settles_on_receipt_status_1()
    test_pending_reaper_marks_failed_on_status_0()
    test_pending_reaper_leaves_pending_when_no_receipt()
    test_settling_reaper_settles_on_status_1_with_tx()
    test_settling_reaper_recycles_no_tx_to_pending()
    print("=" * 76)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
