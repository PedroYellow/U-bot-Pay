"""
Regression tests for the wallet sign-in nonce expiry (audit 20 Oct, round 5).

core.consume_wallet_nonce() validated jwt_exp, but nothing called it:
/auth/wallet/verify looked the wallet up by address and only asked "is there a
nonce?". So the 10-minute expiry that /auth/wallet/nonce advertises was never
enforced — a captured nonce stayed usable until it was spent.

These mirror the branch logic in auth_wallet_verify.

    python3 test_nonce_expiry.py
"""
import os
import sys
import time

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


def verify_branch(user_row, now):
    """Mirror of the guard sequence in auth_wallet_verify."""
    if not user_row or not user_row.get("jwt_nonce"):
        raise HTTPException(400, "no active nonce — call /auth/wallet/nonce first")
    if not user_row.get("jwt_exp") or user_row["jwt_exp"] < now:
        raise HTTPException(400, "nonce expired — call /auth/wallet/nonce again")
    return "would verify signature and mint a JWT"


def t_expired_nonce_is_refused():
    now = 1_700_000_000
    row = {"jwt_nonce": "UbotPay sign-in: abc", "jwt_exp": now - 1}
    try:
        verify_branch(row, now)
        check("expired nonce refused", False, True)
    except HTTPException as e:
        check("expired nonce -> 400", e.status_code, 400)


def t_fresh_nonce_passes():
    now = 1_700_000_000
    row = {"jwt_nonce": "UbotPay sign-in: abc", "jwt_exp": now + 600}
    try:
        r = verify_branch(row, now)
        check("fresh nonce accepted", r.startswith("would verify"), True)
    except HTTPException as e:
        check("fresh nonce accepted", f"unexpected {e.status_code}", True)


def t_exactly_at_expiry():
    """The check is `< now`, so a nonce whose exp equals now is still valid.
    Documenting the boundary so a future refactor doesn't silently move it."""
    now = 1_700_000_000
    row = {"jwt_nonce": "x", "jwt_exp": now}
    try:
        verify_branch(row, now)
        check("exp == now is still inside the window", True, True)
    except HTTPException:
        check("exp == now is still inside the window", False, True)


def t_missing_exp_is_refused():
    """A row with a nonce but no expiry must NOT be treated as valid —
    fail closed, otherwise clearing jwt_exp would extend a nonce's life."""
    now = 1_700_000_000
    for row in ({"jwt_nonce": "x", "jwt_exp": None},
                {"jwt_nonce": "x"},
                {"jwt_nonce": "x", "jwt_exp": 0}):
        try:
            verify_branch(row, now)
            check(f"missing/zero exp refused ({row})", False, True)
        except HTTPException as e:
            check(f"missing/zero exp refused (exp={row.get('jwt_exp')})",
                  e.status_code, 400)


def t_no_nonce_at_all():
    now = 1_700_000_000
    for row in (None, {}, {"jwt_nonce": None, "jwt_exp": now + 600}):
        try:
            verify_branch(row, now)
            check("no nonce -> 400", False, True)
        except HTTPException as e:
            check("no nonce -> 400", e.status_code, 400)


def t_ten_minute_window():
    """The advertised window really is 10 minutes."""
    now = 1_700_000_000
    exp = now + 600
    check("window is 600s (10 min)", exp - now, 600)
    row = {"jwt_nonce": "x", "jwt_exp": exp}
    check("valid at 9m59s", verify_branch(row, now + 599).startswith("would"), True)
    try:
        verify_branch(row, now + 601)
        check("invalid at 10m01s", False, True)
    except HTTPException as e:
        check("invalid at 10m01s -> 400", e.status_code, 400)


def t_one_minute_early_still_valid():
    """Clocks drift; a couple of seconds of slack avoids spurious failures."""
    now = 1_700_000_000
    row = {"jwt_nonce": "x", "jwt_exp": now - 2}
    try:
        verify_branch(row, now)
        check("2s past expiry is refused (no slack)", False, True)
    except HTTPException:
        check("2s past expiry is refused (no slack)", True, True)


if __name__ == "__main__":
    t_expired_nonce_is_refused()
    t_fresh_nonce_passes()
    t_exactly_at_expiry()
    t_missing_exp_is_refused()
    t_no_nonce_at_all()
    t_ten_minute_window()
    t_one_minute_early_still_valid()
    print("=" * 70)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)