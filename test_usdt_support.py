"""
Regression tests for USDT support (audit 20 Oct, multichain round).

The product moves one stablecoin on both sides of a bridge. It used to be USDC
only, and adding a second one touches three places that can each quietly return the
wrong token:

1. The cache key. _cache_key() was built from chains, amount and addresses only.
   Adding USDT without adding the symbol to the key means a cached USDC route is
   served for a USDT request, and the user signs a swap into USDC while the UI said
   USDT. This is the failure the tests below exist to prevent.
2. get_best_quote() picks a winner by output amount across partners. Across only
   routes USDC; if it quoted anyway during a USDT request, its route would often
   win on price and be selected — a USDC swap returned for a USDT request.
3. An unknown symbol must not fall back to USDC. It has to fail loudly.

    python3 test_usdt_support.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []
HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_PY = os.path.join(HERE, "bridge.py")
API_PY = os.path.join(HERE, "api.py")

bridge_src = open(BRIDGE_PY, encoding="utf-8").read()
api_src = open(API_PY, encoding="utf-8").read()

# The addresses LI.FI returned as the canonical USDT for each chain, verified to
# quote in both directions during implementation.
USDT_CANONICAL = {
    1:     "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    137:   "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
    146:   "0x6047828dc181963ba44974801FF68e538dA5eaF9",
    8453:  "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
    43114: "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
    59144: "0xA219439258ca9da29E9Cc4cE5596924745e12B93",
}


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:58} got={got!r} want={want!r}")


def parse_map(name):
    blk = bridge_src[bridge_src.index(name):]
    blk = blk[:blk.index("\n}")]
    return {int(k): v for k, v in
            re.findall(r'(\d+):\s*"(0x[0-9a-fA-F]{40})"', blk)}


def t_usdt_map_has_canonical_addresses():
    got = parse_map("USDT_BY_CHAIN")
    for cid, addr in USDT_CANONICAL.items():
        check(f"USDT on chain {cid} matches LI.FI", got.get(cid), addr)


def t_usdt_excludes_chains_without_a_working_route():
    """USDT exists in the 1209-token list on Tron, but the only route loses 42%
    even USDT-to-USDT. It must not be quotable."""
    got = parse_map("USDT_BY_CHAIN")
    check("Tron (728126428) is not quotable in USDT", 728126428 in got, False)
    check("Solana has no USDT route", 1151111081099710 in got, False)


def t_token_registry_is_single_sourced():
    check("both tokens registered in one table",
          'TOKENS_BY_SYMBOL = {"USDC": USDC_BY_CHAIN, "USDT": USDT_BY_CHAIN}' in bridge_src, True)
    # quote() must read through the table, not reach for USDC_BY_CHAIN directly —
    # that indirection is what makes adding a third token a one-line change.
    fn = bridge_src[bridge_src.index("def quote("):]
    fn = fn[:fn.index("\ndef ")]
    direct = [ln.strip() for ln in fn.split("\n") if "USDC_BY_CHAIN[" in ln]
    check("quote() never indexes USDC_BY_CHAIN directly", direct, [])


def t_unknown_token_fails_loudly():
    check("helper exists", "def _token_map(" in bridge_src, True)
    check("rejects unknown symbols", "unsupported token:" in bridge_src, True)
    check("no silent USDC fallback",
          re.search(r'if key not in TOKENS_BY_SYMBOL:\s*\n\s*raise', bridge_src) is not None, True)
    check("symbol match is case-insensitive", '.strip().upper()' in bridge_src, True)


def t_cache_key_includes_the_token():
    """The one that would have moved money into the wrong token."""
    fn = bridge_src[bridge_src.index("def quote("):]
    fn = fn[:fn.index("\ndef ")]
    lines = [ln.strip() for ln in fn.split("\n") if "_cache_key(" in ln and "def " not in ln]
    check("cache key includes the token symbol", any('+ ":" + token' in ln for ln in lines), True)
    # And the key must actually be the one used for both set and get.
    check("token is part of the key variable", 'cache_key = _cache_key(' in fn and '+ ":" + token' in fn, True)


def t_across_does_not_quote_usdt():
    """get_best_quote() merges partners by output amount, so a USDC route offered
    for a USDT request could win and be handed to the user to sign."""
    i = bridge_src.index("def quote_across")
    seg = bridge_src[i:bridge_src.index("\ndef ", i + 10)]
    check("quote_across takes a token argument", "token: str = \"USDC\"" in seg, True)
    check("and returns None for non-USDC",
          re.search(r'upper\(\) != "USDC":\s*\n\s*return None', seg) is not None, True)


def t_partners_receive_the_token():
    i = bridge_src.index("def get_best_quote")
    seg = bridge_src[i:bridge_src.index("\ndef ", i + 10)]
    check("get_best_quote takes a token", 'token: str = "USDC"' in seg, True)
    # Assert the token is the argument actually passed, not merely mentioned
    # somewhere in the block. The first version of this test passed while Across
    # was still being called without it, and the result was a USDC route winning a
    # USDT request — the API then labelled a USDC swap as USDT.
    # DOTALL: the calls span several lines, so [^)]* without it stops at the
    # first newline and matches nothing useful.
    # group(1) = partner key, group(3) = the arguments (group(2) is the function
    # name, which is why an earlier version of this check kept failing).
    subs = {m.group(1): " ".join(m.group(3).split())
            for m in re.finditer(r'"(\w+)":\s*ex\.submit\(\s*(\w+),(.*?)\)', seg, re.S)}
    check("both partners are submitted", sorted(subs), ["across", "lifi"])
    check("lifi receives token as an argument", subs.get("lifi", "").endswith("True, token"), True)
    check("across receives token as an argument",
          subs.get("across", "").endswith("to_address, token"), True)


def t_get_best_quote_validates_against_the_token_map():
    """USDT covers fewer chains than USDC. Validating only that the chain ids are
    known — as get_best_quote used to, via USDC_BY_CHAIN — lets USDT -> Solana
    through the check and come back as a 502 from the fan-out instead of a 400."""
    i = bridge_src.index("def get_best_quote")
    seg = bridge_src[i:bridge_src.index("\ndef ", i + 10)]
    check("resolves the map for the requested token", "_tmap = _token_map(token)" in seg, True)
    check("and validates against it", "if cid not in _tmap:" in seg, True)
    check("no longer validates only against USDC",
          "if cid not in USDC_BY_CHAIN:" not in seg, True)
    check("unsupported partner failures still surface as 400",
          "if errors and len(errors) == len(futures):" in seg, True)


def t_api_exposes_and_forwards_it():
    check("endpoint accepts token", 'token: str = Query("USDC"' in api_src, True)
    check("only USDC/USDT accepted", r"^(?i:USDC|USDT)$" in api_src, True)
    fn = api_src[api_src.index("def bridge_quote"):]
    fn = fn[:fn.index("\n@app.")]
    for partner in ("quote", "quote_across", "get_best_quote"):
        check(f"{partner} receives token in api.py", f"_bridge.{partner}(" in fn, True)
    check("response states the token", '"token": q.get("token"' in fn, True)


def t_usdt_default_keeps_usdc_callers_working():
    """Every pre-existing caller passes no token; it must default to USDC."""
    check("quote defaults to USDC", 'token: str = "USDC"' in bridge_src, True)
    check("get_best_quote defaults to USDC", 'token: str = "USDC"' in bridge_src, True)
    check("api defaults to USDC", 'token: str = Query("USDC"' in api_src, True)


if __name__ == "__main__":
    t_usdt_map_has_canonical_addresses()
    t_usdt_excludes_chains_without_a_working_route()
    t_token_registry_is_single_sourced()
    t_unknown_token_fails_loudly()
    t_cache_key_includes_the_token()
    t_across_does_not_quote_usdt()
    t_partners_receive_the_token()
    t_get_best_quote_validates_against_the_token_map()
    t_api_exposes_and_forwards_it()
    t_usdt_default_keeps_usdc_callers_working()
    print("=" * 84)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)