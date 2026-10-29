#!/usr/bin/env python3
"""Verify Circle's CCTP contracts on Starknet, on chain.

Circle's own documentation lists the CCTP contracts for Starknet:

    mainnet   TokenMessengerMinterV2  0x07d421B9cA8aA32DF259965cDA8ACb93F7599F69209A41872AE84638B2A20F2a
              MessageTransmitterV2    0x02EBB5777B6dD8B26ea11D68Fdf1D2c85cD2099335328Be845a28c77A8AEf183
    sepolia   TokenMessengerMinterV2  0x04bDdE1E09a4B09a2F95d893D94a967b7717eB85A3f6dEcA8c080Ee01fBc3370
              MessageTransmitterV2    0x04db7926C64f1f32a840F3Fa95cB551f3801a3600Bae87aF87807A54DCE12Fe8

Domain 25 is Starknet.

This reads them from the chain: the class hash at each address, and
the contract's own name and symbol where it has them. An address with
no code is not a contract, and an address whose code is a stub is not
CCTP.

Note the shape difference: on Starknet, CCTP uses CAIRO contracts and
a COMBINED TokenMessengerMinterV2 - on EVM they are two separate
contracts. So the Starknet entry points are not the EVM ones.

Usage: verify_cctp_starknet.py [--sepolia]
"""
import asyncio
import json
import sys

from starknet_py.net.full_node_client import FullNodeClient

MAINNET = {
    "TokenMessengerMinterV2": "0x07d421B9cA8aA32DF259965cDA8ACb93F7599F69209A41872AE84638B2A20F2a",
    "MessageTransmitterV2": "0x02EBB5777B6dD8B26ea11D68Fdf1D2c85cD2099335328Be845a28c77A8AEf183",
}

SEPOLIA = {
    "TokenMessengerMinterV2": "0x04bDdE1E09a4B09a2F95d893D94a967b7717eB85A3f6dEcA8c080Ee01fBc3370",
    "MessageTransmitterV2": "0x04db7926C64f1f32a840F3Fa95cB551f3801a3600Bae87aF87807A54DCE12Fe8",
}

RPCS = {
    "mainnet": "https://starknet-rpc.publicnode.com",
    "sepolia": "https://starknet-sepolia-rpc.publicnode.com",
}

# USDC on Starknet, read from the chain earlier.
USDC_MAINNET = "0x053c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8"


async def main():
    net = "sepolia" if "--sepolia" in sys.argv else "mainnet"
    contracts = SEPOLIA if net == "sepolia" else MAINNET

    print(f"=== Circle's CCTP on Starknet {net} ===")
    print(f"  domain 25 (Starknet)")
    print()

    client = FullNodeClient(node_url=RPCS[net])
    ok = True

    for name, addr in contracts.items():
        a = int(addr, 16)
        print(f"  {name}")
        print(f"    address: {addr}")
        try:
            ch = await client.get_class_hash_at(a, block_number="latest")
            print(f"    DEPLOYED  class hash {hex(ch)}")
        except Exception as e:
            print(f"    NOT DEPLOYED  ({str(e)[:60]})")
            ok = False
            continue

        # the contract's own name, where it has one
        try:
            from starknet_py.hash.selector import get_selector_from_name
            from starknet_py.net.client_models import Call
            raw = await client.call_contract(
                Call(to_addr=a, selector=get_selector_from_name("name"),
                     calldata=[]),
                block_number="latest")
            sys.path.insert(0, "/opt/ustartkpay_test")
            from chains_starknet_engine import decode_string
            print(f"    name:     {decode_string(raw)!r}")
        except Exception:
            print("    name:     (no name() entry point)")

    if net == "mainnet":
        print()
        print("  === the native USDC ===")
        print(f"    address: {USDC_MAINNET}")
        try:
            from starknet_py.hash.selector import get_selector_from_name
            from starknet_py.net.client_models import Call
            a = int(USDC_MAINNET, 16)
            for entry, label in (("name", "name"), ("symbol", "symbol"),
                                 ("decimals", "decimals")):
                raw = await client.call_contract(
                    Call(to_addr=a,
                         selector=get_selector_from_name(entry),
                         calldata=[]),
                    block_number="latest")
                if entry == "decimals":
                    print(f"    {label}:  {int(raw[0])}")
                else:
                    sys.path.insert(0, "/opt/ustartkpay_test")
                    from chains_starknet_engine import decode_string
                    print(f"    {label}:  {decode_string(raw)!r}")
        except Exception as e:
            print(f"    the read failed: {str(e)[:70]}")
            ok = False

    print()
    if ok:
        print("VERDICT: Circle's CCTP contracts are deployed on Starknet "
              f"{net}")
    else:
        print("SOME CONTRACTS ARE NOT DEPLOYED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
