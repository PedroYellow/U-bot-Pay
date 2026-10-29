#!/usr/bin/env python3
"""Watch the deployments' balance guards and their chain identity.

The guard was lost once already - on the main service, when the
repository was synced over the deployment - and the audit note and the
code disagreed for six days. The whitelabels were forked from a
deployment that never had it, so two of them shipped without it.

Nothing was watching for it to happen again. This does.

It checks, every run:

  1. each deployment's service is up
  2. the anonymous-balance allowlist is present in each deployment's
     code
  3. the treasury is NOT in the allowlist - that is the whole point
  4. at runtime, the treasury answers 401 and the demo address still
     reads
  5. each deployment's live chain id matches the chain it claims to
     be - a deployment that drifted onto another chain would answer
     200 while paying on the wrong network

The HTTP checks run ON THE VPS, because that is where the deployments
live and their ports are bound to 127.0.0.1 there. The probe lives in
a FILE on the VPS: passing a multi-line script as an argument through
ssh loses the quoting, and the probe silently failed - which looked
like a broken deployment.

The output is one line per deployment and it is DETERMINISTIC - no
timestamps, no durations - so it can be used as a change detector.
Only a change wakes the agent.

Usage: guard_watch.py
"""
import json
import subprocess
import sys

HOST = "hvps"

# name: (port, the treasury address on that chain, the expected chain id)
DEPLOYMENTS = {
    "ubotpay":   (8090, "0x673678B1870Ae7ef598A5982D25ffdee68d85669", 43114),
    "usonicpay": (8094, "0xCC57d2f8A941B3408f939E6fe949fb7C2b1F01Af", 146),
    "usolpay":   (8093, "BX6sfCwA9CkVqfpppco3HgXi3gCMhxQgBFoH4yLpJk55", 1151111081099710),
    "utronpay":  (8092, None, 728126428),
    # Starknet. The chain id is a STRING - "SN_MAIN" - because Starknet
    # has no numeric CAIP-2 chain id. The treasury is empty until the
    # mainnet account exists, so there is no treasury to protect yet.
    "ustartkpay": (8095, None, "SN_MAIN"),
}

# The address the front-end uses for the demo, which must stay readable.
# It is an EVM address, so on Solana and Tron it is a 400 - wrong chain
# format - not a 200. Both are fine; a 401 would mean the guard
# rejected it, and a 500 would mean the guard is broken.
ALLOWED = "0x713261F2151a759d9D935e6De2DB1d63A98176f6"

PROBE = '''import json, sys, urllib.request, urllib.error

port = int(sys.argv[1])
allowed = sys.argv[2]
treasury = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None


def get(path):
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path),
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:
        return None, type(e).__name__


out = {}
if treasury:
    st, _ = get("/balance/" + treasury)
    out["treasury"] = st
st, _ = get("/balance/" + allowed)
out["demo"] = st
st, body = get("/dashboard")
out["dashboard"] = st
if st == 200:
    try:
        out["chain_id"] = json.loads(body).get("chain_id")
    except Exception:
        out["chain_id"] = None
else:
    out["chain_id"] = None
print(json.dumps(out))
'''


def ssh(cmd, timeout=60):
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, cmd],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"__ERR__ {type(e).__name__}"


def ship_probe():
    """Put the probe on the VPS as a FILE."""
    local = "/tmp/_guard_probe.py"
    with open(local, "w", encoding="utf-8") as fh:
        fh.write(PROBE)
    try:
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", local,
                        f"{HOST}:/tmp/_guard_probe.py"],
                       capture_output=True, text=True, timeout=60)
        return True
    except Exception:
        return False


def probe(port, treasury):
    raw = ssh(
        f"/opt/ubotpay/venv/bin/python /tmp/_guard_probe.py "
        f"{port} {ALLOWED} {treasury or ''} 2>/dev/null", timeout=90)
    try:
        return json.loads(raw.strip().split("\n")[-1])
    except Exception:
        return None


def allowlist_of(name):
    """The addresses the allowlist actually contains.

    Read the way the code does: the string literal the function
    returns. Grepping the file for an address matched the DOCSTRING -
    which mentions the treasury to explain why it is NOT there - and
    reported a treasury that is not in the list as one that is.
    """
    out = ssh(
        "sed -n '/def _anon_balance_allowlist/,/^def /p' "
        f"/opt/{name}/api.py | grep -oE '\"0x[0-9a-fA-F]{{40}}\"' "
        "| tr -d '\"' | tr 'A-F' 'a-f' || true")
    return {a.strip().lower() for a in out.split("\n") if a.strip()}


def main():
    if not ship_probe():
        print("guard-watch")
        print("  PROBLEM: the probe could not be copied to the VPS")
        return 1

    problems = []
    lines = []

    for name, (port, treasury, chain_id) in DEPLOYMENTS.items():
        issues = []

        # ── the service is up ──────────────────────────────────────
        active = ssh(f"systemctl is-active {name} 2>/dev/null || echo inactive")
        if active != "active":
            issues.append(f"service={active}")

        # ── the guard is in the code ───────────────────────────────
        n = ssh(f"grep -c '_anon_balance_allowlist' /opt/{name}/api.py "
                f"2>/dev/null || echo 0")
        try:
            n = int(n)
        except ValueError:
            n = 0
        if n == 0:
            issues.append("no-allowlist-in-code")

        # ── the treasury is NOT in the allowlist ──────────────────
        if treasury:
            allow = allowlist_of(name)
            if not allow:
                issues.append("allowlist-unreadable")
            elif treasury.lower() in allow:
                issues.append("treasury-in-allowlist")

        # ── the runtime behaviour, on the VPS ─────────────────────
        p = probe(port, treasury)
        if p is None:
            issues.append("probe-failed")
        else:
            st = p.get("treasury")
            if treasury and st != 401:
                issues.append(f"treasury={st}(want 401)")

            st = p.get("demo")
            if st not in (200, 400):
                issues.append(f"demo={st}(want 200 or 400)")

            st = p.get("dashboard")
            if st != 200:
                issues.append(f"dashboard={st}")

            got = p.get("chain_id")
            if got is not None:
                # The chain id can be a NUMBER or a STRING - Starknet's
                # is "SN_MAIN", because it has no numeric CAIP-2 chain
                # id. So they are compared as strings.
                if str(got) != str(chain_id):
                    issues.append(f"chain_id={got}(want {chain_id})")

        if issues:
            problems.append(f"{name}: {', '.join(issues)}")
        lines.append(f"{name}: " + ("OK" if not issues else "; ".join(issues)))

    print("guard-watch")
    for l in lines:
        print("  " + l)

    if problems:
        print(f"PROBLEMS: {len(problems)}")
        for p in problems:
            print("  " + p)
        return 1

    print("all deployments: the balance guard is present, enforced, and on "
          "the right chain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
