#!/usr/bin/env python3
"""Report the Starknet accounts, and flag anything stranded.

Two accounts exist on Starknet Sepolia. One works; one is stranded.

    0x4af96d3945b91f77f2f36abfd45291ec5e0d7d753709ee2a5484a34f05ce259
        the working one - deployed, and it signs

    0x447a57e2d4632b54e4ae2cff361d3b5285adc8ff95916ca4cf51340ae9bf681
        stranded - 5 STRK, no contract at the address, and NO KEY

The second one is the price of getting the order wrong. The first
bootstrap generated a key, derived the address, funded it, and then
crashed before writing the key out. The address is DERIVED from the
key, so with the key gone the funds are unrecoverable - 5 STRK of
Sepolia testnet money, sitting at an address with no code and no way
to spend it.

The bootstrap now writes the key FIRST, before anything is sent to the
chain. The stranded account is the receipt for why.

It is reported here rather than forgotten, because a balance at an
address nobody holds the key for looks like a problem until you know
the story. It is not a problem - it is 5 STRK of testnet money that
cannot be moved and cannot be recovered.

Usage: report_starknet_accounts.py [--json]
"""
import json
import os
import sys

KEY_FILE = "/opt/ustartkpay_secrets/starknet_sepolia_account.json"

# The address whose key was lost, and the amount left at it.
ORPHAN = "0x447a57e2d4632b54e4ae2cff361d3b5285adc8ff95916ca4cf51340ae9bf681"
ORPHAN_STRK = 5.0

STRK = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"


def main():
    import asyncio

    from starknet_py.hash.selector import get_selector_from_name
    from starknet_py.net.client_models import Call
    from starknet_py.net.full_node_client import FullNodeClient

    RPC = "https://starknet-sepolia-rpc.publicnode.com"

    async def check():
        c = FullNodeClient(node_url=RPC)
        out = []

        if os.path.exists(KEY_FILE):
            d = json.load(open(KEY_FILE))
            addr = d["address"]
            try:
                ch = await c.get_class_hash_at(int(addr, 16),
                                               block_number="latest")
                deployed = hex(ch)
            except Exception:
                deployed = None
            bal = await _strk(c, addr)
            out.append({
                "address": addr,
                "role": "the working account",
                "deployed": deployed is not None,
                "class_hash": deployed,
                "strk": bal,
                "key_file": KEY_FILE,
            })
        else:
            out.append({
                "address": None,
                "role": "the working account",
                "deployed": False,
                "class_hash": None,
                "strk": 0.0,
                "key_file": KEY_FILE,
                "error": "the key file is missing",
            })

        try:
            ch = await c.get_class_hash_at(int(ORPHAN, 16),
                                           block_number="latest")
            orphan_deployed = hex(ch)
        except Exception:
            orphan_deployed = None
        bal = await _strk(c, ORPHAN)
        out.append({
            "address": ORPHAN,
            "role": "STRANDED - the key was lost, the funds cannot move",
            "deployed": orphan_deployed is not None,
            "class_hash": orphan_deployed,
            "strk": bal,
            "key_file": None,
        })

        return out

    async def _strk(c, addr):
        raw = await c.call_contract(
            Call(to_addr=int(STRK, 16),
                 selector=get_selector_from_name("balance_of"),
                 calldata=[int(addr, 16)]),
            block_number="latest")
        return int(raw[0]) / 1e18

    rows = asyncio.run(check())

    if "--json" in sys.argv:
        print(json.dumps(rows, indent=2))
        return 0

    print("=== the Starknet Sepolia accounts ===")
    for r in rows:
        print(f"  {r['address']}")
        print(f"    role:     {r['role']}")
        print(f"    deployed: {'yes' if r['deployed'] else 'no'}")
        if r["class_hash"]:
            print(f"    class:    {r['class_hash']}")
        print(f"    STRK:     {r['strk']:,.6f}")
        if r.get("key_file"):
            print(f"    key:      {r['key_file']}")
        print()

    working = [r for r in rows if r["role"] == "the working account"]
    if working and working[0]["deployed"]:
        print("  the working account is deployed and holds its key")
    else:
        print("  PROBLEM: the working account is not deployed")
        return 1

    print()
    print(f"  note: {ORPHAN_STRK:g} STRK is stranded at the orphan address.")
    print("  The key was lost before it was written to disk, and the")
    print("  address is derived from the key - so the funds cannot be")
    print("  recovered. It is Sepolia testnet money.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
