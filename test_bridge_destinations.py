"""
Regression tests for non-EVM bridge destinations (audit 20 Oct, Solana round).

Solana is the first destination that is not EVM, and it broke three assumptions
that were previously safe because every supported chain was EVM:

1. /bridge/quote validated to_chain with le=999999. Solana's LI.FI id is
   1151111081099710, so every request was rejected with 422 before reaching the
   bridge.
2. to_address was pinned to pattern ^0x[a-fA-F0-9]{40}$. A Solana destination is a
   base58 address, which that pattern can never match.
3. /balance/{address} swallowed the error and returned usdc=0.0, which reads as
   "this address is broke" rather than "this chain is not readable here".

The address rules below are checked directly, including the base58 alphabet:
Solana's excludes 0, O, I and l, and a length-only check would happily accept
32 zeros.

    python3 test_bridge_destinations.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []
API_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api.py")

SOLANA_LIFI_ID = 1151111081099710
SOL_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WRAPPED_SOL = "So11111111111111111111111111111111111111112"


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:56} got={got!r} want={want!r}")


# Same rules api.py applies, restated here so the test is checking behaviour
# rather than re-reading the file it is meant to cover.
EVM_ADDR = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def t_evm_address_rule_unchanged():
    """Loosening to_chain must not have loosened the EVM address rule — that is
    the rule that stops a bad address reaching a bridge provider."""
    good = "0x" + "11" * 20
    cases = [
        (good, True, "valid 0x address"),
        (good.upper().replace("0X", "0x"), True, "uppercase hex"),
        (good[:-1], False, "39 hex chars"),
        (good + "0", False, "41 hex chars"),
        ("0x" + "1" * 39, False, "wrong length"),
        ("0x" + "z" * 40, False, "non-hex characters"),
        (SOL_USDC, False, "a Solana mint is not an EVM address"),
    ]
    for value, want, label in cases:
        check(f"EVM rule: {label}", bool(EVM_ADDR.match(value)), want)


def t_solana_address_rule():
    cases = [
        (WRAPPED_SOL, True, "wrapped SOL"),
        (SOL_USDC, True, "USDC mint"),
        ("1" * 32, True, "32 valid base58 chars"),
        ("1" * 44, True, "44 valid base58 chars"),
        ("0x" + "11" * 20, False, "an 0x address is not base58"),
        ("0" * 32, False, "all zeros (0 is not in base58)"),
        ("0O" * 20, False, "contains 0 and O"),
        ("I" * 32, False, "contains I (excluded from base58)"),
        ("l" * 32, False, "contains lowercase l (excluded)"),
        ("1" * 31, False, "too short"),
        ("1" * 45, False, "too long"),
    ]
    for value, want, label in cases:
        check(f"Solana rule: {label}", bool(SOLANA_ADDR.match(value)), want)


def t_solana_id_fits_the_raised_bound():
    """The endpoint's upper bound was raised from 999999 so Solana's id fits.
    Assert it actually fits and that the bound is not now unbounded."""
    bound = 2 ** 62
    check("Solana id fits the raised bound", SOLANA_LIFI_ID <= bound, True)
    check("old bound would have rejected it", SOLANA_LIFI_ID <= 999999, False)


def t_only_solana_gets_the_loose_bound():
    """The relaxation should be a named constant, not a blanket 'accept anything'
    — otherwise a typo'd chain id sails through validation."""
    src = open(API_PY, encoding="utf-8").read()
    check("solana id is a named constant", "_SOLANA_CHAIN_ID = 1151111081099710" in src, True)
    check("the guard is scoped to that constant",
          "if to_chain == _SOLANA_CHAIN_ID:" in src, True)


