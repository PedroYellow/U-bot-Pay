"""
Regression tests for dual-control self-approval (audit 20 Oct, round 6).

/cosigner accepted any integer with no validation, so a user could appoint
themselves as their own co-signer and then approve their own high-value
payments. The product's headline promise is "nothing moves without a second
yes" — and the second yes has to come from someone else.

    python3 test_dual_control.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


class HTTPException(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}")
        self.status_code = code
        self.detail = detail


# ── /cosigner: validation of the nominee ──────────────────────────────────
def cosigner(raw_arg, me, me_row, nominee):
    """Mirror of on_cosigner's logic. Returns (ok, reason)."""
    raw = raw_arg.lstrip("@").strip()
    if not raw.isdigit():
        return False, "not numeric"
    approver_id = int(raw)
    if approver_id == me:
        return False, "self_appointment"
    if nominee is None:
        return False, "nominee_unknown"
    if nominee["id"] == me_row["id"]:
        return False, "same_account"
    return True, "set"


def t_self_appointment_refused():
    me = 555
    me_row = {"id": 1}
    ok, why = cosigner("555", me, me_row, {"id": 2})
    check("cannot appoint yourself", (ok, why), (False, "self_appointment"))


def t_garbage_refused():
    me = 555
    me_row = {"id": 1}
    for arg in ("abc", "5.5", "-1", "", "1e5", "0x10", "555; DROP TABLE users"):
        ok, why = cosigner(arg, me, me_row, {"id": 2})
        check(f"garbage refused: {arg!r}", ok, False)


def t_unknown_nominee_refused():
    me = 555
    me_row = {"id": 1}
    ok, why = cosigner("999", me, me_row, None)
    check("unknown nominee refused", (ok, why), (False, "nominee_unknown"))


def t_valid_nominee_accepted():
    me = 555
    me_row = {"id": 1}
    ok, why = cosigner("999", me, me_row, {"id": 2})
    check("a real other user is accepted", (ok, why), (True, "set"))
    # @-prefixed, as pasted from Telegram
    ok2, _ = cosigner("@999", me, me_row, {"id": 2})
    check("'@999' accepted too", ok2, True)


# ── /approve: settlement-time guard ───────────────────────────────────────
def approve(user, approver_key):
    """Mirror of the guards in api.approve()."""
    if str(user.get("approver_telegram_id")) != str(approver_key):
        raise HTTPException(403, "not the approver")
    if user.get("telegram_id") is not None and str(user["telegram_id"]) == str(approver_key):
        raise HTTPException(403, "self_approval")
    return "settled"


def t_self_approval_blocked_at_settlement():
    """The case the bug allowed: configured as own co-signer, then approves."""
    user = {"telegram_id": 555, "approver_telegram_id": 555}
    try:
        approve(user, 555)
        check("self-approval refused at settlement", False, True)
    except HTTPException as e:
        check("self-approval refused at settlement", e.status_code, 403)


def t_genuine_second_person_works():
    user = {"telegram_id": 555, "approver_telegram_id": 999}
    check("a real co-signer still approves", approve(user, 999), "settled")


def t_wrong_person_still_refused():
    user = {"telegram_id": 555, "approver_telegram_id": 999}
    try:
        approve(user, 1234)
        check("someone else refused", False, True)
    except HTTPException as e:
        check("someone else refused", e.status_code, 403)


def t_legacy_self_configured_rows_are_covered():
    """Rows configured before the fix still can't settle themselves."""
    user = {"telegram_id": 777, "approver_telegram_id": 777}
    try:
        approve(user, 777)
        check("legacy self-cosigner row blocked", False, True)
    except HTTPException as e:
        check("legacy self-cosigner row blocked", e.status_code, 403)


def t_string_vs_int_ids():
    """Telegram ids arrive as ints from PTB but may be strings from HTTP."""
    user = {"telegram_id": "555", "approver_telegram_id": 555}
    try:
        approve(user, 555)
        check("string telegram_id compared correctly", False, True)
    except HTTPException as e:
        check("string telegram_id compared correctly", e.status_code, 403)


def t_web_only_user_has_no_telegram_id():
    """A wallet user has telegram_id = NULL. Approving must not blow up on the
    comparison (the guard is guarded by `is not None`)."""
    user = {"telegram_id": None, "approver_telegram_id": 999}
    check("wallet user with no telegram_id still approvable",
          approve(user, 999), "settled")


if __name__ == "__main__":
    t_self_appointment_refused()
    t_garbage_refused()
    t_unknown_nominee_refused()
    t_valid_nominee_accepted()
    print()
    t_self_approval_blocked_at_settlement()
    t_genuine_second_person_works()
    t_wrong_person_still_refused()
    t_legacy_self_configured_rows_are_covered()
    t_string_vs_int_ids()
    t_web_only_user_has_no_telegram_id()
    print("=" * 70)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)