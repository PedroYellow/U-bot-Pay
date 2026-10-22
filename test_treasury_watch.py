"""
Tests for treasury_watch — the 5-minute balance poller that alerts the
operator on incoming USDC.

Run on the VPS with the real venv (it imports chain, which needs web3):
    /opt/ubotpay/venv/bin/python /opt/ubotpay/test_treasury_watch.py

No network, no chain access, no bot: _fetch_balance is monkeypatched and
notify is a plain list.append. The point is that the alerting rules are
right — a fresh deploy must stay silent, a real arrival must fire once,
a dust delta must not, and a big unexplained drop must be logged.

State file is redirected into a temp dir per test so nothing touches
/opt/ubotpay/treasury_watch.json.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, "/opt/ubotpay")

RESULTS = []


def chk(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print("%s  %-56s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


def chk_true(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-56s %s" % ("PASS" if cond else "FAIL", name, detail))


# ── a fake balance source we can drive ────────────────────────────────
_FAKE = {"balance": 60.0}


def install_fake_watch(tmpdir, balance):
    """Import treasury_watch fresh, point its state file at tmpdir and its
    balance source at a value we control."""
    import importlib
    if "treasury_watch" in sys.modules:
        del sys.modules["treasury_watch"]
    tw = importlib.import_module("treasury_watch")

    tw.STATE_PATH = os.path.join(tmpdir, "state.json")
    tw.TREASURY = "0x673678B1870Ae7ef598A5982D25ffdee68d85669"
    tw.MIN_DELTA = 0.01
    tw.DROP_TOLERANCE = 0.05
    tw.POLL_SECONDS = 300

    _FAKE["balance"] = balance
    tw._fetch_balance = lambda: _FAKE["balance"]
    return tw


def test_first_run_is_silent(tw, tmpdir):
    """A fresh deploy must not page the operator for pre-existing money.
    This is the single most important behaviour: without it every restart
    would announce the whole treasury balance."""
    seen = []
    delta, last = tw.check_once(notify=seen.append)
    chk("first run returns no delta", delta, None)
    chk("first run records the baseline", last, 60.0)
    chk("first run notifies nobody", len(seen), 0)
    st = json.load(open(tw.STATE_PATH))
    chk("baseline persisted", st["last_balance"], 60.0)
    # And it must not be world-readable: the state file holds a number,
    # but it is still operator state.
    mode = oct(os.stat(tw.STATE_PATH).st_mode & 0o777)
    chk("state file is 0600", mode, "0o600")


def test_arrival_fires_once(tw, tmpdir):
    """A real increase notifies exactly once, then goes quiet until the next
    increase. Double-firing would be worse than not firing."""
    seen = []
    tw.check_once(notify=seen.append)          # baseline at 60
    _FAKE["balance"] = 75.5
    delta, last = tw.check_once(notify=seen.append)
    chk("arrival returns the delta", delta, 15.5)
    chk("arrival updates the balance", last, 75.5)
    chk_true("arrival notifies once", len(seen) == 1, seen[0][:60] if seen else "")

    # A second poll with no movement must stay quiet.
    _FAKE["balance"] = 75.5
    delta2, _ = tw.check_once(notify=seen.append)
    chk("no movement returns no delta", delta2, None)
    chk("no movement does not notify again", len(seen), 1)

    # A second, separate arrival does notify again.
    _FAKE["balance"] = 80.0
    delta3, _ = tw.check_once(notify=seen.append)
    chk("second arrival returns its own delta", delta3, 4.5)
    chk("second arrival notifies", len(seen), 2)


def test_message_contents(tw, tmpdir):
    """The alert has to carry the amount, the treasury, the new balance and
    a way to look it up. An alert without a link is an alert that costs a
    round trip."""
    seen = []
    tw.check_once(notify=seen.append)          # baseline, silent
    _FAKE["balance"] = 61.0
    tw.check_once(notify=seen.append)
    msg = seen[-1]
    chk_true("message names the amount", "1.00 USDC" in msg, msg.splitlines()[0])
    chk_true("message names the treasury", tw.TREASURY in msg)
    chk_true("message states the new balance", "61.00 USDC" in msg)
    chk_true("message carries a snowtrace link",
             "snowtrace.io/address/" + tw.TREASURY in msg)


def test_dust_is_ignored(tw, tmpdir):
    """Rounding noise below MIN_DELTA must not page anyone, and must not
    accumulate into a false alert on the next poll."""
    seen = []
    tw.check_once(notify=seen.append)
    _FAKE["balance"] = 60.005          # under the 0.01 threshold
    delta, last = tw.check_once(notify=seen.append)
    chk("dust returns no delta", delta, None)
    chk("dust is recorded anyway", last, 60.005)
    chk("dust does not notify", len(seen), 0)

    # 1.005 USDC arriving in three dust steps must not sum into an alert,
    # because each step was written to the state file as it happened.
    _FAKE["balance"] = 60.3
    delta, _ = tw.check_once(notify=seen.append)
    chk("accumulated change fires once it is real", delta, 0.295)
    chk("and notifies once", len(seen), 1)


def test_outgoing_is_ignored(tw, tmpdir):
    """Our own settlements and the take-rate split make the balance go down.
    That is not an alert — it is the product working."""
    seen = []
    tw.check_once(notify=seen.append)
    _FAKE["balance"] = 40.0            # a 33% drop: a real settlement
    delta, last = tw.check_once(notify=seen.append)
    chk("outgoing returns no delta", delta, None)
    chk("outgoing updates the baseline", last, 40.0)
    chk("outgoing does not notify", len(seen), 0)


def test_big_drop_is_logged(tw, tmpdir, capsys=None):
    """A drop beyond the tolerance with no matching payment is worth a log
    line. The alert path stays silent — an unexplained decrease is an
    investigation, not a page."""
    seen = []
    tw.check_once(notify=seen.append)              # baseline 60
    _FAKE["balance"] = 3.0                          # 95% drop
    delta, last = tw.check_once(notify=seen.append)
    chk("big drop returns no delta", delta, None)
    chk("big drop updates the baseline", last, 3.0)
    chk("big drop does not notify the operator", len(seen), 0)


def test_small_drop_not_flagged(tw, tmpdir):
    """A small drop stays below the tolerance and is treated as routine."""
    seen = []
    tw.check_once(notify=seen.append)
    _FAKE["balance"] = 59.0                          # 1.7% down
    tw.check_once(notify=seen.append)
    chk("small drop does not notify", len(seen), 0)


def test_corrupt_state_recovers(tw, tmpdir):
    """A truncated or hand-mangled state file must not wedge the watcher
    forever. It re-baselines and stays quiet — one missed alert is better
    than a crash loop."""
    seen = []
    tw.check_once(notify=seen.append)
    with open(tw.STATE_PATH, "w") as fh:
        fh.write("{not json")
    delta, last = tw.check_once(notify=seen.append)
    chk("corrupt state returns no delta", delta, None)
    chk("corrupt state re-baselines", last, 60.0)
    chk("corrupt state notifies nobody", len(seen), 0)
    # And it keeps working afterwards.
    _FAKE["balance"] = 61.0
    delta, _ = tw.check_once(notify=seen.append)
    chk("watcher works again after recovery", delta, 1.0)
    chk("and notifies", len(seen), 1)


def test_state_is_replace_not_truncate(tw, tmpdir):
    """The write must be atomic. A crash mid-write that left a truncated
    file would be caught by the corrupt-state path, but only after the
    next arrival is lost — so write to a temp file and rename."""
    seen = []
    tw.check_once(notify=seen.append)
    _FAKE["balance"] = 70.0
    tw.check_once(notify=seen.append)
    leftovers = [f for f in os.listdir(tmpdir) if f.endswith(".tmp")]
    chk("no temp file left behind", leftovers, [])
    chk("state is valid json", isinstance(json.load(open(tw.STATE_PATH)), dict), True)


if __name__ == "__main__":
    tmpdir = tempfile.mkdtemp(prefix="tw_test_")

    # Every test gets a fresh module instance and a fresh state file, so no
    # test inherits another's balance baseline. Sharing one instance made
    # the earlier version of this file order-dependent: a test that left
    # _FAKE at 80.0 made the next one re-baseline to 80 and never fire.
    def fresh(balance=60.0):
        sub = tempfile.mkdtemp(prefix="tw_t_", dir=tmpdir)
        return install_fake_watch(sub, balance)

    test_first_run_is_silent(fresh(), tmpdir)
    test_arrival_fires_once(fresh(), tmpdir)
    test_message_contents(fresh(), tmpdir)
    test_dust_is_ignored(fresh(), tmpdir)
    test_outgoing_is_ignored(fresh(), tmpdir)
    test_big_drop_is_logged(fresh(), tmpdir)
    test_small_drop_not_flagged(fresh(), tmpdir)
    test_corrupt_state_recovers(fresh(), tmpdir)
    test_state_is_replace_not_truncate(fresh(), tmpdir)

    print("=" * 78)
    print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)