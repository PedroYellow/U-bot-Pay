#!/usr/bin/env python3
"""Correct the stale `deployed` flag in the Starknet account file.

The file says:

    "deployed": false

but the account contract IS deployed. Verified on Sepolia:

    get_class_hash_at(0x4af96d...e259)
      -> 0x05b4b537eaa2399e3aa99c4e2e0208ebd6c71bc1467938cd52c798c601e43564

which is the OpenZeppelin v1.0.0 account class - the same class hash
the file already records. A contract with a class hash at its address
is deployed; the flag was simply never flipped after the deployment
succeeded, and the account has since signed and settled a transaction
(block 17302376).

Why it matters: the flag is what says whether the service can send.
A false flag makes a working account look undeployable, so the engine
refuses to sign and the deployment reads as broken when it is not.

The flag is corrected from the chain, not asserted - the script asks
the RPC for the class hash at the address, and only writes true if the
chain confirms a contract is there.

Usage: fix_starknet_account_flag.py [account.json]
"""
import json
import sys

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else \
    "/opt/ustartkpay_secrets/starknet_sepolia_account.json"

RPC = "https://starknet-sepolia-rpc.publicnode.com"


def main():
    import asyncio
    from starknet_py.net.full_node_client import FullNodeClient

    acct = json.load(open(ACCOUNT))
    addr = acct["address"]
    expected_class = acct.get("class_hash", "")

    async def probe():
        c = FullNodeClient(node_url=RPC)
        return await c.get_class_hash_at(int(addr, 16))

    try:
        got = asyncio.run(probe())
    except Exception as e:
        print(f"  the RPC did not answer: {type(e).__name__}: {str(e)[:100]}")
        print("  the flag was NOT changed")
        return

    if not got:
        print(f"  no contract at {addr} - the flag stays false")
        return

    got_hex = hex(got)
    exp_hex = (expected_class if expected_class.startswith("0x")
               else "0x" + expected_class) if expected_class else ""

    print(f"  the address:        {addr}")
    print(f"  the class hash:     {got_hex}")
    if exp_hex:
        same = int(got_hex, 16) == int(exp_hex, 16)
        print(f"  the expected class: {exp_hex}  {'(a match)' if same else '(DIFFERENT!)'}")

    if acct.get("deployed"):
        print("  the flag was already true")
        return

    if exp_hex and int(got_hex, 16) != int(exp_hex, 16):
        print("  the class hash does not match the file - the flag was NOT changed")
        return

    acct["deployed"] = True
    # keep the cached balance honest too
    acct.pop("strk_balance", None)

    with open(ACCOUNT, "w") as f:
        json.dump(acct, f, indent=2)
    os_mode = None
    try:
        os_mode = oct(__import__("os").stat(ACCOUNT).st_mode & 0o777)
    except Exception:
        pass
    print(f"  the flag is now true. The file's mode: {os_mode}")


if __name__ == "__main__":
    main()