def t_impossible_request_is_not_a_502():
    """A request that can never succeed must not be reported as a transient
    upstream failure.

    get_best_quote() fans out to LI.FI and Across and swallows each partner's
    exception, so before this was fixed a Solana-as-source request surfaced as
    "all bridge partners failed" -> 502, telling the caller to retry something
    that would fail identically forever.
    """
    bridge_src = open(os.path.join(os.path.dirname(API_PY), "bridge.py"), encoding="utf-8").read()
    api_src = open(API_PY, encoding="utf-8").read()
    check("upstream failure has its own exception type",
          "class _BridgeUpstreamError(ValueError)" in bridge_src, True)
    check("get_best_quote rejects destination-only sources up front",
          "if from_chain in _DESTINATION_ONLY_CHAINS:" in bridge_src, True)
    check("and raises it for a genuine upstream failure",
          'raise _BridgeUpstreamError("all bridge partners failed' in bridge_src, True)
    # Subclassing ValueError means ordering in the except ladder is load-bearing.
    # Scope the search to bridge_quote: a file-wide index() would find unrelated
    # earlier occurrences and report on the wrong code.
    fn = api_src[api_src.index("def bridge_quote"):]
    fn = fn[:fn.index("\n@app.")]
    check("unsupported chain caught before upstream",
          fn.index("except _bridge._UnsupportedChain") < fn.index("except _bridge._BridgeUpstreamError"), True)
    check("unsupported chain caught before plain ValueError",
          fn.index("except _bridge._UnsupportedChain") < fn.index("except ValueError as e"), True)
    # A bare `raise` of a non-HTTPException escapes FastAPI as a 500. That is
    # exactly what happened when _BridgeUpstreamError was introduced: the branch
    # re-raised it and the caller saw "500 Internal Server Error" for a request
    # that was simply invalid.
    check("unsupported chain becomes a 400",
          "except _bridge._UnsupportedChain as e:" in fn and "raise HTTPException(400, str(e)[:200])" in fn, True)
    check("upstream failure becomes a 502, not a bare re-raise",
          "except _bridge._BridgeUpstreamError:" in fn
          and "raise HTTPException(502," in fn[fn.index("except _bridge._BridgeUpstreamError"):], True)
    # Every branch on a *custom* exception must convert it to HTTPException.
    # `except HTTPException: raise` is correct as-is — it already is one.
    lines = fn.split("\n")
    offenders = []
    for n, ln in enumerate(lines):
        st = ln.strip()
        if not st.startswith("except ") or "HTTPException" in st:
            continue
        if "_bridge." not in st:            # ValueError/Exception arms all raise below
            continue
        body = "\n".join(lines[n + 1:n + 3])
        if "HTTPException" not in body:
            offenders.append(st)
    check("every custom-exception branch converts to HTTPException", offenders, [])
    # An unknown chain id must be caught before the fan-out, otherwise both
    # partners raise it, the per-partner `except` swallows it twice, and the
    # caller sees 502 instead of 400.
    bq = bridge_src[bridge_src.index("def get_best_quote"):]
    bq = bq[:bq.index("\ndef ")]
    # The map it validates against is now the one for the requested token, so a
    # USDT route for a chain USDC supports (but USDT does not) is rejected up
    # front. Assert the token-aware form, not the older USDC-only literal.
    check("get_best_quote validates both chains before fanning out",
          "if cid not in _tmap:" in bq, True)
    check("and raises the unsupported-chain type",
          "raise _UnsupportedChain(" in bq and "unsupported {label} chain" in bq, True)
    check("check precedes the thread pool",
          bq.index("if cid not in _tmap:") < bq.index("ThreadPoolExecutor"), True)
    check("plain ValueError becomes a 400",
          "except ValueError as e:" in api_src and "HTTPException(400, str(e)[:200])" in api_src, True)


def t_balance_endpoint_refuses_destination_only():
    """Returning 0.0 for an unreadable chain is worse than an error: it reads as
    an empty balance rather than an unsupported request."""
    src = open(API_PY, encoding="utf-8").read()
    check("balance endpoint checks for a missing RPC", "if not target.rpc:" in src, True)
    check("and raises 400", "HTTPException(" in src and "400" in src, True)
    chain_src = open(os.path.join(os.path.dirname(API_PY), "chain.py"), encoding="utf-8").read()
    check("balance_at explains destination-only chains",
          "destination only" in chain_src, True)


if __name__ == "__main__":
    t_evm_address_rule_unchanged()
    t_solana_address_rule()
    t_solana_id_fits_the_raised_bound()
    t_only_solana_gets_the_loose_bound()
    t_impossible_request_is_not_a_502()
    t_balance_endpoint_refuses_destination_only()
    print("=" * 74)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
