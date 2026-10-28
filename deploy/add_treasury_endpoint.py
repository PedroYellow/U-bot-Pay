#!/usr/bin/env python3
"""Add the public /treasury endpoint to a whitelabel codebase.

The static pages fetch /treasury at runtime to fill in the footer
link and the treasury KPI tile, so the address is not baked into the
HTML and can be rotated without a redeploy.

The main U-bot Pay service grew this endpoint in audit round 3 (S-3),
but the whitelabel codebases were forked before that, so they never got
it. The page asked for it and the API answered 404, which meant the
footer link stayed hidden and the KPI tile had nothing to show.

The address comes from the engine, which knows it from the profile and
the key file - so the right chain's address is reported without any
hardcoding.

Usage: add_treasury_endpoint.py <api.py>
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usonicpay/api.py"

ENDPOINT = '''

@app.get("/treasury")
def treasury_public():
    """Public treasury address for the homepage footer link.

    The address is on-chain, so it is not a secret - but it must be
    opt-in and rotatable, which is why the static page reads it at
    runtime instead of having it baked in. A cached copy of the HTML
    must not be able to pin an old address.

    The address and the explorer come from the engine, so a deployment
    on a different chain reports its own, not a hardcoded one.
    """
    addr = chain.WALLET_ADDRESS or ""
    explorer = chain.EXPLORER or ""
    show = cfg("SHOW_TREASURY_ON_HOMEPAGE", "true").lower() in ("1", "true", "yes")

    link = ""
    if addr and explorer:
        link = f"{explorer.rstrip('/')}/address/{addr}"

    return {
        "ok": True,
        "address": addr,
        "explorer": link,
        "visible": show and bool(addr),
        "chain": chain.NETWORK_NAME,
        "configured": bool(addr),
    }

'''

src = open(path, encoding="utf-8").read()

if '@app.get("/treasury")' in src:
    print("the endpoint already exists")
    sys.exit(0)

# put it right before the dashboard endpoint, so it sits with the other
# public read-only endpoints
anchor = '@app.get("/dashboard")'
if anchor not in src:
    print("could not find the dashboard endpoint to anchor against")
    sys.exit(1)

i = src.index(anchor)
out = src[:i] + ENDPOINT.lstrip("\n") + "\n" + src[i:]
open(path, "w", encoding="utf-8").write(out)
print("the /treasury endpoint was added")

# does it have everything it uses?
for need in ("chain.WALLET_ADDRESS", "chain.EXPLORER", "chain.NETWORK_NAME", "cfg("):
    if need in ENDPOINT and need.replace("(", "").replace(")", "") not in src:
        print(f"  ! {need} may not be available in this file")
