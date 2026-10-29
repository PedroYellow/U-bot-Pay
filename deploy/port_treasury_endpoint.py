#!/usr/bin/env python3
"""Port the /treasury endpoint to the deployments that never got it.

Audit round 3 (S-3) made the treasury address rotatable: the endpoint
`/treasury` returns it, and the homepage reads it at runtime instead of
having it baked into the static HTML.

It was added to three deployments and not to the other two:

    ubotpay      SI
    usonicpay    SI
    ustartkpay   SI
    usolpay      NO     <- this script
    utronpay     NO     <- this script

So on the Solana and Tron deployments:

  - the app has no `/treasury` route at all - it answers 404
  - the nginx vhost has no `location = /treasury`, so the request falls
    through to the SPA fallback and the API HOST returns the site's
    HTML with a 200

That second one is the nasty part: a 200 that is not the endpoint.
`check_public_endpoints.py` accepted it, because it only looked at the
status code. A JSON API answering with text/html is a broken route
that looks healthy from the outside.

## Why it does not copy the source's env lookups

The source reads:

    os.environ.get(f"UBOT_TREASURY_{prefix}_ADDRESS", "")

but each deployment names its own variables differently:

    ubotpay    UBOT_WALLET_ADDRESS
    usolpay    USOL_WALLET_ADDRESS
    usonicpay  USONIC_WALLET_ADDRESS
    utronpay   Utron_WALLET_ADDRESS
    ustartkpay USTART_WALLET_ADDRESS

and the explorer is named differently too - `EXPLORER` in ubotpay,
`UTRON_EXPLORER` in utronpay, and `EXPLORER` is not defined in the
Solana .env at all. A prefix substitution would either read nothing or
show the wrong chain's address.

So this reads the values through the deployment's OWN facade, which
already resolves them:

    chain.WALLET_ADDRESS     the treasury, from the profile
    chain.EXPLORER           the block explorer, from the profile
    chain.NETWORK_NAME       the chain's name

Those are the same values the payments and the health check use, so
the address cannot drift from the one that actually signs.

Usage: port_treasury_endpoint.py <dst-deployment>...
"""
import os
import re
import shutil
import sys

ENDPOINT = '''@app.get("/treasury")
def treasury_public(request: Request = None):
    """Public treasury address for the homepage footer link.

    Audit round 3 (S-3): the treasury address was hardcoded in the
    static HTML, so it was baked into every cached copy and could not
    be rotated without a full redeploy. The address is on-chain
    anyway, so it is not a secret - but it must be rotatable.

    The footer reads this endpoint at runtime and fills the link in.

    It was missing from this deployment, and the nginx vhost had no
    route for it either - so the request fell through to the SPA
    fallback and the API host answered 200 with the site's HTML. A 200
    that is not the endpoint. The check_public_endpoints.py check only
    looked at the status code, so it read as healthy.

    The address comes from the deployment's own engine - the same
    wallet the payments use - so it cannot drift.

    Resolving it took some care, because the engines do not agree on
    how to expose it:

      - the facade maps WALLET_ADDRESS to the engine's
        `_treasury_address`, which on Solana is a METHOD. Reading the
        attribute gives the bound method, not the address - and
        str() of a bound method is what the first version returned.
      - the Tron engine names it `treasury_address()` instead.

    So it is resolved by trying the public name first, then the
    private one, and calling it if it turns out to be callable. An
    address is a string; a bound method is not.
    """
    addr = ""
    try:
        eng = chain._eng() if hasattr(chain, "_eng") else None
    except Exception:
        eng = None

    if eng is not None:
        for name in ("treasury_address", "_treasury_address"):
            val = getattr(eng, name, None)
            if callable(val):
                try:
                    val = val()
                except Exception:
                    val = None
            if isinstance(val, str) and val:
                addr = val
                break

    if not addr:
        val = getattr(chain, "WALLET_ADDRESS", "")
        if isinstance(val, str) and val:
            addr = val

    explorer = getattr(chain, "EXPLORER", "") or ""
    if not isinstance(explorer, str):
        explorer = ""

    link = ""
    if addr and explorer:
        link = f"{explorer.rstrip('/')}/address/{addr}"

    return {
        "ok": True,
        "address": addr,
        "explorer": link,
        "visible": bool(addr),
        "chain": getattr(chain, "NETWORK_NAME", ""),
        "configured": bool(addr),
    }

'''


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    for deployment in sys.argv[1:]:
        api = os.path.join("/opt", deployment, "api.py")
        if not os.path.exists(api):
            print(f"  {deployment}: no api.py")
            continue

        with open(api, encoding="utf-8") as f:
            text = f.read()

        if '@app.get("/treasury")' in text:
            print(f"  {deployment}: already has the endpoint")
            continue

        # the facade has to be imported as `chain`. It is not always
        # `import chain` - in these deployments it is a combined
        # import: `import core, chain, brain, x402_client`.
        if not re.search(r"^import\b[^#\n]*\bchain\b", text, re.M):
            print(f"  {deployment}: it does not import chain as 'chain'")
            continue

        # insert before the first @app route
        anchor = "@app.get("
        i = text.find(anchor)
        if i < 0:
            print(f"  {deployment}: no @app route to anchor to")
            continue

        text = text[:i] + ENDPOINT + text[i:]

        try:
            compile(text, api, "exec")
        except SyntaxError as e:
            print(f"  {deployment}: it would NOT parse: {e}")
            continue

        shutil.copy2(api, api + ".bak-treasury")
        with open(api, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  {deployment}: the endpoint was added")

    return 0


if __name__ == "__main__":
    sys.exit(main())
