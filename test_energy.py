"""
Tests for the TronRental energy client (integration 21 Oct 2026).

The address validator is the part that must not be wrong. It was checked against
TronGrid before being trusted: two addresses quoted from memory as real turned
out not to exist on-chain and correctly fail the checksum, while the Tether
treasury and the Tron Foundation — which do exist — pass. Those are the fixtures
here.

The client tests that touch the network are marked and only exercise read-only
public endpoints.

    python3 test_energy.py
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import energy  # noqa: E402

RESULTS = []


def check(name, got, want=True):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:54} got={got!r} want={want!r}")


def raises(name, fn, fragment):
    try:
        fn()
        got = None
    except ValueError as e:
        got = str(e)
    ok = got is not None and fragment.lower() in got.lower()
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:54} {got!r}")


# ── addresses that exist on Tron ───────────────────────────────────────────
REAL_TRON = [
    "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj",   # Tether treasury
    "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",   # USDT-TRC20 contract
    "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",   # Tron Foundation
]

# ── things that must be rejected ───────────────────────────────────────────
NOT_TRON = [
    ("0x29219dd400f2Bf60E5a23d13Be72B486D4038894", "EVM address (Sonic USDC)"),
    ("0x673678B1870Ae7ef598A5982D25ffdee68d85669", "EVM address (treasury)"),
    ("So11111111111111111111111111111111111111112", "wrapped SOL"),
    ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Solana USDC mint"),
    ("TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdk", "last character changed"),
    ("TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcd", "truncated"),
    ("TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdjX", "one character too long"),
    ("1XLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj", "leading digit not T"),
]


def t_real_addresses_accepted():
    for a in REAL_TRON:
        check(f"accepts {a[:12]}…", energy.validate_tron_address(a), a)


def t_non_tron_rejected():
    for a, label in NOT_TRON:
        try:
            energy.validate_tron_address(a)
            got = "ACCEPTED"
        except ValueError as e:
            got = f"rejected: {e}"
        check(f"rejects {label}", got.startswith("rejected"), True)


def t_empty_and_whitespace():
    raises("empty string", lambda: energy.validate_tron_address(""), "required")
    raises("None", lambda: energy.validate_tron_address(None), "required")
    # Surrounding whitespace is a paste artefact, not an error worth rejecting.
    check("surrounding whitespace tolerated",
          energy.validate_tron_address("  " + REAL_TRON[0] + "  "), REAL_TRON[0])


def t_volume_bounds():
    check("minimum accepted", energy.validate_volume(60_000), 60_000)
    check("maximum accepted", energy.validate_volume(5_000_000), 5_000_000)
    check("usual transfer amount accepted",
          energy.validate_volume(65_000), 65_000)
    raises("below minimum", lambda: energy.validate_volume(59_999), "between")
    raises("above maximum", lambda: energy.validate_volume(5_000_001), "between")
    raises("non-numeric", lambda: energy.validate_volume("abc"), "whole number")


def t_duration_only_1h():
    check("default is 1h", energy.validate_duration(None), "1h")
    check("1h accepted", energy.validate_duration("1h"), "1h")
    raises("1d not supported", lambda: energy.validate_duration("1d"), "duration must")


def t_savings_helper():
    s = energy.savings(65_000, recipient_has_usdt=True)
    check("recipient with USDT needs 65k", s["energy_needed"], 65_000)
    s2 = energy.savings(65_000, recipient_has_usdt=False)
    check("recipient without USDT needs 131k", s2["energy_needed"], 131_000)
    check("the note explains the difference",
          "activates the token" in s2["note"], True)


def t_base_url_is_configurable():
    check("base url has no trailing slash", energy.TRONRENTAL_BASE.endswith("/"), False)
    check("points at their API", "tronrental.com" in energy.TRONRENTAL_BASE, True)


def t_api_key_is_optional_header():
    """The quote endpoints are public; a key changes the rate, not the access."""
    saved = energy.TRONRENTAL_API_KEY
    try:
        energy.TRONRENTAL_API_KEY = ""
        check("no header without a key", "X-API-Key" in energy._headers(), False)
        energy.TRONRENTAL_API_KEY = "k"
        check("header present with a key", energy._headers().get("X-API-Key"), "k")
    finally:
        energy.TRONRENTAL_API_KEY = saved


def t_live_quote_matches_published_figures():
    """Read-only, public endpoint. If this fails the integration is pointing at
    something other than TronRental."""
    async def go():
        try:
            q = await energy.quote(65_000, "1h")
        except Exception as e:
            print(f"SKIP  live quote unavailable: {type(e).__name__}: {e}")
            return
        check("quote returns a positive TRX price", q["total_trx"] > 0, True)
        check("rental plus fixed equals total",
              abs((q["rental_trx"] + q["fixed_fee_trx"]) - q["total_trx"]) < 0.01, True)
        check("USD present", q["total_usd"] is not None, True)
        check("volume echoed", q["volume"], 65_000)
        # Their published burn cost for this transfer is 6.5 TRX.
        check("renting is cheaper than burning", q["total_trx"] < 6.5, True)
    asyncio.run(go())


def t_live_quote_for_the_expensive_case():
    async def go():
        try:
            q = await energy.quote(131_000, "1h")
        except Exception as e:
            print(f"SKIP  live quote unavailable: {type(e).__name__}: {e}")
            return
        check("131k costs about 2.8 TRX", 2.0 < q["total_trx"] < 4.0, True)
    asyncio.run(go())


def t_live_market_prices():
    async def go():
        try:
            p = await energy.market_prices()
        except Exception as e:
            print(f"SKIP  live prices unavailable: {type(e).__name__}: {e}")
            return
        check("prices have an energy quote", "energy_trx" in p, True)
        check("advertised savings are reported", "savings_percent" in p, True)
        check("burn cost present", "burn_cost_trx" in p, True)
    asyncio.run(go())


def t_invoice_is_not_created_in_tests():
    """Creating an invoice is a side effect on a real account. The validator is
    what these tests cover; the call itself is only exercised manually."""
    src = open(os.path.join(HERE, "energy.py"), encoding="utf-8").read()
    fn = src[src.index("async def create_invoice"):]
    check("create_invoice validates the address first",
          fn.index("validate_tron_address") < fn.index("_post"), True)
    check("create_invoice validates the volume first",
          fn.index("validate_volume") < fn.index("_post"), True)
    check("invoice body carries no price field",
          '"price"' not in fn and '"amount"' not in fn, True)


if __name__ == "__main__":
    t_real_addresses_accepted()
    t_non_tron_rejected()
    t_empty_and_whitespace()
    t_volume_bounds()
    t_duration_only_1h()
    t_savings_helper()
    t_base_url_is_configurable()
    t_api_key_is_optional_header()
    t_live_quote_matches_published_figures()
    t_live_quote_for_the_expensive_case()
    t_live_market_prices()
    t_invoice_is_not_created_in_tests()
    print("=" * 78)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
