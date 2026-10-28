#!/usr/bin/env python3
"""Prove the Starknet account can sign and send a transaction.

A deployed account contract is not yet a working account. The proof
that it works is a SIGNED TRANSACTION that the chain accepts.

The test:
  1. reads the account's class hash from the chain (it is deployed)
  2. signs a transfer with the account's own key
  3. broadcasts it
  4. waits for it to settle
  5. shows the fee was paid from the account itself

## The API moved

starknet-py 0.30.0 renamed the account methods:

    account.execute(...)        ->  account.execute_v3(...)
    account.wait_for_transaction ->  client.wait_for_tx(tx_hash)

The library version matters. On 0.25.1 the v3 deploy failed at the
RPC with "Field validation for 'L1DataGas' failed on the 'required'
tag" - the transaction was missing the fields a v3 transaction
requires. On 0.30.0 it deploys and signs.

Usage:
    prove_starknet_account.py --key-file /tmp/starknet_account_v2.json
"""
import argparse
import asyncio
import json
import sys

from starknet_py.hash.selector import get_selector_from_name
from starknet_py.net.client_models import Call
from starknet_py.net.full_node_client import FullNodeClient
from starknet_py.net.models.chains import StarknetChainId
from starknet_py.net.signer.key_pair import KeyPair
from starknet_py.net.signer.stark_curve_signer import StarkCurveSigner

RPCS = {
    "sepolia": "https://starknet-sepolia-rpc.publicnode.com",
    "mainnet": "https://starknet-rpc.publicnode.com",
}

STRK = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--network", default="sepolia")
    ap.add_argument("--amount", default="0.0000001")
    args = ap.parse_args()

    data = json.load(open(args.key_file))
    kp = KeyPair(int(data["private_key"], 16), data["public_key"])
    address = int(data["address"], 16)
    client = FullNodeClient(node_url=RPCS[args.network])

    chain_id = (StarknetChainId.SEPOLIA if args.network == "sepolia"
                else StarknetChainId.MAINNET)

    print("=== the account ===")
    try:
        ch = await client.get_class_hash_at(address, block_number="latest")
        print(f"  address:    {hex(address)}")
        print(f"  class hash: {hex(ch)}")
        print("  deployed:   yes")
    except Exception as e:
        print(f"  NOT deployed: {str(e)[:80]}")
        return 1

    from starknet_py.net.account.account import Account
    account = Account(
        client=client,
        address=hex(address),
        signer=StarkCurveSigner(hex(kp.private_key), kp, chain_id),
        chain=chain_id,
    )

    async def balance():
        raw = await client.call_contract(
            Call(to_addr=int(STRK, 16),
                 selector=get_selector_from_name("balance_of"),
                 calldata=[address]),
            block_number="latest")
        return int(raw[0])

    b0 = await balance()
    print()
    print("=== signing a transaction ===")
    print(f"  STRK before: {b0/1e18:,.8f}")

    try:
        response = await account.execute_v3(
            calls=[
                Call(to_addr=int(STRK, 16),
                     selector=get_selector_from_name("transfer"),
                     calldata=[address, int(float(args.amount) * 1e18), 0]),
            ],
            auto_estimate=True,
        )
        tx_hash = response.transaction_hash
        print(f"  tx: {tx_hash}")
    except Exception as e:
        print(f"  the signing failed: {type(e).__name__}")
        print(f"  {str(e)[:500]}")
        return 1

    print("  waiting for it to settle...")
    try:
        receipt = await client.wait_for_tx(tx_hash)
        print(f"  settled in block {getattr(receipt, 'block_number', '?')}")
    except Exception as e:
        print(f"  the wait failed: {type(e).__name__} {str(e)[:250]}")
        return 1

    b1 = await balance()
    print()
    print("=== the result ===")
    print(f"  STRK after:   {b1/1e18:,.8f}")
    print(f"  spent on gas: {(b0-b1)/1e18:,.8f} STRK")
    print()
    print("  The account signs, the chain accepts the signature, and the")
    print("  fee is paid from the account itself. That is a working")
    print("  Starknet account.")
    print()
    print(f"  tx: {tx_hash}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
