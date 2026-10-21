"""
Regression tests for Unicode word boundaries in the intent parser (audit 20 Oct
round 14).

\b in Python's re is an ASCII word boundary: it sits between a [A-Za-z0-9_]
character and a non-word character. Thai script characters are not word
characters, so \b can never sit correctly against them. Six patterns in
brain._local_parse() wrapped Thai alternatives in \b(...)\b, which made those
alternatives unreachable — they were listed in the source and did nothing.

Measured before the fix, on the production venv:

    "ใช่"  (yes)      -> unknown      "ได้"  (ok/go ahead) -> unknown
    "ช่วยด้วย" (help) -> unknown      "รายการ" (history) -> unknown
    "รายชื่อ" (contacts) -> unknown

Separately, the pay pattern listed env[ií]a / manda / transferir but not "pagar" —
the most obvious Spanish word for this product did not route to pay at all, while
"manda 5 usdc" did.

    python3 test_brain_unicode.py
"""
import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brain  # noqa: E402

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:52} got={got!r} want={want!r}")


async def intent_of(text):
    return (await brain.parse(text))["intent"]


# ── the Thai words that were listed in the source and never matched ──────────
THAI = [
    ("ใช่", "confirm", "yes"),
    ("ได้", "confirm", "ok / go ahead"),
    ("ส่งเลย", "confirm", "send it now"),
    ("หยุด", "cancel", "stop"),
    ("ยกเลิก", "cancel", "cancel"),
    ("รายการ", "history", "transactions"),
    ("รายชื่อ", "contacts", "contact list"),
    ("ผู้ติดต่อ", "contacts", "contacts"),
    ("ช่วยด้วย", "help", "help me"),
    ("ยอด", "balance", "balance"),
    ("ส่ง", "pay", "send"),
    ("จ่าย", "pay", "pay"),
    ("โอน", "pay", "transfer"),
]

# ── Spanish that should route to pay ────────────────────────────────────────
SPANISH_PAY = ["pagar", "paga", "pagarle", "abonar", "envía", "enviar",
                "manda", "mandar", "transferir"]


def t_thai_reachable():
    async def go():
        for text, want, label in THAI:
            check(f"thai {text!r} -> {want} ({label})", await intent_of(text), want)
    asyncio.run(go())


def t_spanish_pay_reachable():
    async def go():
        for text in SPANISH_PAY:
            check(f"spanish {text!r} -> pay", await intent_of(text), "pay")
    asyncio.run(go())


def t_confirm_includes_y():
    """"Y" is the commonest Spanish conjunction. Treating it as a confirmation is
    the intended behaviour here: the parser only runs on a reply to a quote."""
    async def go():
        for text in ["y", "Y", "sí", "ok", "confirma", "confirmo", "yes", "go ahead"]:
            check(f"{text!r} -> confirm", await intent_of(text), "confirm")
    asyncio.run(go())


def t_no_ascii_inside_word_boundary():
    """The root cause, asserted on the source so it cannot come back.

    Every pattern is inspected for a \b adjacent to a non-ASCII alternative.
    """
    src = open(os.path.join(HERE, "brain.py"), encoding="utf-8").read()
    offenders = []
    for m in re.finditer(r're\.(?:search|fullmatch|match)\(r?["\']([^"\']{10,240})["\']', src, re.S):
        pat = m.group(1)
        if "\b" in pat and re.search(r"[\u0e00-\u0e7f\u00c0-\u024f\u0370-\u03ff]", pat):
            offenders.append(pat[:60])
    check("no pattern mixes \b with non-ASCII alternatives", offenders, [])


def t_latin_still_word_bounded():
    """Dropping \b for everything would let "cancelar" match inside
    "cancelarse" or "confirmed" inside "unconfirmed". The Latin half must keep
    its boundaries."""
    async def go():
        check("'unconfirmed' is not confirm", await intent_of("unconfirmed"), "unknown")
        check("'cancelarse' is not cancel", await intent_of("cancelarse"), "unknown")
        check("'paypal' does not route to pay", await intent_of("paypal me 5"), "unknown")
    asyncio.run(go())


def t_cancel_still_works():
    async def go():
        for text in ["cancel", "cancelar", "cancela", "no", "nope", "stop", "หยุด"]:
            check(f"{text!r} -> cancel", await intent_of(text), "cancel")
    asyncio.run(go())


def t_other_intents_unaffected():
    async def go():
        for text, want in [("balance", "balance"), ("saldo", "balance"),
                           ("how much do i have", "balance"),
                           ("help", "help"), ("ayuda", "help"),
                           ("history", "history"), ("historial", "history"),
                           ("contacts", "contacts"), ("contactos", "contacts"),
                           ("invoices", "invoices"), ("factura", "invoices")]:
            check(f"{text!r} -> {want}", await intent_of(text), want)
    asyncio.run(go())


def t_money_still_parsed():
    """The amount and recipient extraction must be unaffected by the pattern fix."""
    async def go():
        r = await brain.parse("send 5 usdc to mom")
        check("amount still parsed", r["amount"], 5.0)
        check("recipient still parsed", (r["recipient"] or "").lower(), "mom")
    asyncio.run(go())


if __name__ == "__main__":
    t_thai_reachable()
    t_spanish_pay_reachable()
    t_confirm_includes_y()
    t_no_ascii_inside_word_boundary()
    t_latin_still_word_bounded()
    t_cancel_still_works()
    t_other_intents_unaffected()
    t_money_still_parsed()
    print("=" * 74)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
    sys.exit(0 if all(RESULTS) else 1)
