"""
The audit findings, pinned.

Each test here corresponds to something that was actually broken and
actually fixed. Without them, the same bug comes back the next time
someone edits the file it lives in.

  1. Decimal * float -- the balance of any wallet whose primary token
     was not USDC raised TypeError and answered 500.
  2. No security headers at all on the site.
  3. POST /voice/session unreachable: nginx routed it to the static
     handler, so the voice agent could not mint a session token.
  4. The database was world-readable (644).
  5. The Telegram bot token was written to the journal on every poll.
  6. /energy/prices answered with live TRON prices on a Solana
     product.
  7. A concurrent double "confirm" must settle exactly once.

The first four are checked against the live deployment, because they
are properties of the deployment and not of the code. The rest are
checked here.
"""
import os
import sys

sys.path.insert(0, os.environ.get("ENGINE_ROOT", "/opt/usolpay"))

ENV_FILE = os.environ.get("USOLPAY_ENV_FILE", "/opt/usolpay/.env")
if os.path.exists(ENV_FILE):
    for line in open(ENV_FILE):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip('"').strip("'"))

import json
import subprocess
import threading
import urllib.error
import urllib.request

RESULTS = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-58s %s" % ("PASS" if cond else "FAIL", name, str(detail)))


def get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()[:400].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()[:300].decode("utf-8", "replace")
    except Exception as e:
        return 0, {}, str(e)[:150]


def post(url, body=None, headers=None, timeout=40):
    """POST and return (status, body-as-text).

    The body is NOT truncated: a settled payment's response carries
    the whole payment row and is well over 600 characters, and a
    truncated body does not parse -- which made a successful payment
    look like a failure.
    """
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(body or {}).encode(),
                                 headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)[:150]


LOCAL = "http://127.0.0.1:%s" % os.environ.get("USOL_PORT", "8093")
PUBLIC = "https://api.usolpay.com"
SITE = "https://usolpay.com"

# ── 1. Decimal * float ──────────────────────────────────────────
try:
    import core
    import api

    # A user whose primary token is NOT the deployment's default.
    # The engine returns money as Decimal; the FX table is floats.
    # The voice webhook with the demo user: the demo wallet's primary
    # token is USDC and it holds none, so `settled` comes back as
    # Decimal("0") -- exactly the type that used to raise when
    # multiplied by the float FX rate.
    st, d = post(LOCAL + "/turn",
                 {"user_key": "demo", "text": "cual es mi balance"},
                 {"x-ubot-channel": os.environ["USOL_VOICE_SECRET"]})
    chk("the balance does not 500 on a Decimal amount", st == 200, st)
    if st == 200:
        body = json.loads(d)
        chk("the balance answers with a number",
            isinstance((body.get("data") or {}).get("settled"), (int, float)),
            repr((body.get("data") or {}).get("settled")))

    # And the token path: a valid token acts for that user's wallet.
    u = core.upsert_user(778000111, "audit-decimal", "es", None,
                         1151111081099710)
    tok = core.issue_session_jwt(u, os.environ["USOL_JWT_SECRET"], ttl_s=600)
    st, d = post(LOCAL + "/turn",
                 {"user_key": tok, "text": "cual es mi balance"},
                 {"x-ubot-channel": os.environ["USOL_VOICE_SECRET"]})
    chk("the balance works for a user reached by session token",
        st == 200, st)
except Exception as e:
    chk("the balance does not 500 on a Decimal amount", False,
        "%s: %s" % (type(e).__name__, str(e)[:70]))

# ── 2. the security headers ─────────────────────────────────────
st, headers, _ = get(SITE + "/")
for name, hdr in [("HSTS", "Strict-Transport-Security"),
                  ("nosniff", "X-Content-Type-Options"),
                  ("X-Frame-Options", "X-Frame-Options"),
                  ("Referrer-Policy", "Referrer-Policy"),
                  ("Permissions-Policy", "Permissions-Policy"),
                  ("Content-Security-Policy", "Content-Security-Policy")]:
    chk("the site sends %s" % name, hdr in headers, sorted(headers)[:4])

# ── 3. POST /voice/session is reachable ─────────────────────────
st, body = post(PUBLIC + "/voice/session", {})
chk("POST /voice/session is routed to the API, not the static handler",
    st != 405, st)
chk("POST /voice/session without the secret is refused",
    st == 401, st)

# With the secret it must mint a token.
st, body = post(PUBLIC + "/voice/session", {},
                 {"x-ubot-channel": os.environ["USOL_VOICE_SECRET"]})
