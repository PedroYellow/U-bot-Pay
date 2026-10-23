"""
Test for bitcrm's BitrixClient._mcp shape-compatibility guard.

Reproduces the bit24.es MCP "suscripción vencida" response — a dict
with `result.content[0].text = "Acceso MCP bit24 cerrado: ..."` — and
checks that the client raises a clear RuntimeError, not a cryptic
`TypeError: string indices must be integers, not 'str'` that the
caller would surface to the operator as a Bitrix24 error.

Run with:
    /opt/bitcrm/venv/bin/python /opt/bitcrm/test_mcp_shape.py
"""
import asyncio
import sqlite3
import sys

sys.path.insert(0, "/opt/bitcrm")
from bitrix_client import BitrixClient

RESULTS = []


def chk(name, got, want):
    ok = got == want
    RESULTS.append(ok)
    print("%s  %-50s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want))


def chk_true(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-50s %s" % ("PASS" if cond else "FAIL", name, detail))


with sqlite3.connect("/opt/bitcrm/bitcrm.db") as c:
    row = c.execute("SELECT * FROM tenants LIMIT 1").fetchone()
    cols = [d[0] for d in c.execute("PRAGMA table_info(tenants)").fetchall()]
    t = dict(zip(cols, row))

URL = t[3]
TOKEN = t[4]


async def go():
    bx = BitrixClient("mcp", URL, token=TOKEN)

    # 1. Live call against the (now expired) subscription. This is the
    #    "what the user actually saw" scenario.
    try:
        await bx.create_deal("expired-sub-test",
                              {"STAGE_ID": "NEW", "CURRENCY_ID": "EUR"})
        chk("expired sub: raises (not returns string)", False, True)
    except RuntimeError as e:
        msg = str(e)
        chk_true("expired sub: RuntimeError is raised", True, type(e).__name__)
        chk_true("expired sub: message names bit24 explicitly",
                 "bit24" in msg.lower() or "suscripci" in msg.lower(), msg[:80])
        chk_true("expired sub: message is human-readable (not a stack)",
                 not msg.startswith("Traceback"), msg[:60])

    # 2. Search should also raise, not return a string the caller would
    #    later try to index.
    try:
        await bx.search_deals("test")
        chk("search: raises when sub expired (not returns [])", False, True)
    except RuntimeError:
        chk("search: raises when sub expired (not returns [])", True, True)

    # 3. Add_comment with sub expired should also raise cleanly.
    try:
        await bx.add_comment(99999, "test")
        chk("add_comment: raises when sub expired", False, True)
    except RuntimeError:
        chk("add_comment: raises when sub expired", True, True)


asyncio.run(go())

print("=" * 78)
print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
print("ALL TESTS PASSED" if all(RESULTS) else "SOME TESTS FAILED")
sys.exit(0 if all(RESULTS) else 1)