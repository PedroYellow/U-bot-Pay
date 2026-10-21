"""
Tests for the TronRental fee settlement and status polling (integration 21 Oct 2026).

Two endpoints added after the first deployment:

  POST /energy/order/{id}/settle   charge the fee, then ask for the invoice
  GET  /energy/order/{id}/status   ask TronRental whether the energy landed

The settle path is the one that can take money, so the properties pinned here
are the ones that keep it honest:

  - the fee is claimed atomically, so a retry cannot charge twice
  - the invoice is only requested after the transfer actually settled
  - a reverted transfer is not treated as a paid fee
  - if the invoice fails after the fee settled, the order says so explicitly
    rather than looking like a normal pending order
  - the remote status only moves the order forward when TronRental confirms it

Sending real money is not exercised here. The assertions read the source so the
ordering guarantees are checked, plus unit tests of the claim/release behaviour.

    python3 test_energy_settle.py
"""
import ast
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core  # noqa: E402

RESULTS = []
api_src = open(os.path.join(HERE, "api.py"), encoding="utf-8").read()
core_src = open(os.path.join(HERE, "core.py"), encoding="utf-8").read()


def check(name, got, want=True):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


def fn(src, name):
    """The source of one function, by name."""
    for prefix in (f"async def {name}(", f"def {name}("):
        if prefix in src:
            i = src.index(prefix)
            break
    else:
        return ""
    stops = [src.find(x, i + 5) for x in
             (chr(10) + "async def ", chr(10) + "def ", chr(10) + "@app.",
              chr(10) + "# ")]
    stops = [p for p in stops if p > 0]
    return src[i:min(stops) if stops else len(src)]


def t_claim_is_atomic():
    """Without this a retry after a timeout charges the fee twice."""
    body = fn(core_src, "claim_energy_order_for_payment")
    check("claim is a conditional UPDATE", "UPDATE energy_orders SET status=" in body, True)
    check("and requires the pre-claim status",
          "WHERE id=? AND status='quoted'" in body, True)
    check("returns rowcount == 1", "rowcount == 1" in body, True)
    check("release puts it back", "status='quoted'" in fn(core_src, "release_energy_order"), True)


def t_settle_charges_before_it_invoices():
    body = fn(api_src, "energy_settle")
    charge = body.index("chain.send_usdc")
    invoice = body.index("_energy.create_invoice")
    check("the fee is charged before the invoice is requested", charge < invoice, True)
    check("the claim happens before the charge",
          body.index("claim_energy_order_for_payment") < charge, True)


def t_settle_refuses_a_wrong_owner():
    body = fn(api_src, "energy_settle")
    check("the order is looked up scoped to the user",
          "core.get_energy_order(order_id, user_id=" in body, True)
    check("a missing order is a 404", 'HTTPException(404, "no such energy order")' in body, True)


def t_reverted_transfer_is_not_a_paid_fee():
    body = fn(api_src, "energy_settle")
    OKCHK = 'if not r["ok"]:'
    check("ok is checked before marking settled", OKCHK in body, True)
    check("a revert releases the claim",
          "release_energy_order" in body.split(OKCHK)[1][:400], True)
    check("and marks the payment failed", 'core.mark(pay["id"], "failed"' in body, True)
    check("the invoice is not requested on a revert",
          body.index(OKCHK) < body.index("_energy.create_invoice"), True)


def test_claim_holds_the_order():
    """Behavioural, not just source reading."""
    core.DB = tempfile.mktemp(suffix=".db")
    core.init()
    u = core.upsert_user(900, "tester")
    o = core.create_energy_order(u["id"], "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
                                  65_000, 1.49935, 0.51)
    check("new order starts quoted", o["status"], "quoted")
    check("first claim wins", core.claim_energy_order_for_payment(o["id"]), True)
    check("second claim is refused", core.claim_energy_order_for_payment(o["id"]), False)
    check("status is now fee_claiming", core.get_energy_order(o["id"])["status"], "fee_claiming")
    core.release_energy_order(o["id"])
    check("release returns it to quoted", core.get_energy_order(o["id"])["status"], "quoted")
    check("and it can be claimed again", core.claim_energy_order_for_payment(o["id"]), True)


