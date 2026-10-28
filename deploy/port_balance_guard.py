#!/usr/bin/env python3
"""Port the anonymous-balance guard to a whitelabel deployment.

Audit L-3, carried over from the main U-bot Pay service:

    /balance/{addr} returned real balances for ANY address without
    authentication. The demo address was harmless, but the treasury and
    the revenue wallet were not - the treasury is the wallet that signs
    transactions, and its balance is exactly the kind of thing that
    should not be public.

The main service got the guard on 21 Oct. It was then LOST when the
repository was synced over the deployment, and the audit note and the
code disagreed for six days.

The whitelabels were forked from the Solana deployment, which never had
the guard. api.usonicpay.com and api.usolpay.com both answered a
balance read for ANY address with no auth - including their own
treasury.

Two things this has to get right:

  1. The env var name. The Sonic deployment uses config.py's
     cfg("BALANCE_ANON_ALLOWLIST"), which resolves under its own
     prefix. The Solana deployment never got that refactor and reads
     os.environ.get("USOL_...") directly. A guard that calls cfg() in a
     codebase without config.py raises NameError at request time - a
     500 with nothing in the journal, which is the hardest kind of
     failure to diagnose.

  2. The check has to go INSIDE the endpoint, after the address is
     validated.

Line-based rather than regex-based: a regex over the whole function
body is easy to get subtly wrong, and a wrong patch that still compiles
reads the same as no patch at all.

Usage: port_balance_guard.py <api.py> <prefix>
  api.py:   the service to patch
  prefix:   the deployment prefix, e.g. USONIC
"""
import os
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/api.py"
prefix = sys.argv[2] if len(sys.argv) > 2 else "USONIC"

# Does this codebase have the config.py prefix layer?
has_cfg = False
for cand in (os.path.join(os.path.dirname(path), "config.py"),):
    if os.path.exists(cand):
        with open(cand, encoding="utf-8") as fh:
            has_cfg = "def cfg(" in fh.read()
        break

if has_cfg:
    reader = f'''    raw = cfg(
        "BALANCE_ANON_ALLOWLIST",
        # the burn address and the historical demo address
        "0x000000000000000000000000000000000000dEaD,"
        "0x713261F2151a759d9D935e6De2DB1d63A98176f6",
    )'''
    env_note = f"{prefix}_BALANCE_ANON_ALLOWLIST"
else:
    reader = f'''    raw = os.environ.get(
        "{prefix}_BALANCE_ANON_ALLOWLIST",
        # the burn address and the historical demo address
        "0x000000000000000000000000000000000000dEaD,"
        "0x713261F2151a759d9D935e6De2DB1d63A98176f6",
    )'''
    env_note = f"{prefix}_BALANCE_ANON_ALLOWLIST"

GUARD = f'''def _anon_balance_allowlist() -> set[str]:
    """Addresses whose balances may be read without authentication.

    Audit L-3, carried over from the main U-bot Pay service:
    /balance/{{addr}} returned real balances for ANY address without
    auth. The demo address was harmless, but the treasury and the
    revenue wallet were not - the treasury is the wallet that signs
    transactions, and its balance is exactly the kind of thing that
    should not be public.

    Configurable via {env_note} in the environment.
    """
{reader}
    return {{a.strip().lower() for a in raw.split(",") if a.strip()}}


'''

CHECK = """    if address.lower() not in _anon_balance_allowlist():
        auth(x_api_key)
"""

lines = open(path, encoding="utf-8").read().split("\n")

if any("_anon_balance_allowlist" in l for l in lines):
    print("the guard is already there")
    sys.exit(0)

# 1. find the balance endpoint and its address validation
start = None
for i, l in enumerate(lines):
    if '@app.get("/balance/{address}")' in l:
        start = i
        break
if start is None:
    print("could not find the balance endpoint")
    sys.exit(1)

# The validation message differs per chain - "bad address" on EVM,
# "bad TRON address" on Tron. Look for the raise, not the wording.
validate = None
for i in range(start, min(start + 120, len(lines))):
    if "HTTPException(400," in lines[i] and "address" in lines[i]:
        validate = i
        break
if validate is None:
    print("could not find the address validation")
    sys.exit(1)

# 2. insert the check after the validation
lines[validate + 1:validate + 1] = CHECK.rstrip("\n").split("\n")

# 3. insert the guard function before the endpoint
lines[start:start] = GUARD.rstrip("\n").split("\n") + [""]

open(path, "w", encoding="utf-8").write("\n".join(lines))
print(f"the anonymous-balance guard was ported for {prefix}")
print(f"  config.py present: {has_cfg}")
print(f"  env var: {env_note}")

# verify: the guard, the check, and that the reader exists
out = open(path, encoding="utf-8").read()
problems = []
if "_anon_balance_allowlist" not in out:
    problems.append("the guard function is missing")
if "auth(x_api_key)" not in out:
    problems.append("the auth check is missing")
if has_cfg and "cfg(" not in out.split("def _anon_balance_allowlist")[1][:900]:
    problems.append("the guard does not use cfg() but the codebase has config.py")
if not has_cfg and "os.environ.get" not in out.split("def _anon_balance_allowlist")[1][:900]:
    problems.append("the guard does not read os.environ but the codebase has no config.py")

if problems:
    for p in problems:
        print(f"  WARNING: {p}")
    sys.exit(1)
print("  the guard and its check are both present, and the reader matches this codebase")
