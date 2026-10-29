#!/usr/bin/env python3
"""Fix the Tron /balance endpoint: it answered 500 for a bad chain id.

The Tron deployment's /balance/{address} was:

    @app.get("/balance/{address}")
    def balance_by_address(address: str, chain_id: int = 0, ...):
        try:
            target = chain.get_chain(chain_id)
        except ValueError as e:
            raise HTTPException(400, str(e))

Two things wrong with it.

The default chain id was 0, and 0 is not in the Tron profile - the
profile's chain id is 728126428. So a plain request with no chain_id
looked up a chain that does not exist.

And `chain.get_chain` raises NotSupported, not ValueError, when the
chain is not in the profile. The `except ValueError` did not catch
it, so it escaped as an unhandled exception:

    chains.engine.NotSupported: chain 0 is not in this profile

and the endpoint answered:

    500 Internal Server Error

The main deployment gets this right - its default is 43114, its own
Avalanche chain id:

    def balance_by_address(address: str, chain_id: int = 43114, ...)

So the fix is the same shape: default to the deployment's OWN chain id
from its profile, and catch NotSupported as well as ValueError, so a
chain that is not in the profile is a 400 - a bad request - rather
than a server error.

The guard-watch cron checks this endpoint on every deployment, and
its own comment says it:

    It is an EVM address, so on Solana and Tron it is a 400 - wrong
    chain format - not a 200. Both are fine; a 401 would mean the
    guard rejected it, and a 500 would mean the guard is broken.

So the 500 was the guard telling the truth: the endpoint WAS broken.

Usage: fix_tron_balance_chain_id.py <api.py> [profile]
"""
import re
import shutil
import sys


def profile_chain_id(path):
    m = re.search(r"^\s*chain_id:\s*(\d+)", open(path).read(), re.M)
    return int(m.group(1)) if m else None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/api.py"
    profile = sys.argv[2] if len(sys.argv) > 2 else \
        "/opt/utronpay/profiles/utronpay.yaml"

    chain_id = profile_chain_id(profile)
    if not chain_id:
        print("  the profile's chain id could not be read")
        return 1
    print(f"  the profile's chain id: {chain_id}")

    with open(path, encoding="utf-8") as f:
        src = f.read()

    if "chain_id: int = %d" % chain_id in src:
        print("  the default is already the profile's chain id")
    else:
        old = "def balance_by_address(address: str, chain_id: int = 0,"
        new = ("def balance_by_address(address: str, chain_id: int = %d,"
               % chain_id)
        if old not in src:
            print("  the signature was not found")
            return 1
        src = src.replace(old, new, 1)
        print("  the default chain id is now the profile's")

    # NotSupported has to be caught too, or a chain that is not in the
    # profile is a 500 instead of a 400.
    old_except = """    try:
        target = chain.get_chain(chain_id)
    except ValueError as e:
        raise HTTPException(400, str(e))"""
    new_except = """    try:
        target = chain.get_chain(chain_id)
    except (ValueError, NotSupported) as e:
        # NotSupported is what the registry raises for a chain that is
        # not in this profile - and it is NOT a ValueError, so the
        # old `except ValueError` let it escape as a 500. A chain
        # that is not in the profile is a bad request, not a server
        # error.
        raise HTTPException(400, str(e))"""

    if old_except in src:
        src = src.replace(old_except, new_except, 1)
        print("  NotSupported is caught as a 400")
    elif "except (ValueError, NotSupported)" in src:
        print("  NotSupported is already caught")

    # the import
    if "from chains.engine import NotSupported" not in src and \
            "NotSupported" not in src.split("@app.get(\"/balance/")[0]:
        for anchor in ("from chains.registry import", "import chain"):
            if anchor in src:
                src = src.replace(
                    anchor,
                    "from chains.engine import NotSupported\n" + anchor, 1)
                print("  the NotSupported import was added")
                break
        else:
            print("  WARNING: could not add the NotSupported import")

    try:
        compile(src, path, "exec")
    except SyntaxError as e:
        print(f"  it would NOT parse: {e}")
        return 1

    shutil.copy2(path, path + ".bak-balance")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"  {path}: the endpoint was fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
