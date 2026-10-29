#!/usr/bin/env python3
"""Read the U-StarkPay treasury state on Starknet.

The engine test reports a USDC balance of 0.0. The reason is in
/opt/ustartkpay/.env, next to the setting:

    # The treasury address. Empty on purpose: the MAINNET account does
    # not exist yet, and an address that does not exist is not a lie the
    # dashboard can publish. The engine runs read-only until it does.
    USTART_WALLET_ADDRESS=

So there is no mainnet account, no treasury address, and nothing to
read. That is deliberate and correct - publishing an address that does
not exist would put a lie on the dashboard.

This reports the gap precisely instead of guessing at it:

  - the deployment's address settings, and whether they are set
  - the account file, and whether it is deployed
  - the LIVE native balance of that account, through the engine
  - the mainnet USDC contract, as a liveness check

It goes through the ENGINE rather than through starknet_py directly.
Not out of laziness: the engine's call shape is the one that works on
this version - a Call object with a selector derived from the entry
point name and an explicit block_number - and its 31 passing tests
are the proof. A second implementation of the same reads is a second
thing to be wrong.

Usage: check_starknet_treasury.py
"""
import json
import os
import sys

sys.path.insert(0, "/opt/ustartkpay")

ACCOUNT_FILE = "/opt/ustartkpay_secrets/starknet_sepolia_account.json"

USDC_MAINNET = "0x053c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8"
STRK = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"
SEPOLIA_RPC = "https://starknet-sepolia-rpc.publicnode.com"
MAINNET_RPC = "https://starknet-rpc.publicnode.com"


def read_env(path="/opt/ustartkpay/.env"):
    env = {}
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def main():
    env = read_env()

    print("  the deployment's address settings:")
    for k in ("USTART_WALLET_ADDRESS", "USTART_REVENUE_ADDRESS"):
        v = env.get(k, "")
        print(f"    {k:26s} {v if v else '(empty - no mainnet account yet)'}")

    print()
    print("  the account file:")
    acct = {}
    try:
        acct = json.load(open(ACCOUNT_FILE))
        print(f"    the address:       {acct['address']}")
        print(f"    the network:       {acct.get('network')}")
        print(f"    the class hash:    {acct.get('class_hash')}")
        print(f"    the deployed flag: {acct.get('deployed')}")
    except Exception as e:
        print(f"    -> {type(e).__name__}: {str(e)[:100]}")
        return

    from chains_starknet_engine import StarknetEngine

    print()
    print("  the LIVE native balance of that account, through the engine:")
    try:
        eng = StarknetEngine(
            rpc=acct.get("rpc", SEPOLIA_RPC),
            stable_address=USDC_MAINNET,   # only the native read is used
            explorer="https://sepolia.starkscan.co",
            native_symbol="STRK",
            account_address=acct.get("address", ""),
        )
        native = eng._run(eng._native_balance_async(acct["address"]))
        print(f"    the STRK: {native}")
    except Exception as e:
        print(f"    -> {type(e).__name__}: {str(e)[:140]}")

    print()
    print("  the mainnet USDC contract, as a liveness check:")
    try:
        eng = StarknetEngine(
            rpc=MAINNET_RPC,
            stable_address=USDC_MAINNET,
            explorer="https://starkscan.co",
            native_symbol="STRK",
            account_address="",
        )
        probe = "0x0000000000000000000000000000000000000000000000000000000000000001"
        val = eng._run(eng._balance_async(probe))
        print(f"    the mainnet RPC answers: yes")
        print(f"    the mainnet USDC reads:  {val}")
    except Exception as e:
        print(f"    -> {type(e).__name__}: {str(e)[:140]}")


if __name__ == "__main__":
    main()
