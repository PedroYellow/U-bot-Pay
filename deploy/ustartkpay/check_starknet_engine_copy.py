#!/usr/bin/env python3
"""Check that the Starknet engine the service IMPORTS is the one that is fixed.

The bug

There were two copies of the Starknet engine in the deployment:

    /opt/ustartkpay/chains/starknet.py             <- what the registry imports
    /opt/ustartkpay/chains_starknet_engine.py      <- what I edited

The registry does:

    from .starknet import StarknetEngine

so the service ran the FIRST one. The second - the one with the key
validation, the `await self.client` fix and the network parameter -
was never loaded by anything.

So three real fixes were deployed and none of them was live. The
symptoms were all "the fix is not working":

    network: ?                    <- the attribute did not exist
    has_signer(): True            <- with the WRONG key
    send -> empty error           <- the await bug

Why it happened

The integration script writes the engine to `chains/starknet.py`
because that is the name the registry imports. But the file I edited
by hand was `chains_starknet_engine.py`, which is the name in the
REPOSITORY. So the deployment had the fixed engine at the repository's
path and the old one at the path that matters.

The check

Compare the two files in the deployment. They must be identical - and
the imported one must carry the fixes.

There is also a check that the registry imports the file that exists.

Usage: check_starknet_engine_copy.py [deployment-dir]
"""
import hashlib
import os
import sys

DEPLOY = sys.argv[1] if len(sys.argv) > 1 else "/opt/ustartkpay"

IMPORTED = os.path.join(DEPLOY, "chains", "starknet.py")
OTHER = os.path.join(DEPLOY, "chains_starknet_engine.py")
REGISTRY = os.path.join(DEPLOY, "chains", "registry.py")

RESULTS = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-58s %s" % ("PASS" if cond else "FAIL", name, detail))


def md5(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()
    except OSError:
        return None


def main():
    print("=== the files ===")
    for p in (IMPORTED, OTHER):
        print(f"  {p}: {md5(p) or 'MISSING'}")

    print()
    print("=== they must be identical ===")
    a, b = md5(IMPORTED), md5(OTHER)
    if b is None:
        # only one copy - that is fine, as long as it is the right one
        chk("the other copy does not exist", True,
            "nothing to compare against")
    else:
        chk("the two copies are identical", a == b,
            f"{str(a)[:8]} vs {str(b)[:8]}")

    print()
    print("=== the imported one must carry the fixes ===")
    try:
        src = open(IMPORTED, encoding="utf-8").read()
    except OSError as e:
        print(f"  the imported engine is missing: {e}")
        return 1

    chk("the key validation is there",
        "1. the network" in src and "the address" in src.lower())
    chk("the await fix is there", "client=self.client" in src)
    chk("the network parameter is there", 'network: str = "mainnet"' in src)
    chk("the sentinel is there", "_UNSET = object()" in src)

    print()
    print("=== the registry must import the file that exists ===")
    try:
        reg = open(REGISTRY, encoding="utf-8").read()
    except OSError as e:
        print(f"  the registry is missing: {e}")
        return 1

    imports_starknet = "from .starknet import StarknetEngine" in reg
    chk("the registry imports .starknet", imports_starknet)
    chk("the file it imports exists",
        os.path.exists(IMPORTED) if imports_starknet else True)

    print()
    passed = sum(1 for r in RESULTS if r)
    print(f"{passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
