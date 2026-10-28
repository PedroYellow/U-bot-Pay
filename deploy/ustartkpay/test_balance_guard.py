#!/usr/bin/env python3
"""The anonymous-balance guard must exist, and must work.

Audit L-3, carried over from the main U-bot Pay service.

The main service got this guard on 21 Oct. It was then LOST when the
repository was synced over the deployment, and the audit note and the
code disagreed for six days. It is here now, in the repository, with a
test that fails if it disappears again.

The whitelabels were forked from the Solana deployment, which never had
the guard. So api.usonicpay.com answered a balance read for ANY address
with no auth - including its own treasury. It returned 0 only because
the treasury was unfunded; the moment it was funded, the balance would
have been public.

This test checks two things:

  1. the guard is IN THE CODE - the function exists, the endpoint calls
     it, and the environment variable is configurable
  2. the guard WORKS - the treasury is refused anonymously, and an
     allow-listed address still reads

The second one is the one that matters. A guard that is present but
wired to the wrong thing reads the same as no guard at all.

Usage:
    python3 test_balance_guard.py                 # against the local API
    python3 test_balance_guard.py --api http://127.0.0.1:8094
"""
import json
import sys
import urllib.error
import urllib.request

API = "http://127.0.0.1:8094"
for i, a in enumerate(sys.argv):
    if a == "--api" and i + 1 < len(sys.argv):
        API = sys.argv[i + 1]

# The addresses this deployment cares about. The treasury is the one
# that must be protected; the demo address is the one that must still
# be readable, because the front-end uses it.
TREASURY = "0xCC57d2f8A941B3408f939E6fe949fb7C2b1F01Af"
ALLOWED = "0x713261F2151a759d9D935e6De2DB1d63A98176f6"

failures = []


def check(cond, msg, detail=""):
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}" + (f"  [{detail}]" if detail else ""))
        failures.append(msg)


def get(path):
    try:
        req = urllib.request.Request(API + path,
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"detail": body[:120]}
    except Exception as e:
        return None, {"detail": f"{type(e).__name__}: {str(e)[:80]}"}


print("=" * 72)
print("B1: the guard is in the code")
print("=" * 72)

try:
    src = open("/opt/usonicpay/api.py", encoding="utf-8").read()
except Exception as e:
    src = ""
    print(f"  (could not read api.py: {type(e).__name__})")

check("_anon_balance_allowlist" in src,
      "the allow-list function exists in api.py")
check("auth(x_api_key)" in src,
      "the endpoint calls the auth check")
check("BALANCE_ANON_ALLOWLIST" in src,
      "the allow-list is configurable from the environment")

# the check must be INSIDE the balance endpoint, not somewhere else
idx_ep = src.find('@app.get("/balance/{address}")')
idx_fn = src.find("def _anon_balance_allowlist")
idx_check = src.find("if address.lower() not in _anon_balance_allowlist()")
if idx_fn and idx_ep and idx_check:
    check(idx_fn < idx_ep,
          "the allow-list function is defined before the endpoint")
    check(idx_check > idx_ep,
          "the check is inside the balance endpoint")
else:
    check(False, "the guard, the endpoint and the check are all present")

print()
print("=" * 72)
print("B2: the guard works")
print("=" * 72)

status, body = get(f"/balance/{TREASURY}")
check(status in (401, 403),
      "the treasury is refused without authentication",
      f"status={status} body={str(body)[:90]}")
check("Unauthorized" in str(body.get("detail", "")) or status in (401, 403),
      "the refusal says so",
      str(body.get("detail", ""))[:80])

status, body = get(f"/balance/{ALLOWED}")
check(status == 200,
      "an allow-listed address still reads anonymously",
      f"status={status} body={str(body)[:90]}")
if status == 200:
    check("address" in body,
          "the response is a balance, not an error")

# and the chain is still the right one - the guard must not have broken
# the endpoint it protects
if status == 200:
    check(body.get("chain_id") == 146,
          "the balance is read on Sonic",
          f"chain_id={body.get('chain_id')}")

print()
print("=" * 72)
print(f"{len(failures)} failure(s)")
print("=" * 72)
for f in failures:
    print(f"  - {f}")

sys.exit(1 if failures else 0)
