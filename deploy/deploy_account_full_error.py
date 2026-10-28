#!/usr/bin/env python3
"""Deploy a Starknet account and print the FULL error.

The truncated error said:

    Key: '...Transaction.ResourceBounds.L1DataGas'
    Error: Field validation for 'L1DataGas' failed on the ...

and the message cut off exactly where the reason is. The full text
says whether the bounds were zero, negative, or over the limit - and
those need different fixes.

Usage:
    deploy_account_full_error.py --key-file /tmp/sk.json [--class-name ...]
"""
import argparse
import asyncio
import json
import os
import sys

from starknet_py.hash.address import compute_address
from starknet_py.net.full_node_client import FullNodeClient
from starknet_py.net.models.chains import StarknetChainId
from starknet_py.net.signer.key_pair import KeyPair
from starknet_py.net.signer.stark_curve_signer import StarkCurveSigner

CLASS_HASHES = {
    "oz v1.0.0": "0x05b4b537eaa2399e3aa99c4e2e0208ebd6c71bc1467938cd52c798c601e43564",
    "ready v0.4.0": "0x036078334509b514626504edc9fb252328d1a240e4e948bef8d0c08dff45927f",
    "braavos v1.2.0": "0x03957f9f5a1cbfe918cedc2015c85200ca51a5f7506ecb6de98a5207b759bf8a",
}

RPCS = {
    "sepolia": "https://starknet-sepolia-rpc.publicnode.com",
    "mainnet": "https://starknet-rpc.publicnode.com",
}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--network", default="sepolia")
    ap.add_argument("--class-name", default="oz v1.0.0")
    args = ap.parse_args()

    data = json.load(open(args.key_file))
    kp = KeyPair(int(data["private_key"], 16), data["public_key"])
    class_hash = CLASS_HASHES[args.class_name]
    salt = int(data.get("salt", "0x0"), 16)

    address = compute_address(
        salt=salt,
        class_hash=int(class_hash, 16),
        constructor_calldata=[kp.public_key],
        deployer_address=0,
    )
    print(f"  address: {hex(address)}")
    print(f"  class:   {args.class_name}")

    client = FullNodeClient(node_url=RPCS[args.network])
    chain_id = (StarknetChainId.SEPOLIA if args.network == "sepolia"
                else StarknetChainId.MAINNET)

    from starknet_py.net.account.account import Account
    account = Account(
        client=client,
        address=hex(address),
        signer=StarkCurveSigner(hex(kp.private_key), kp, chain_id),
        chain=chain_id,
    )

    for method in ("deploy_account_v3", "deploy_account_v1"):
        fn = getattr(account, method, None)
        if fn is None:
            continue
        print()
        print(f"=== {method} ===")
        try:
            result = await fn(
                address=hex(address),
                class_hash=int(class_hash, 16),
                salt=salt,
                key_pair=kp,
                client=client,
                constructor_calldata=[kp.public_key],
                auto_estimate=True,
            )
            print(f"  tx: {result.transaction_hash}")
            await account.wait_for_transaction(result.transaction_hash)
            print("  settled")
            return 0
        except Exception as e:
            print(f"  {type(e).__name__}")
            print(f"  {e}")
            # the message attribute holds the detail
            for attr in ("message", "data", "body"):
                v = getattr(e, attr, None)
                if v:
                    print(f"  {attr}: {v}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