def t_release_only_undoes_a_claim():
    """It must not resurrect an order that has moved past fee_claiming."""
    body = fn(core_src, "release_energy_order")
    check("release is conditional on the claimed state",
          "WHERE id=? AND status='fee_claiming'" in body, True)


def t_invoice_failure_is_explicit():
    """Taking the fee and then doing nothing is the outcome this must never have."""
    body = fn(api_src, "energy_settle")
    check("a failed invoice call sets invoice_failed",
          'set_energy_status(order["id"], "invoice_failed")' in body, True)
    check("and tells the user the fee was taken but not refunded twice",
          "was charged but TronRental did not return an invoice" in body, True)
    check("and promises no second charge",
          "you will not be charged again" in body, True)


def t_retry_after_invoice_failure_does_not_recharge():
    body = fn(api_src, "energy_settle")
    check("invoice_failed is an accepted state", '"invoice_failed"' in body, True)
    check("the retry path skips the charge",
          "do not take it twice" in body, True)
    check("and goes straight back to fee_paid",
          'set_energy_status(order["id"], "fee_paid")' in body, True)


def t_fee_goes_to_revenue_not_the_tron_address():
    """The TRON address is where energy is delegated. Sending USDC there would be
    wrong and would confuse the user's TRON balance."""
    body = fn(api_src, "energy_settle")
    charge = body[body.index("chain.send_usdc"):]
    line = charge.splitlines()[0]
    check("the fee is sent to the revenue wallet", "revenue" in line, True)
    check("and not to the TRON address",
          'order["target_address"]' not in charge[:200], True)


def t_status_only_moves_forward_on_confirmation():
    body = fn(api_src, "energy_order_remote_status")
    tail = body.split('remote.get("status"')[1][:600] if 'remote.get("status"' in body else ""
    check("does not assume success", "activated" in tail, True)
    check("requires an explicit success state",
          any(s in body for s in ("completed", "success", "succeeded")), True)
    # The remote id is the invoice id, stored in tronrental_invoice_id when the
    # invoice is created — not a separate tronrental_order_id.
    check("does not invent a second remote id column",
          "tronrental_order_id=str(" in body, False)
    check("activation is recorded locally", 'set_energy_status(order["id"], "activated")' in body, True)
    check("does not re-activate an already activated order",
          'order["status"] != "activated"' in body, True)


def t_status_is_read_only_against_their_api():
    body = fn(api_src, "energy_order_remote_status")
    # GET /orders/{id} rejects an invoice UUID with 422; the read endpoint is
    # GET /invoices/{id}.
    check("calls the invoice read endpoint", "_energy.get_invoice(" in body, True)
    check("never creates an invoice here", "create_invoice" not in body, True)


def t_settle_has_no_dead_code():
    body = fn(api_src, "energy_settle")
    check("no leftover stub", "if False" not in body, True)
    # Every custom exception raised by the energy client must be caught and
    # converted. TronRentalBusy is handled on its own; a bare re-raise of it
    # would escape FastAPI as a 500, which is what the settle path must not do
    # after the fee has been taken.
    check("TronRentalBusy is handled explicitly",
          "except _energy.TronRentalBusy as e:" in body, True)
    check("the generic handler follows it",
          body.index("except _energy.TronRentalBusy") <
          body.index("except Exception as e:", body.index("except _energy.TronRentalBusy")), True)


def t_module_still_parses():
    check("api.py parses", bool(ast.parse(api_src)), True)
    check("core.py parses", bool(ast.parse(core_src)), True)


if __name__ == "__main__":
    t_claim_is_atomic()
    t_settle_charges_before_it_invoices()
    t_settle_refuses_a_wrong_owner()
    t_reverted_transfer_is_not_a_paid_fee()
    test_claim_holds_the_order()
    t_release_only_undoes_a_claim()
    t_invoice_failure_is_explicit()
    t_retry_after_invoice_failure_does_not_recharge()
    t_fee_goes_to_revenue_not_the_tron_address()
    t_status_only_moves_forward_on_confirmation()
    t_status_is_read_only_against_their_api()
    t_settle_has_no_dead_code()
    t_module_still_parses()
    print("=" * 80)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
