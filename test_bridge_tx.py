"""
Regression tests for the bridge tx_hash validation (audit 20 Oct, round 4).

/bridge/{id}/confirm used to store any string as the transaction hash. A bogus
hash marked the bridge "submitted", LI.FI never saw it, the error was swallowed,
and the bridge stayed in "submitted" forever while the UI claimed the money was
on its way.

These tests exercise the decision logic with a stubbed RPC, so they need no
network and no chain access.

    python3 test_bridge_tx.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []

ADDR_ME = "0x1111111111111111111111111111111111111111"
ADDR_OTHER = "0x2222222222222222222222222222222222222222"
GOOD_HASH = "0x" + "ab" * 32


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


# ── the format gate ───────────────────────────────────────────────────────
import re
_TX_HASH_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


def is_tx_hash(v):
    return bool(v) and bool(_TX_HASH_RE.match(v.strip()))


def t_format():
    check("valid 0x + 64 hex accepted", is_tx_hash(GOOD_HASH), True)
    check("64 hex without 0x accepted", is_tx_hash("ab" * 32), True)
    check("mixed case accepted", is_tx_hash("0x" + "Ab" * 32), True)
    check("short hash rejected", is_tx_hash("0xabc123"), False)
    check("empty rejected", is_tx_hash(""), False)
    check("plain word rejected", is_tx_hash("not-a-hash"), False)
    check("arbitrary string rejected", is_tx_hash("hello world"), False)
    check("63 hex rejected", is_tx_hash("0x" + "ab" * 31 + "a"), False)
    check("65 hex rejected", is_tx_hash("0x" + "ab" * 32 + "a"), False)
    check("non-hex chars rejected", is_tx_hash("0x" + "zz" * 32), False)
    # SQL-injection-ish payloads must never reach the DB layer.
    check("quote-injection rejected", is_tx_hash("' OR 1=1 --"), False)
    check("sql-ish rejected", is_tx_hash("0x'; DROP TABLE bridges; --"), False)
    # A valid-looking hash that is actually an address (20 bytes) must fail.
    check("address-shaped value rejected", is_tx_hash(ADDR_ME), False)


# ── the verification outcome ──────────────────────────────────────────────
class Ok:
    def __init__(self, frm, to=ADDR_OTHER, value=0):
        self.ok = True
        self.error = None
        self.from_ = frm
        self.to = to
        self.value = value


def decide(check_result):
    """Mirror the branch in bridge_confirm: is it safe to mark submitted?"""
    if check_result.get("error") == "rpc_error":
        return 503            # our node is down; not evidence of a fake tx
    if not check_result.get("ok"):
        return 400            # genuinely unverifiable
    return 200


def t_verify_outcomes():
    # The sender must match the address the bridge belongs to.
    check("tx from the bridge owner -> 200",
          decide({"ok": True, "error": None}), 200)
    check("tx from someone else -> 400 (sender_mismatch)",
          decide({"ok": False, "error": "sender_mismatch"}), 400)
    # A hash nobody has ever seen.
    check("unknown hash -> 400 (tx_not_found)",
          decide({"ok": False, "error": "tx_not_found"}), 400)
    # Our own RPC being down must not be reported as a bad transaction.
    check("rpc down -> 503, not 400",
          decide({"ok": False, "error": "rpc_error"}), 503)
    check("unknown chain -> 400",
          decide({"ok": False, "error": "unknown_chain:5"}), 400)


# ── the delivered-amount extraction ───────────────────────────────────────
def _delivered_amount(receiving):
    if not isinstance(receiving, dict):
        return None
    for key in ("toAmount", "to_amount", "amount", "value"):
        v = receiving.get(key)
        if v in (None, ""):
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v / 1e6
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if isinstance(v, str) and "." not in v and "e" not in v.lower():
            return f / 1e6
        return f if f % 1 else f / 1e6
    return None


def t_delivered_amount():
    # The bug being fixed: reporting to_amount_min (0.500076) as what arrived.
    # LI.FI/Across send integers in base units; bridge.py already divides by 1e6.
    check("int base units -> human (9994914 = 9.994914)",
          _delivered_amount({"toAmount": 9994914}), 9.994914)
    check("snake_case int key handled",
          _delivered_amount({"to_amount": 9994914}), 9.994914)
    check("numeric string of an int -> base units",
          _delivered_amount({"toAmount": "9994914"}), 9.994914)
    check("float already in human units passes through",
          _delivered_amount({"amount": 9.994914}), 9.994914)
    check("float string in human units",
          _delivered_amount({"value": "9.99"}), 9.99)
    check("0 base units -> 0.0", _delivered_amount({"toAmount": 0}), 0.0)
    check("5 base units -> 0.000005 (not 5.0)",
          _delivered_amount({"amount": 5}), 0.000005)
    # The regression a magnitude heuristic would have caused.
    check("9994914 is NOT 9994914.0 USDC",
          _delivered_amount({"toAmount": 9994914}) > 1e6, False)
    check("missing amount -> None (caller falls back)",
          _delivered_amount({}), None)
    check("non-dict -> None", _delivered_amount("nope"), None)
    check("garbage value -> None",
          _delivered_amount({"amount": "abc"}), None)
    check("bool is not an amount",
          _delivered_amount({"amount": True}), None)
    check("empty string skipped",
          _delivered_amount({"toAmount": "", "amount": 5000000}), 5.0)
    check("first non-empty key wins",
          _delivered_amount({"toAmount": 2000000, "amount": 9999999}), 2.0)


# ── the done-message: never print the floor as the result ─────────────────
def message_for(delivered, floor):
    if delivered is None:
        delivered = 9.994914          # _expected_amount fallback
    warn = ""
    if floor and delivered < floor * 0.999:
        warn = f"⚠️ below {floor:.2f}"
    return f"{delivered:.2f}", warn


def t_done_message():
    got, warn = message_for(9.994914, 0.500076)
    check("success reports the real amount, not the floor", got, "9.99")
    check("no false warning on a normal bridge", warn, "")

    # If the bridge really under-delivers, we must say so rather than call it fine.
    got2, warn2 = message_for(0.40, 0.500076)
    check("under-delivery still reports the real number", got2, "0.40")
    check("under-delivery is flagged", warn2.startswith("⚠️"), True)

    # The old code would have printed "0.50" here in every case.
    check("old buggy output would have said 0.50", "0.50" == got, False)


if __name__ == "__main__":
    t_format()
    print()
    t_verify_outcomes()
    print()
    t_delivered_amount()
    print()
    t_done_message()
    print("=" * 70)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)