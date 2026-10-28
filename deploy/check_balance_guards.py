#!/usr/bin/env python3
"""Check the anonymous-balance guard on every deployment.

The guard was lost once already - on the main service, when the
repository was synced over the deployment - and the audit note and the
code disagreed for six days. The whitelabels were forked from a
deployment that never had it, so two of them shipped without it.

So: check all of them, from the code and from the running service.

Both halves matter. A guard that is present but wired to the wrong
thing reads the same as no guard at all, and a guard that raises at
request time reads as a 500 with nothing in the journal.

Usage: check_balance_guards.py
"""
import subprocess
import sys
import urllib.error
import urllib.request

# name: (port, the treasury address on that chain)
DEPLOYMENTS = {
    "ubotpay":   (8090, "0x673678B1870Ae7ef598A5982D25ffdee68d85669"),
    "usonicpay": (8094, "0xCC57d2f8A941B3408f939E6fe949fb7C2b1F01Af"),
    "usolpay":   (8093, "BX6sfCwA9CkVqfpppco3HgXi3gCMhxQgBFoH4yLpJk55"),
    "utronpay":  (8092, None),   # no treasury configured yet
}

# The address the front-end uses for the demo, which must stay
# readable. It is an EVM address, so on Solana and Tron it is a 400 -
# wrong chain format - not a 200. Both are fine; a 401 would mean the
# guard rejected it, and a 500 would mean the guard is broken.
ALLOWED = "0x713261F2151a759d9D935e6De2DB1d63A98176f6"


def get(url):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def guard_refs(path):
    r = subprocess.run(["grep", "-c", "_anon_balance_allowlist", path],
                       capture_output=True, text=True, timeout=20)
    out = r.stdout.strip()
    return int(out) if out.isdigit() else 0


failures = []

print("=" * 72)
print("the guard in the CODE")
print("=" * 72)
for name in DEPLOYMENTS:
    n = guard_refs(f"/opt/{name}/api.py")
    ok = n > 0
    print(f"  {'PASS' if ok else 'FAIL'}  {name:12s} {n} reference(s)")
    if not ok:
        failures.append(f"{name}: the guard is not in the code")

print()
print("=" * 72)
print("the guard at RUNTIME")
print("=" * 72)
for name, (port, treasury) in DEPLOYMENTS.items():
    if treasury:
        st = get(f"http://127.0.0.1:{port}/balance/{treasury}")
        protected = st in (401, 403)
        print(f"  {'PASS' if protected else 'FAIL'}  {name:12s} treasury -> {st} "
              f"{'protected' if protected else 'LEAKED'}")
        if not protected:
            failures.append(f"{name}: the treasury balance is readable anonymously")
    else:
        print(f"  SKIP  {name:12s} no treasury configured yet")

    st2 = get(f"http://127.0.0.1:{port}/balance/{ALLOWED}")
    ok2 = st2 in (200, 400)
    note = "reads" if st2 == 200 else ("wrong chain format" if st2 == 400 else "?")
    print(f"  {'PASS' if ok2 else 'FAIL'}  {name:12s} demo addr -> {st2} ({note})")
    if not ok2:
        failures.append(f"{name}: the allow-listed address no longer reads ({st2})")

print()
print("=" * 72)
print(f"{len(failures)} failure(s)")
print("=" * 72)
for f in failures:
    print(f"  - {f}")

sys.exit(1 if failures else 0)
