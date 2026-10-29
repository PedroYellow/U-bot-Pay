#!/usr/bin/env python3
"""Test the Starknet engine's key validation.

The bug this covers: the mainnet deployment pointed at a SEPOLIA
account file and has_signer() said True. The engine loaded any file
with a private_key in it and never asked whether the key belonged to
this network or to this address.

So a deployment advertised a signer it could not use. A send would
have failed obscurely, or worse, signed for an account the dashboard
does not publish.

These tests use the REAL key files on the deployment, and real
temporary files for the wrong cases. No key material is printed.

Usage: test_starknet_key_validation.py
"""
import json
import os
import shutil
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chains_starknet_engine import StarknetEngine  # noqa: E402

USDC = "0x053c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8"
MAINNET_RPC = "https://starknet-rpc.publicnode.com"

SEPOLIA_KEY = "/opt/ustartkpay/secrets/account.json"
MAINNET_KEY = "/opt/ustartkpay/secrets/mainnet_account.json"

RESULTS = []


def chk(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print("%s  %-58s %s" % ("PASS" if cond else "FAIL", name, detail))


def engine(key_file, account_address, network):
    return StarknetEngine(
        rpc=MAINNET_RPC,
        stable_address=USDC,
        explorer="https://starkscan.co",
        native_symbol="STRK",
        account_address=account_address,
        key_file=key_file,
        network=network,
    )


def copy_with(src, changes, mode=0o600):
    """A copy of a key file with some fields changed."""
    d = json.load(open(src))
    d.update(changes)
    fd, path = tempfile.mkstemp(prefix="stark_key_", suffix=".json")
    os.close(fd)
    with open(path, "w") as fh:
        json.dump(d, fh)
    os.chmod(path, mode)
    return path


def main():
    if not os.path.exists(SEPOLIA_KEY):
        print("  the sepolia key file is missing:", SEPOLIA_KEY)
        return 1

    sepolia = json.load(open(SEPOLIA_KEY))
    sepolia_addr = sepolia["address"]

    print("=== the key file is read at all ===")
    eng = engine(SEPOLIA_KEY, sepolia_addr, "sepolia")
    chk("a matching key loads", eng._key_pair() is not None)
    chk("has_signer() is True for a matching key", eng.has_signer() is True)

    print()
    print("=== the network must match ===")
    eng = engine(SEPOLIA_KEY, sepolia_addr, "mainnet")
    chk("a sepolia key is rejected by a mainnet deployment",
        eng._key_pair() is None)
    chk("has_signer() is False for the wrong network",
        eng.has_signer() is False)

    print()
    print("=== the address must match ===")
    eng = engine(SEPOLIA_KEY, "0x" + "11" * 31 + "22", "sepolia")
    chk("a key whose address differs is rejected",
        eng._key_pair() is None)
    chk("has_signer() is False for the wrong address",
        eng.has_signer() is False)

    print()
    print("=== the derivation must match ===")
    # the address does not match the one derived from the key
    path = copy_with(SEPOLIA_KEY, {"address": "0x" + "aa" * 31 + "bb"})
    try:
        eng = engine(path, "0x" + "aa" * 31 + "bb", "sepolia")
        chk("a hand-edited address is rejected by the derivation check",
            eng._key_pair() is None)
    finally:
        os.unlink(path)

    print()
    print("=== the permissions must be 0600 ===")
    path = copy_with(SEPOLIA_KEY, {}, mode=0o644)
    try:
        eng = engine(path, sepolia_addr, "sepolia")
        chk("a world-readable key file is refused", eng._key_pair() is None)
        chk("has_signer() is False for a readable key file",
            eng.has_signer() is False)
    finally:
        os.unlink(path)

    print()
    print("=== no key file at all ===")
    eng = engine("", sepolia_addr, "sepolia")
    chk("no key file means no signer", eng.has_signer() is False)

    print()
    print("=== the result is cached ===")
    eng = engine(SEPOLIA_KEY, sepolia_addr, "sepolia")
    eng._key_pair()
    # replace the file with garbage - a cached key must survive it
    path = copy_with(SEPOLIA_KEY, {})
    try:
        eng2 = engine(path, sepolia_addr, "sepolia")
        eng2._key_pair()
        with open(path, "w") as fh:
            fh.write("not json")
        chk("a cached key is not re-read from disk",
            eng2._key_pair() is not None)
    finally:
        os.unlink(path)

    print()
    print("=== submit() refuses without a signer ===")
    eng = engine(SEPOLIA_KEY, sepolia_addr, "mainnet")
    res = eng.submit("0x" + "33" * 31 + "44", 1.0)
    chk("submit() refuses with no signer",
        res.get("ok") is False and "signer" in str(res.get("error", "")),
        str(res.get("error", ""))[:70])

    print()
    passed = sum(1 for r in RESULTS if r)
    print(f"{passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
