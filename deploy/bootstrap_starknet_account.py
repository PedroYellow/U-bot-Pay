#!/usr/bin/env python3
"""Bootstrap a Starknet account: derive it, fund it, deploy it.

A Starknet account is a CONTRACT. The sequence is:

  1. generate a key pair
  2. derive the contract address from (public key, salt, class hash)
  3. fund it - the address has to exist on chain first, and deploying
     it costs STRK, so the funding has to land BEFORE the deployment
  4. deploy the account contract, paying the fee in STRK

Step 3 is the one that catches people out: the faucet funds an ADDRESS,
and until the account contract is deployed at that address the funds sit
at an address with no code. Sending to it first is harmless; the
deployment is what makes it a working account.

The class hashes are verified against the chain rather than trusted from
a guide - `get_class_by_hash` is the right call for a class, and
`get_class_hash_at` is the right call for an address. Asking for a
class hash at a class hash returns "Contract not found", which looks
like the class is missing when it is not.

Usage:
    bootstrap_starknet_account.py --network sepolia
    bootstrap_starknet_account.py --network mainnet --class-hash 0x...
"""
import argparse
import asyncio
import json
import os
import sys

from starknet_py.hash.selector import get_selector_from_name
from starknet_py.net.client_models import Call
from starknet_py.net.full_node_client import FullNodeClient
from starknet_py.net.models.chains import StarknetChainId
from starknet_py.net.signer.key_pair import KeyPair
from starknet_py.net.signer.stark_curve_signer import StarkCurveSigner

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RPCS = {
    "sepolia": ["https://starknet-sepolia-rpc.publicnode.com"],
    "mainnet": ["https://starknet-rpc.publicnode.com",
                "https://starknet.api.onfinality.io/public"],
}

# The account class hashes, from the official sncast documentation.
# Each is verified against the chain before use.
CLASS_HASHES = {
    "oz v1.0.0": "0x05b4b537eaa2399e3aa99c4e2e0208ebd6c71bc1467938cd52c798c601e43564",
    "ready v0.4.0": "0x036078334509b514626504edc9fb252328d1a240e4e948bef8d0c08dff45927f",
    "braavos v1.2.0": "0x03957f9f5a1cbfe918cedc2015c85200ca51a5f7506ecb6de98a5207b759bf8a",
}

STRK = {
    "sepolia": "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d",
    "mainnet": "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d",
}


async def pick_client(network):
    for url in RPCS[network]:
        try:
            c = FullNodeClient(node_url=url)
            cid = await c.get_chain_id()
            print(f"  {url:56s} -> {cid}")
            return c, url
        except Exception as e:
            print(f"  {url:56s} -> {type(e).__name__} {str(e)[:50]}")
    return None, None


async def verify_class(client, class_hash):
    """A class hash has to be asked for with get_class_by_hash.

    get_class_hash_at asks for the class of an ADDRESS. Passing it a
    class hash returns "Contract not found", which reads like the class
    is missing when it is not.
    """
    try:
        await client.get_class_by_hash(class_hash, block_number="latest")
        return True
    except Exception:
        return False


async def strk_balance(client, token, address):
    raw = await client.call_contract(
        Call(to_addr=int(token, 16),
             selector=get_selector_from_name("balance_of"),
             calldata=[int(address, 16)]),
        block_number="latest")
    return int(raw[0]) / 1e18


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="sepolia", choices=["sepolia", "mainnet"])
    ap.add_argument("--class-hash", default=None,
                    help="the account class hash; the default is verified against the chain")
    ap.add_argument("--class-name", default="oz v1.0.0")
    ap.add_argument("--salt", default="0x0")
    ap.add_argument("--out", default=None, help="where to write the account json")
    args = ap.parse_args()

    print("=== the RPC ===")
    client, url = await pick_client(args.network)
    if client is None:
        print("  no working RPC")
        return 1

    print()
    print("=== the account class ===")
    class_hash = args.class_hash or CLASS_HASHES.get(args.class_name)
    if not class_hash:
        print(f"  no class hash for {args.class_name!r}")
        return 1
    if await verify_class(client, class_hash):
        print(f"  {args.class_name}: {class_hash}  (deployed)")
    else:
        print(f"  {args.class_name}: {class_hash}  NOT DEPLOYED on {args.network}")
        print("  the other candidates:")
        for label, ch in CLASS_HASHES.items():
            if await verify_class(client, ch):
                print(f"    {label}: {ch}")
        return 1

    print()
    print("=== the key ===")
    kp = KeyPair.generate()
    print(f"  public key:  {hex(kp.public_key)}")

    from starknet_py.net.account.account import Account
    address = Account.calculate_contract_address(
        salt=int(args.salt, 16),
        class_hash=int(class_hash, 16),
        constructor_calldata=[kp.public_key],
        deployer_address=0,
    )
    print(f"  address:     {hex(address)}")

    # is it already deployed?
    print()
    print("=== is it deployed? ===")
    try:
        await client.get_class_hash_at(address, block_number="latest")
        print("  yes - already deployed")
        deployed = True
    except Exception:
        print("  no")
        deployed = False

    if not deployed:
        print()
        print("=== funding it ===")
        if args.network != "sepolia":
            print("  mainnet funding is not automated here.")
            print("  use StarkGate, or the Foundation's grant.")
            return 1

        import subprocess
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "fund_starknet_sepolia.py"),
             hex(address), "--json"],
            capture_output=True, text=True, timeout=900)
        print(r.stdout[-1500:])
        if r.returncode != 0:
            print(r.stderr[-600:])
            return 1

        bal = await strk_balance(client, STRK[args.network], address)
        print(f"  STRK balance: {bal:,.6f}")
        if bal <= 0:
            print("  the address was not funded")
            return 1

        print()
        print("=== deploying the account ===")
        chain_id = (StarknetChainId.SEPOLIA if args.network == "sepolia"
                    else StarknetChainId.MAINNET)
        signer = StarkCurveSigner(hex(kp.private_key), kp, chain_id)
        account = Account(
            client=client,
            address=hex(address),
            signer=signer,
            chain=chain_id,
        )
        try:
            deploy = await account.deploy_account_v2(
                client=client,
                class_hash=int(class_hash, 16),
                salt=int(args.salt, 16),
                constructor_calldata=[kp.public_key],
                max_fee=0,
            )
            print(f"  tx: {deploy.transaction_hash}")
            await account.wait_for_transaction(deploy.transaction_hash)
            print("  deployed")
        except Exception as e:
            print(f"  the deployment failed: {type(e).__name__} {str(e)[:200]}")
            return 1

    bal = await strk_balance(client, STRK[args.network], address)
    print()
    print("=== the account ===")
    print(f"  address:      {hex(address)}")
    print(f"  private key:  {hex(kp.private_key)}")
    print(f"  STRK balance: {bal:,.6f}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({
                "network": args.network,
                "address": hex(address),
                "private_key": hex(kp.private_key),
                "public_key": hex(kp.public_key),
                "class_hash": class_hash,
                "salt": args.salt,
                "strk_balance": bal,
                "rpc": url,
            }, fh, indent=2)
        os.chmod(args.out, 0o600)
        print(f"  written to {args.out} (0600)")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
