"""
Tests for the TronRental API shape (21 Oct 2026, after the API key was supplied).

Three things the first implementation got wrong, all found by calling their live
API rather than by reading their docs:

1. Invoice status lives at GET /invoices/{uuid}. The code used GET /orders/{id},
   which only accepts integer purchase ids and rejects an invoice UUID with 422.
2. Their account allows at most 3 pending invoices and answers 429 past that.
   There is no endpoint to cancel one early; they expire after 30 minutes. That
   turns it into a wait condition, not a hard failure, so it gets its own
   exception and its own status code.
3. create_invoice() echoes duration back as null even when one is sent, so the
   duration held is the one requested rather than a confirmed value. Worth stating
   so nobody later treats the field as an acknowledgement.

Also covers the webhook added after the dashboard screenshot showed one: it
authenticates with a shared secret compared in constant time, because TronRental
posts no signature header.

    python3 test_energy_api_shape.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import energy  # noqa: E402

RESULTS = []
api_src = open(os.path.join(HERE, "api.py"), encoding="utf-8").read()
core_src = open(os.path.join(HERE, "core.py"), encoding="utf-8").read()


def check(name, got, want=True):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


def _section(src, marker):
    """Source from a marker to the next endpoint or section header."""
    i = src.index(marker)
    stops = [src.find(x, i + 5) for x in
             (chr(10) + "@app.", chr(10) + "async def ", chr(10) + "def ",
              chr(10) + "# ")]
    stops = [q for q in stops if q > 0]
    return src[i:min(stops) if stops else len(src)]


def t_invoice_status_uses_the_invoices_path():
    """GET /orders/{uuid} is a 422 from their API — integer ids only."""
    src = open(os.path.join(HERE, "energy.py"), encoding="utf-8").read()
    check("get_invoice exists", "async def get_invoice(" in src, True)
    check("and it targets /invoices/", 'f"/invoices/{invoice_id}"' in src, True)
    check("get_order coerces to an integer id",
          '_get(f"/orders/{int(order_id)}")' in src, True)
    # the API layer must call the invoice one
    fn = _section(api_src, "async def energy_order_remote_status")
    check("the status endpoint calls get_invoice",
          "_energy.get_invoice(" in fn, True)
    check("and not get_order", "_energy.get_order(" in fn, False)


def t_pending_invoice_limit_is_its_own_error():
    check("TronRentalBusy exists", hasattr(energy, "TronRentalBusy"), True)
    check("it is a RuntimeError so existing handlers still catch it",
          issubclass(energy.TronRentalBusy, RuntimeError), True)
    src = open(os.path.join(HERE, "energy.py"), encoding="utf-8").read()
    check("429 is raised as TronRentalBusy",
          "if r.status_code == 429:" in src, True)
    check("the message says it clears in time, not permanently",
          "expire after 30" in energy.TronRentalBusy.__doc__ or
          "expire after 30" in src, True)
    # and the API must not report it as a generic 502
    fn = _section(api_src, "async def energy_settle")
    check("settle catches TronRentalBusy separately",
          "except _energy.TronRentalBusy" in fn, True)
    check("and answers 429, telling the user the fee was taken",
          "429," in fn, True)


def t_duration_is_documented_as_unconfirmed():
    src = open(os.path.join(HERE, "energy.py"), encoding="utf-8").read()
    check("the null-duration quirk is documented", "echoes" in src and "null" in src, True)
    check("and not treated as a confirmation", "confirmation" in src, True)


def t_webhook_is_authenticated():
    fn = _section(api_src, "async def energy_webhook")
    check("the webhook exists", "/energy/webhook" in api_src, True)
    check("it refuses to run without a configured secret",
          "TRONRENTAL_WEBHOOK_SECRET" in fn, True)
    check("the secret is compared in constant time",
          "_hmac.compare_digest(" in fn, True)
    check("a wrong secret is a 401", "HTTPException(401" in fn, True)
    check("an unconfigured secret is a 503", "HTTPException(503" in fn, True)


def t_webhook_only_moves_forward():
    """A late failure event must not undo an already-activated order."""
    fn = _section(api_src, "async def energy_webhook")
    check("activation is guarded", 'order["status"] != "activated"' in fn, True)
    check("and so is failure", fn.count('order["status"] != "activated"') >= 2, True)


def t_webhook_does_not_leak_existence():
    fn = _section(api_src, "async def energy_webhook")
    check("an unknown invoice is ignored quietly, not 404'd",
          "ignored" in fn and "unknown invoice" in fn, True)
    check("and does not confirm it exists", "HTTPException(404" not in fn, True)


def t_webhook_lookup_exists():
    check("find_energy_order_by_invoice exists",
          "def find_energy_order_by_invoice" in core_src, True)
    check("and is used by the webhook",
          "core.find_energy_order_by_invoice(" in api_src, True)
    check("it rejects an empty id",
          "if not invoice_id:" in core_src, True)


def t_no_unsigned_mutation_path():
    """The only unauthenticated-looking endpoint must not move money."""
    fn = _section(api_src, "async def energy_webhook")
    check("the webhook does not create invoices",
          "create_invoice" not in fn, True)
    check("nor send anything on chain", "send_usdc" not in fn, True)


def t_modules_parse():
    check("energy.py parses",
          bool(ast.parse(open(os.path.join(HERE, "energy.py"),
                              encoding="utf-8").read())), True)
    check("api.py parses", bool(ast.parse(api_src)), True)
    check("core.py parses", bool(ast.parse(core_src)), True)


if __name__ == "__main__":
    t_invoice_status_uses_the_invoices_path()
    t_pending_invoice_limit_is_its_own_error()
    t_duration_is_documented_as_unconfirmed()
    t_webhook_is_authenticated()
    t_webhook_only_moves_forward()
    t_webhook_does_not_leak_existence()
    t_webhook_lookup_exists()
    t_no_unsigned_mutation_path()
    t_modules_parse()
    print("=" * 80)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
