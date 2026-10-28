#!/usr/bin/env python3
"""Bootstrap a Starknet account end to end, in the right order.

The order matters, and getting it wrong loses the key:

  1. generate the key
  2. DERIVE the address from (public key, salt, class hash)
  3. SAVE THE KEY - to a file, 0600
  4. fund the address with the Foundation's faucet
  5. deploy the account contract at that address

The first attempt at this did 1-2, funded, and then crashed before
writing the key out. The 5 STRK sat at an address whose private key
no longer existed anywhere - unrecoverable, because the address is
derived from the key. On testnet it cost nothing; on mainnet it would
have cost a funding round trip and a support ticket.

So the key is written FIRST, before anything is sent to the chain.

The account contract has to be deployed before it can sign anything,
and the deployment has to be paid for in STRK - so the address is
funded before the deployment. The library says the same thing in its
own docstring: "Provided address must be first prefunded with enough
tokens."

Usage:
    bootstrap_starknet_account_v2.py --network sepolia --out /tmp/starknet_account.json
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys

from starknet_py.hash.address import compute_address
from starknet_py.hash.selector import get_selector_from_name
from starknet_py.net.client_models import Call
from starknet_py.net.full_node_client import FullNodeClient
from starknet_py.net.models.chains import StarknetChainId
from starknet_py.net.signer.key_pair import KeyPair
from starknet_py.net.signer.stark_curve_signer import StarkCurveSigner

HERE = os.path.dirname(os.path.abspath(__file__))

RPCS = {
    "sepolia": "https://starknet-sepolia-rpc.publicnode.com",
    "mainnet": "https://starknet-rpc.publicnode.com",
}

CLASS_HASHES = {
    "oz v1.0.0": "0x05b4b537eaa2399e3aa99c4e2e0208ebd6c71bc1467938cd52c798c601e43564",
    "ready v0.4.0": "0x036078334509b514626504edc9fb252328d1a240e4e948bef8d0c08dff45927f",
    "braavos v1.2.0": "0x03957f9f5a1cbfe918cedc2015c85200ca51a5f7506ecb6de98a5207b759bf8a",
}

STRK = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"


async def strk_balance(client, address):
    # compute_address returns an int, so it cannot be passed to
    # int(x, 16) - that wants a string.
    _addr = address if isinstance(address, int) else int(address, 16)
    raw = await client.call_contract(
        Call(to_addr=int(STRK, 16),
             selector=get_selector_from_name("balance_of"),
             calldata=[_addr]),
        block_number="latest")
    return int(raw[0]) / 1e18


def save(path, kp, address, args, bal=0.0):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "network": args.network,
            "address": hex(address),
            "private_key": hex(kp.private_key),
            "public_key": hex(kp.public_key),
            "class_hash": CLASS_HASHES[args.class_name],
            "salt": args.salt,
            "strk_balance": bal,
            "rpc": RPCS[args.network],
            "deployed": False,
        }, fh, indent=2)
    os.chmod(path, 0o600)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="sepolia", choices=["sepolia", "mainnet"])
    ap.add_argument("--class-name", default="oz v1.0.0", choices=list(CLASS_HASHES))
    ap.add_argument("--salt", default="0x0")
    ap.add_argument("--out", default="/tmp/starknet_account.json")
    ap.add_argument("--skip-fund", action="store_true")
    ap.add_argument("--skip-deploy", action="store_true")
    args = ap.parse_args()

    class_hash = CLASS_HASHES[args.class_name]
    client = FullNodeClient(node_url=RPCS[args.network])

    # ── 1. the key ────────────────────────────────────────────────
    print("=== the key ===")
    if os.path.exists(args.out):
        data = json.load(open(args.out))
        kp = KeyPair(int(data["private_key"], 16), data["public_key"])
        print(f"  reusing the key in {args.out}")
    else:
        kp = KeyPair.generate()
        print(f"  generated: {hex(kp.public_key)}")

    # ── 2. the address ────────────────────────────────────────────
    address = compute_address(
        salt=int(args.salt, 16),
        class_hash=int(class_hash, 16),
        constructor_calldata=[kp.public_key],
        deployer_address=0,
    )
    print(f"  address:     {hex(address)}")

    # ── 3. SAVE THE KEY, BEFORE ANYTHING LEAVES THE MACHINE ───────
    save(args.out, kp, address, args)
    print(f"  key saved to {args.out} (0600)")

    # ── the class has to be real ──────────────────────────────────
    print()
    print("=== the account class ===")
    try:
        await client.get_class_by_hash(class_hash, block_number="latest")
        print(f"  {args.class_name}: deployed")
    except Exception:
        print(f"  {args.class_name}: NOT DEPLOYED on {args.network}")
        return 1

    # ── already deployed? ─────────────────────────────────────────
    print()
    print("=== the state ===")
    deployed = False
    try:
        ch = await client.get_class_hash_at(address, block_number="latest")
        print(f"  DEPLOYED: {ch}")
        deployed = True
    except Exception:
        print("  not deployed")

    bal = await strk_balance(client, address)
    print(f"  STRK balance: {bal:,.6f}")

    if not deployed and not args.skip_deploy:
        # ── 4. fund it ─────────────────────────────────────────────
        if bal <= 0 and not args.skip_fund:
            if args.network != "sepolia":
                print()
                print("  mainnet funding is not automated here.")
                print("  use StarkGate, or the Foundation's grant.")
                return 1
            print()
            print("=== funding ===")
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, "fund_starknet_sepolia.py"),
                 hex(address), "--json"],
                capture_output=True, text=True, timeout=900)
            print(r.stdout[-1200:])
            if r.returncode != 0:
                print(r.stderr[-600:])
                return 1

            bal = await strk_balance(client, address)
            print(f"  STRK balance: {bal:,.6f}")
            if bal <= 0:
                print("  the address was not funded")
                return 1

        # ── 5. deploy ─────────────────────────────────────────────
        print()
        print("=== deploying ===")
        from starknet_py.net.account.account import Account
        chain_id = (StarknetChainId.SEPOLIA if args.network == "sepolia"
                    else StarknetChainId.MAINNET)
        account = Account(
            client=client,
            address=hex(address),
            signer=StarkCurveSigner(hex(kp.private_key), kp, chain_id),
            chain=chain_id,
        )
        try:
            # v1 sends v1 resource bounds, v3 sends v3 bounds. The
            # OZ v1.0.0 account class is a CAIRO 0 contract and the
            # node rejects v3 bounds for it:
            #   Field validation for 'L1DataGas' failed
            # So the deploy version has to match the class.
            deploy = (account.deploy_account_v1
                      if args.class_name == "oz v1.0.0"
                      else account.deploy_account_v3)
            result = await deploy(
                address=hex(address),
                class_hash=int(class_hash, 16),
                salt=int(args.salt, 16),
                key_pair=kp,
                client=client,
                constructor_calldata=[kp.public_key],
                auto_estimate=True,
            )
            print(f"  tx: {result.transaction_hash}")
        except Exception as e:
            print(f"  the deployment failed: {type(e).__name__} {str(e)[:300]}")
            save(args.out, kp, address, args, bal)
            return 1

        print("  waiting for it to settle...")
        try:
            await account.wait_for_transaction(result.transaction_hash)
            print("  settled")
        except Exception as e:
            print(f"  the wait failed: {type(e).__name__} {str(e)[:200]}")
            print("  the transaction was sent - check the explorer")
            save(args.out, kp, address, args, bal)
            return 1

    # ── the result ────────────────────────────────────────────────
    print()
    print("=== the result ===")
    ok = False
    try:
        ch = await client.get_class_hash_at(address, block_number="latest")
        print(f"  DEPLOYED: {ch}")
        ok = True
    except Exception:
        print("  not deployed")

    bal = await strk_balance(client, address)
    print(f"  address:      {hex(address)}")
    print(f"  STRK balance: {bal:,.6f}")
    print(f"  key:          {args.out}")

    save(args.out, kp, address, args, bal)
    with open(args.out, encoding="utf-8") as fh:
        data = json.load(fh)
    data["deployed"] = ok
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(args.out, 0o600)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
