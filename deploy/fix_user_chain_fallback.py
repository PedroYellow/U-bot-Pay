#!/usr/bin/env python3
"""Use the deployment's chain id for the "user has no chain" fallback.

    "chain": user.get("chain_id") or 1151111081099710,

Two places - the session JWT payload and the auth response - fall back
to a hardcoded chain id when the user row has none. That fallback was
Solana's, in every deployment.

The JWT is signed, so the chain is inside the token: a session minted
on the wrong chain is a session bound to the wrong chain. The fallback
is the deployment's own chain id.

Usage: fix_user_chain_fallback.py <core.py> <api.py>
"""
import re
import sys

paths = sys.argv[1:] or ["/opt/usonicpay/core.py", "/opt/usonicpay/api.py"]

for path in paths:
    src = open(path, encoding="utf-8").read()
    out, n = re.subn(
        r'(user\.get\("chain_id"\)\s+or\s+)1151111081099710',
        r"\1chain.CHAIN_ID",
        src)
    if n:
        open(path, "w", encoding="utf-8").write(out)
        print(f"{path}: {n} fallback(s) now use the deployment's chain id")
    else:
        print(f"{path}: nothing to change")
