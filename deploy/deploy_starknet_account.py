#!/usr/bin/env python3
"""Deploy the Starknet account contract at a prefunded address.

The account is already funded - 5 STRK sits at the address - but the
ACCOUNT CONTRACT is not deployed there yet. On Starknet an account is
a contract; a funded address with no code at it can hold money but
cannot sign anything.

The library's signature needs the address and the key pair explicitly:

    deploy_account_v3(address=..., class_hash=..., salt=...,
                      key_pair=..., client=..., constructor_calldata=...)

The address must be DERIVED from the same public key, salt and class
hash, or the deployment lands at an address that was never funded.

Usage:
    deploy_starknet_account.py --key-file /tmp/starknet_key.json
    deploy_starknet_account.py --generate --out /tmp/starknet_account.json
"""
import argparse
import asyncio
import json
import os
import sys

from starknet_py.hash.address import compute_address
from starknet_py.hash.selector import get_selector_from_name
from starknet_py.net.client_models import Call
from starknet_py.net.full_node_client import FullNodeClient
from starknet_py.net.signer.key_pair import KeyPair

RPCS = {
    "sepolia": "https://starknet-sepolia-rpc.publicnode.com",
    "mainnet": "https://starknet-rpc.publicnode.com",
}

CLASS_HASH = "0x05b4b537eaa2399e3aa99c4e2e0208ebd6c71bc1467938cd52c798c601e43564"
STRK = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"


async def strk_balance(client, address):
    raw = await client.call_contract(
        Call(to_addr=int(STRK, 16),
             selector=get_selector_from_name("balance_of"),
             calldata=[int(address, 16)]),
        block_number="latest")
    return int(raw[0]) / 1e18


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="sepolia", choices=["sepolia", "mainnet"])
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--salt", default="0x0")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    client = FullNodeClient(node_url=RPCS[args.network])

    # ── the key ────────────────────────────────────────────────────
    if args.key_file and os.path.exists(args.key_file):
        data = json.load(open(args.key_file))
        pk = int(data["private_key"], 16)
        kp = KeyPair(pk, data["public_key"])
        print(f"=== the key ===")
        print(f"  from {args.key_file}")
    elif args.generate:
        kp = KeyPair.generate()
        print("=== the key ===")
        print(f"  generated: {hex(kp.public_key)}")
    else:
        print("  pass --key-file or --generate")
        return 1

    print(f"  public key:  {hex(kp.public_key)}")
    print(f"  private key: {hex(kp.private_key)}")

    # ── the address ────────────────────────────────────────────────
    address = compute_address(
        salt=int(args.salt, 16),
        class_hash=int(CLASS_HASH, 16),
        constructor_calldata=[kp.public_key],
        deployer_address=0,
    )
    print(f"  address:     {hex(address)}")

    # ── is it deployed? ────────────────────────────────────────────
    print()
    print("=== the state ===")
    deployed = False
    try:
        ch = await client.get_class_hash_at(address, block_number="latest")
        print(f"  DEPLOYED as a contract: {ch}")
        deployed = True
    except Exception:
        print("  not deployed")

    bal = await strk_balance(client, address)
    print(f"  STRK balance: {bal:,.6f}")

    if deployed:
        print("  nothing to do")
        if args.out:
            _write(args.out, kp, address, args, bal)
        return 0

    if bal <= 0:
        print()
        print("  the address holds nothing, so the deployment cannot be paid for.")
        print("  fund it with the Foundation's faucet first:")
        print(f"    python fund_starknet_sepolia.py {hex(address)}")
        return 1

    # ── deploy ─────────────────────────────────────────────────────
    print()
    print("=== deploying ===")
    from starknet_py.net.account.account import Account
    from starknet_py.net.models.chains import StarknetChainId

    chain_id = (StarknetChainId.SEPOLIA if args.network == "sepolia"
                else StarknetChainId.MAINNET)
    account = Account(
        client=client,
        address=hex(address),
        signer=None,
        chain=chain_id,
    )
    # the signer is the key pair; the account signs the deploy itself
    account.signer = None

    from starknet_py.net.signer.stark_curve_signer import StarkCurveSigner
    account.signer = StarkCurveSigner(hex(kp.private_key), kp, chain_id)

    try:
        result = await account.deploy_account_v3(
            address=hex(address),
            class_hash=int(CLASS_HASH, 16),
            salt=int(args.salt, 16),
            key_pair=kp,
            client=client,
            constructor_calldata=[kp.public_key],
            auto_estimate=True,
        )
        print(f"  tx: {result.transaction_hash}")
    except Exception as e:
        print(f"  the deployment failed: {type(e).__name__} {str(e)[:300]}")
        return 1

    print("  waiting for it to settle...")
    try:
        await account.wait_for_transaction(result.transaction_hash)
        print("  settled")
    except Exception as e:
        print(f"  the wait failed: {type(e).__name__} {str(e)[:200]}")
        print("  the transaction was sent - check the explorer before retrying")
        return 1

    # ── verify ─────────────────────────────────────────────────────
    print()
    print("=== the result ===")
    try:
        ch = await client.get_class_hash_at(address, block_number="latest")
        print(f"  DEPLOYED as a contract: {ch}")
        ok = True
    except Exception as e:
        print(f"  still not deployed: {str(e)[:80]}")
        ok = False

    bal = await strk_balance(client, address)
    print(f"  STRK balance: {bal:,.6f}")
    print(f"  address:      {hex(address)}")

    if args.out:
        _write(args.out, kp, address, args, bal)

    return 0 if ok else 1


def _write(path, kp, address, args, bal):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "network": args.network,
            "address": hex(address),
            "private_key": hex(kp.private_key),
            "public_key": hex(kp.public_key),
            "class_hash": CLASS_HASH,
            "salt": args.salt,
            "strk_balance": bal,
            "rpc": RPCS[args.network],
        }, fh, indent=2)
    os.chmod(path, 0o600)
    print(f"  written to {path} (0600)")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
