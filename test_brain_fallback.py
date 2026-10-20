"""
Regression tests for the 20 Oct 2026 audit.

The bug: POST /turn returned HTTP 500 on 100% of requests. UBOT_LLM_URL pointed at
llm.u-topia.com, which did not resolve. brain.parse() swallowed the first failure
with a bare `except: raw = None`, then called the Anthropic fallback, which raised
with no key configured — and that raise was not caught, so the turn 500'd.

These tests are self-contained (no /opt/ubotpay, no network, no API key) so they
can run anywhere:

    python3 test_brain_fallback.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Force the offline path: no Ubot endpoint, no Anthropic key.
for k in ("UBOT_LLM_URL", "UBOT_LLM_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)

import brain  # noqa: E402

CTX = {"contacts": [], "invoices": [], "awaiting": False}

# (input, expected intent, expected amount, expected recipient)
LOCAL_CASES = [
    ("how much do I have?",        "balance",    None,    None),
    ("what's my balance",          "balance",    None,    None),
    ("cuánto tengo?",              "balance",    None,    None),
    ("send 2000 baht to my mother", "pay",       2000.0, "my mother"),
    ("send 2k baht to VietSupply", "pay",        2000.0, "VietSupply"),
    ("transfer 300 dollars to VietSupply", "pay", 300.0, "VietSupply"),
    ("envía 2000 baht a mi madre", "pay",        2000.0, "mi madre"),
    ("show my invoices",           "invoices",   None,    None),
    ("pay the Vietnam invoice",    "pay_invoice", None,   None),
    ("my history",                 "history",    None,    None),
    ("cancel",                     "cancel",     None,    None),
    ("yes",                        "confirm",    None,    None),
    ("no",                         "cancel",     None,    None),
    ("help",                       "help",       None,    None),
    ("who are you",               "smalltalk",  None,    None),
    ("research Sequoia",           "agent_query", None,   None),
    ("",                           "unknown",    None,    None),
    ("asdkjfh random gibberish",   "unknown",    None,    None),
    ("คุณมีเงินเท่าไหร่",            "balance",    None,    None),   # Thai
    ("ส่งเงิน 2000 บาท ให้แม่",          "pay",        2000.0, None),    # Thai
]


def test_local_parser():
    fails = []
    for text, want_intent, want_amt, want_rcpt in LOCAL_CASES:
        r = brain._local_parse(text)
        got_rcpt = (r.get("recipient") or "").strip()
        ok = (
            r.get("intent") == want_intent
            and (want_amt is None or r.get("amount") == want_amt)
            and (want_rcpt is None or got_rcpt.lower() == want_rcpt.lower())
        )
        if not ok:
            fails.append((text, want_intent, want_amt, want_rcpt, r))
        print(f"{'PASS' if ok else 'FAIL'} {text!r:40} -> {r.get('intent'):12} "
              f"amt={r.get('amount')} rcpt={r.get('recipient')!r}")
    print(f"\nlocal parser: {len(LOCAL_CASES) - len(fails)}/{len(LOCAL_CASES)} passed")
    for f in fails:
        print("   FAILED:", f)
    return not fails


def test_parse_never_raises():
    """The actual regression: with no LLM reachable, parse() must degrade to the
    local parser instead of propagating an exception into the API handler."""
    async def run():
        ok = True
        for text, _, _, _ in LOCAL_CASES:
            try:
                r = await brain.parse(text, CTX)
                assert isinstance(r, dict) and "intent" in r, f"bad shape: {r}"
                print(f"PASS  parse({text!r:36}) -> {r['intent']}")
            except Exception as e:
                ok = False
                print(f"FAIL  parse({text!r:36}) raised {type(e).__name__}: {e}")
        return ok

    print()
    return asyncio.run(run())


def test_currency_detection():
    cases = [("send 2000 baht", "THB"), ("500 usdc", "USDC"), ("300 dollars", "USD"),
             ("100 euro", "EUR"), ("50000 vnd", "VND"), ("200 บาท", "THB")]
    ok = True
    for text, want in cases:
        got = brain._detect_currency(text)
        good = got == want
        ok = ok and good
        print(f"{'PASS' if good else 'FAIL'} currency({text!r:20}) -> {got} (want {want})")
    print()
    return ok


if __name__ == "__main__":
    results = [test_local_parser(), test_parse_never_raises(), test_currency_detection()]
    print("=" * 60)
    if all(results):
        print("ALL TESTS PASSED")
        sys.exit(0)
    print("SOME TESTS FAILED")
    sys.exit(1)