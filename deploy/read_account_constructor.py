#!/usr/bin/env python3
"""Read an account class's constructor signature from the chain.

The Ready account rejected the deploy with:

    Failed to deserialize param #1

which means the constructor wanted more than the one parameter that was
passed - the public key. The Ready account takes a second parameter,
the guardian. The class's ABI carries the real signature.

The library returns the ABI as a raw JSON string, so it has to be
parsed before it can be read.

Usage: read_account_constructor.py [class-hash ...]
"""
import asyncio
import json
import sys

from starknet_py.net.full_node_client import FullNodeClient

CLASSES = {
    "oz v1.0.0": "0x05b4b537eaa2399e3aa99c4e2e0208ebd6c71bc1467938cd52c798c601e43564",
    "ready v0.4.0": "0x036078334509b514626504edc9fb252328d1a240e4e948bef8d0c08dff45927f",
    "braavos v1.2.0": "0x03957f9f5a1cbfe918cedc2015c85200ca51a5f7506ecb6de98a5207b759bf8a",
}

RPC = "https://starknet-sepolia-rpc.publicnode.com"


async def main():
    client = FullNodeClient(node_url=RPC)

    hashes = dict(CLASSES)
    for arg in sys.argv[1:]:
        hashes[f"custom {arg[:10]}"] = arg

    for label, ch in hashes.items():
        print(f"=== {label} ===")
        print(f"  class hash: {ch}")
        try:
            cls = await client.get_class_by_hash(ch, block_number="latest")
        except Exception as e:
            print(f"  not deployed: {str(e)[:70]}")
            continue

        abi = getattr(cls, "abi", None)
        if isinstance(abi, str):
            try:
                abi = json.loads(abi)
            except Exception:
                print("  the ABI is not JSON")
                abi = None

        found = False
        if isinstance(abi, list):
            for item in abi:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "constructor":
                    found = True
                    inputs = item.get("inputs") or []
                    print(f"  constructor takes {len(inputs)} parameter(s):")
                    for i, inp in enumerate(inputs):
                        print(f"    #{i} {inp.get('name')}: {inp.get('type')}")

        if not found and isinstance(abi, list):
            # some classes expose it under a different entry
            names = [i.get("name") for i in abi
                     if isinstance(i, dict) and i.get("type") == "function"]
            print(f"  no constructor in the ABI; {len(names)} functions")

        # the entry points by type
        eps = getattr(cls, "entry_points_by_type", None)
        if eps is not None:
            ctor = getattr(eps, "CONSTRUCTOR", None)
            if ctor:
                print(f"  entry points: CONSTRUCTOR has {len(ctor)}")
                for e in ctor:
                    print(f"    {e.name}: {[i.type for i in e.inputs]}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