chk("POST /voice/session with the voice secret mints a token",
    st == 200 and "token" in body, st)

# ── 4. the database permissions ─────────────────────────────────
try:
    r = subprocess.run(["stat", "-c", "%a", "/opt/usolpay/usolpay.db"],
                       capture_output=True, text=True, timeout=10)
    mode = r.stdout.strip()
    chk("the database is not world-readable", mode in ("600", "640"), mode)
except Exception as e:
    chk("the database is not world-readable", False, str(e)[:60])

# ── 5. the bot token does not reach the journal ─────────────────
try:
    # Only since the last restart: the journal keeps the history, and
    # the leak was in the OLD entries, written before the fix.
    since = subprocess.run(
        ["systemctl", "show", "-p", "ActiveEnterTimestamp", "--value", "usolpay"],
        capture_output=True, text=True, timeout=10).stdout.strip()
    r = subprocess.run(
        ["journalctl", "-u", "usolpay", "--since", since or "10 min ago",
         "-o", "cat"],
        capture_output=True, text=True, timeout=180)
    leaked = "bot8915304380" in r.stdout
    chk("the Telegram bot token is not written to the journal", not leaked,
        "aparece" if leaked else "")
except Exception as e:
    chk("the Telegram bot token is not written to the journal", False,
        str(e)[:60])

# ── 6. /energy/prices does not report TRON prices ───────────────
st, _, body = get(LOCAL + "/energy/prices")
try:
    j = json.loads(body)
except Exception:
    j = {}
chk("/energy/prices does not report TRON energy prices",
    "energy_trx" not in j and "bandwidth_trx" not in j, st)

# ── 7. a concurrent double confirm settles once ─────────────────
VOICE = {"x-ubot-channel": os.environ["USOL_VOICE_SECRET"]}

# Clear any payment left staged by an earlier test, so this one starts
# from a known state.
post(LOCAL + "/turn", {"user_key": "demo", "text": "no"}, VOICE, timeout=60)

st, body = post(LOCAL + "/turn",
                 {"user_key": "demo", "text": "manda 1 USDT a mom"}, VOICE,
                 timeout=90)
staged = False
if st == 200:
    try:
        staged = json.loads(body).get("action") == "confirm_needed"
    except Exception:
        staged = False

if staged:
    # The barrier matters: without it the threads arrive staggered
    # and the first one pops the pending before the others start,
    # which tests nothing.
    #
    # What is being asserted is that the PAYMENT happens once. The
    # other three threads get "none" -- there was one pending slot
    # and one of them took it. That is the correct behaviour, and it
    # is the reason there is no double spend.
    barrier = threading.Barrier(4)
    results = []

    def confirm():
        barrier.wait()
        s, d = post(LOCAL + "/turn", {"user_key": "demo", "text": "si"},
                    VOICE, timeout=90)
        try:
            results.append(json.loads(d).get("action"))
        except Exception as e:
            results.append("unparseable: %s" % str(d)[:60])

    threads = [threading.Thread(target=confirm) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The guard is against DOUBLE SETTLEMENT: four confirms racing on
    # one pending payment must not send four payments.
    #
    # `_pending.pop(uid, None)` is atomic, so exactly one thread takes
    # the pending payment and the other three find nothing - their
    # action is "none". That is the guard working.
    #
    # The outcome of the winning thread depends on the treasury: with
    # funds it settles, without them the send fails. The assertion
    # used to require "settled", so on an unfunded deployment it
    # reported the guard as broken when the guard had held.
    settled = results.count("settled")
    failed = results.count("failed")
    nothing = results.count("none")
    took_it = settled + failed

    chk("exactly one confirm took the pending payment", took_it == 1,
        "%d settled, %d failed, %d found nothing: %s"
        % (settled, failed, nothing, results))
    chk("the other three confirms did not take it",
        nothing == 3 and len(results) == 4, results)
    # Informational, not a failure: it needs a funded treasury.
    print("  note: %d settled, %d failed (a failed send means the "
          "treasury cannot pay)" % (settled, failed))
else:
    chk("four concurrent confirms settle exactly once", False,
        "el stage no llego a confirm_needed (status %s)" % st)

print("=" * 88)
print("%d/%d passed" % (sum(RESULTS), len(RESULTS)))
print("ALL AUDIT FINDING TESTS PASSED" if all(RESULTS)
      else "SOME AUDIT FINDING TESTS FAILED")
sys.exit(0 if all(RESULTS) else 1)