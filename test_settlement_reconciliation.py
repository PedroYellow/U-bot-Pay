"""
Regression tests for settlement reconciliation (audit 20 Oct round 16).

send_usdc() reports failure two different ways, and only one of them is an
exception:

    ok=False    the transaction mined and reverted (rcpt.status != 1)
    exception   the RPC call, the signing, or the receipt wait blew up

Only the second one reached an except block. Every call site did
`core.mark(..., "settled" if r["ok"] else "failed", ...)` — that part was right —
and then carried on as if it had succeeded. Reproduced on the production venv with
a stubbed chain:

    r['ok']=False -> DB status=failed | response action='settled' | push "Approved & sent"

Four consequences, in increasing order of cost:

  1. the user is told the money was sent
  2. the invoice is marked paid against a transfer that did not happen
  3. the co-signer is told "Approved & sent" — a human who authorised the payment
     is told a lie about it
  4. the x402 take-rate is recorded as revenue the product never received

    python3 test_settlement_reconciliation.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
API_PY = os.path.join(HERE, "api.py")
RESULTS = []

src = open(API_PY, encoding="utf-8").read()
# Comments are stripped: one explains the double-spend fix and mentions
# send_usdc(), which would otherwise be counted as a fourth call site.
code = re.sub(r"#.*", "", src)

OK_CHECK = 'if not r["ok"]'


def check(name, got, want=True):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:52} got={got!r} want={want!r}")


def call_sites():
    """(line, following-code, enclosing-function) for each real send_usdc call.

    The calls are asyncio.to_thread(chain.send_usdc, a, b, ...) — a comma
    argument list, so send_usdc(...) matches nothing but a comment.
    """
    out = []
    for m in re.finditer(r"send_usdc\s*,", code):
        before = code[:m.start()]
        fn = re.findall(r"(?:async )?def (\w+)\(", before)[-1]
        out.append((before.count("\n") + 1,
                    code[m.start():m.start() + 900], fn))
    return out


def site_in(fn):
    """The call site inside a named function.

    Selected by name rather than position: the x402 take-rate call is the *first*
    send_usdc in the file, not the last, so an index-based lookup checks the wrong
    function and passes for the wrong reason.
    """
    for entry in call_sites():
        if entry[2] == fn:
            return entry
    raise AssertionError("no send_usdc call site inside " + fn)


def _body(seg):
    return seg[:seg.index("except")] if "except" in seg else seg


def t_call_sites_cover_every_payer_path():
    # Three core paths: /turn (chat), _execute (auto-send), _approve_claimed
    # (dual-control approval). energy_settle was added in the TronRental round
    # and is the same call_site pattern with the same r["ok"] discipline,
    # except the destination is the revenue wallet. Any new send_usdc() path
    # belongs in this test.
    call_site_fns = sorted(e[2] for e in call_sites())
    for required in ("turn", "_execute", "_approve_claimed", "energy_settle"):
        check(f"{required} calls send_usdc", required in call_site_fns, True)


def t_every_site_checks_ok():
    unchecked = [ln for ln, seg, _ in call_sites()
                 if not re.search(r'\b(?:r|tk)\s*\[\s*"ok"\s*\]'
                                  r'|\b(?:r|tk)\.get\(\s*"ok"', seg[:600])]
    check("every call site checks the ok flag", unchecked, [])


def t_user_is_not_told_the_money_moved():
    _ln, seg, _fn = site_in("_execute")
    body = _body(seg)
    check("_execute has a failure branch", OK_CHECK in body, True)
    check("it returns action=failed", '"action": "failed"' in body, True)
    check("settled is only reported after the ok check",
          body.index(OK_CHECK) < body.index('"action": "settled"'), True)
    check("it says nothing was sent and the funds remain",
          "nothing" in body and "still yours" in body, True)


def t_invoice_only_settled_on_success():
    _ln, seg, _fn = site_in("_execute")
    body = _body(seg)
    check("invoice is paid only after the ok check",
          body.index(OK_CHECK) < body.index("core.pay_invoice"), True)


def t_cosigner_is_not_told_a_lie():
    """/approve pushed "Approved & sent" unconditionally. A human authorised the
    payment; telling them it moved when it reverted is the worst of the four."""
    _ln, seg, _fn = site_in("_approve_claimed")
    check("ok check precedes the success notification",
          seg.index(OK_CHECK) < seg.index("Approved & sent"), True)
    check("a separate notification goes out on revert",
          "REJECTED by the network" in seg, True)
    check("it says nothing was sent", "Nothing was sent" in seg, True)
    # The sentence is written as three adjacent f-strings, so the source reads
    # `... are still " f"in the treasury.` — the words are split across a quote
    # boundary. Collapse everything non-alphanumeric before comparing.
    flat = " ".join(re.sub(r"[^a-z]+", " ", seg.lower()).split())
    # Adjacent f-strings leave a stray "f" from the string prefix, so drop it.
    flat = flat.replace(" f ", " ")
    check("it says the funds are still in the treasury",
          "still in the treasury" in flat, True)
    check("the API returns ok=False on revert", '"ok": False' in seg, True)
    check("the approval itself is still recorded",
          '"settled" if r["ok"] else "failed"' in seg
          and "approved_by=int(a.approver_key)" in seg, True)


def t_take_rate_only_recorded_when_it_settles():
    _ln, seg, _fn = site_in("turn")
    check("revenue recorded only inside the ok branch",
          seg.index('tk.get("ok")') < seg.index('pay["ubotpay_take_usdc"] = take'), True)
    check("a reverted take is logged", "reverted" in seg.lower(), True)


def t_reverts_are_logged_not_silent():
    """Nothing was logged or raised before, which is how it went unnoticed."""
    check("reverts are logged in more than one place",
          src.lower().count("reverted") >= 3, True)


def t_module_still_parses():
    check("api.py parses", bool(ast.parse(src)), True)


if __name__ == "__main__":
    t_call_sites_cover_every_payer_path()
    t_every_site_checks_ok()
    t_user_is_not_told_the_money_moved()
    t_invoice_only_settled_on_success()
    t_cosigner_is_not_told_a_lie()
    t_take_rate_only_recorded_when_it_settles()
    t_reverts_are_logged_not_silent()
    t_module_still_parses()
    print("=" * 74)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
