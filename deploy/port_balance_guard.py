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
the guard. So api.usonicpay.com answers a balance read for ANY address
with no auth - including its own treasury. It returns 0 today only
because the treasury is unfunded. The moment it is funded, the balance
is public.

This ports the guard, with the deployment's own environment prefix so
each deployment configures its own allow-list.

Line-based rather than regex-based: a regex over the whole function
body is easy to get subtly wrong, and a wrong patch that still compiles
is worse than no patch.

Usage: port_balance_guard.py <api.py> <prefix>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/api.py"
prefix = sys.argv[2] if len(sys.argv) > 2 else "USONIC"

GUARD = f'''def _anon_balance_allowlist() -> set[str]:
    """Addresses whose balances may be read without authentication.

    Audit L-3, carried over from the main U-bot Pay service:
    /balance/{{addr}} returned real balances for ANY address without
    auth. The demo address was harmless, but the treasury and the
    revenue wallet were not - the treasury is the wallet that signs
    transactions, and its balance is exactly the kind of thing that
    should not be public.

    Configurable via {prefix}_BALANCE_ANON_ALLOWLIST in the environment.
    """
    raw = cfg(
        "BALANCE_ANON_ALLOWLIST",
        # the burn address and the historical demo address
        "0x000000000000000000000000000000000000dEaD,"
        "0x713261F2151a759d9D935e6De2DB1d63A98176f6",
    )
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

# the validation line inside that function
validate = None
for i in range(start, min(start + 80, len(lines))):
    if "raise HTTPException(400, \"bad address\")" in lines[i]:
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

# verify
out = open(path, encoding="utf-8").read()
ok = ("_anon_balance_allowlist" in out
      and "auth(x_api_key)" in out
      and f"{prefix}_BALANCE_ANON_ALLOWLIST" not in out)  # the prefix is used via cfg()
print("the guard and its check are both present" if ok else "WARNING: verify by hand")
