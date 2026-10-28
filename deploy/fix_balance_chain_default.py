#!/usr/bin/env python3
"""Make the balance route's chain_id default the deployment's own.

The route was:

    @app.get("/balance/{address}")
    def balance_by_address(address: str, chain_id: int = 0, ...):
        target = chain.get_chain(chain_id)

The default was the literal 0, which happened to be the Tron
deployment's chain id - the deployment reported chain_id 0 because
the profile had no key and the engine hardcoded it. So the default and
the registry's key agreed, by accident, on a wrong value.

Giving the deployment its real chain id (728126428) broke it: the
default 0 no longer matched the key, engine_for raised NotSupported,
and the endpoint answered 500 - a crash where it had answered 400 for
a wrong-chain address.

The default has to come from the deployment, not from a literal. Then
it is right whatever the chain is, and the route and the registry
cannot disagree.

The same literal appears in the other deployments; each has the same
problem, and the same fix.

Usage: fix_balance_chain_default.py <api.py>
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/api.py"

src = open(path, encoding="utf-8").read()
n = 0

# ── 1. the route's default ────────────────────────────────────────
OLD = '''@app.get("/balance/{address}")
def balance_by_address(address: str, chain_id: int = 0, x_api_key: str | None = Header(default=None)):'''

NEW = '''@app.get("/balance/{address}")
def balance_by_address(address: str, chain_id: int | None = None,
                       x_api_key: str | None = Header(default=None)):
    # The chain id defaults to THIS deployment's, not to a literal.
    # It was 0, which happened to be the Tron deployment's chain id
    # because the profile had no key and the engine hardcoded it - so
    # the default and the registry's key agreed by accident, on a
    # wrong value. Give the deployment its real chain id and the
    # literal no longer matches, and the endpoint answers 500 where it
    # had answered 400.
    if chain_id is None:
        chain_id = chain.CHAIN_ID'''

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    n += 1
else:
    print("  the route signature was not found")

open(path, "w", encoding="utf-8").write(src)

# ── 2. any other literal chain-id defaults ────────────────────────
remaining = re.findall(r"chain_id\s*:\s*int\s*=\s*0", src)
if remaining:
    print(f"  {len(remaining)} other literal chain_id default(s) remain")

try:
    compile(src, path, "exec")
    print(f"{path}: {n} change(s), it parses")
except SyntaxError as e:
    print(f"{path}: {n} change(s) but it does NOT parse: {e}")
