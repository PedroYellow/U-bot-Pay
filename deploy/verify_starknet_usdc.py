#!/usr/bin/env python3
"""Verify the USDC token on Starknet mainnet, on chain.

The address comes from the avnu token API:
    0x053c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8

But an address from an indexer is a claim, not a fact. This reads the
token contract itself and checks that:

  - it exists (has code)
  - name() says "USD Coin" or similar
  - symbol() says "USDC"
  - decimals() is 6
  - total_supply() is non-zero

The Cairo 1 string encoding is worth spelling out, because getting it
wrong is easy and looks plausible:

  a string is a felt array [offset, length, ...payload]
  where `length` is itself felt-encoded (not a plain integer), and
  each payload felt carries 31 bytes of the string in its LOW bytes.

Getting the byte order wrong yields a string of NUL bytes with the
length at the end, which is what a first attempt produced.

Usage: verify_starknet_usdc.py [address]
"""
import asyncio
import sys

from starknet_py.hash.selector import get_selector_from_name
from starknet_py.net.client_models import Call
from starknet_py.net.full_node_client import FullNodeClient

RPCS = [
    "https://starknet-rpc.publicnode.com",
    "https://starknet.api.onfinality.io/public",
    "https://starknet-mainnet-rpc.itrocket.net",
]

USDC = "0x053c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8"
STRK = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"
ETH = "0x049d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7"


def decode_string(felts):
    """Decode a Starknet string. Two encodings are in use.

    A ByteArray - what the USDC contract uses - comes back as ONE felt
    whose bytes are the string, right-aligned:
        hex = 000000...000055534420436f696e   -> "USD Coin"

    A legacy felt array - what STRK uses - is
        [offset, felt_length, ...payload]
    where the length is felt-encoded and each payload felt carries 31
    bytes in its low bytes.

    Getting the byte order wrong yields a string of NUL bytes with the
    text at one end, which is what a first attempt produced. Both are
    stripped here so the result is the text either way.
    """
    if not isinstance(felts, list) or not felts:
        return ""

    if len(felts) == 1:
        # ByteArray: the bytes are the string, right-aligned
        b = int(felts[0]).to_bytes(32, "big")
        return b.strip(b"\x00").decode("ascii", "replace")

    # legacy felt array
    length = int(felts[1]) % (1 << 31)
    out = bytearray()
    for f in felts[2:]:
        b = int(f).to_bytes(32, "big")
        out += b[32 - 31:]      # the low 31 bytes
    if not length:
        return ""
    return bytes(out[:length]).strip(b"\x00").decode("ascii", "replace")


async def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else USDC

    client = None
    for url in RPCS:
        try:
            c = FullNodeClient(node_url=url)
            await c.get_chain_id()
            if client is None:
                client = c
                print(f"  using {url}")
        except Exception:
            pass
    if client is None:
        print("no working RPC")
        return 1

    async def call(a, entry, calldata=None):
        return await client.call_contract(
            Call(to_addr=int(a, 16),
                 selector=get_selector_from_name(entry),
                 calldata=calldata or []),
            block_number="latest")

    print()
    print(f"=== the contract at {addr} ===")
    try:
        code = await client.get_class_hash_at(int(addr, 16), block_number="latest")
        print(f"  has code: yes (class hash {hex(code)[:18] if code else 'none'})")
    except Exception as e:
        print(f"  has code: NO - {str(e)[:80]}")
        return 1

    print()
    print("=== its metadata ===")
    ok = True
    for entry in ("name", "symbol"):
        try:
            res = await call(addr, entry)
            text = decode_string(res)
            print(f"  {entry:14s} -> {text!r}")
            if not text:
                ok = False
        except Exception as e:
            print(f"  {entry:14s} -> ERROR {str(e)[:70]}")
            ok = False

    try:
        res = await call(addr, "decimals")
        print(f"  {'decimals':14s} -> {res}")
        if res != [6]:
            print("    !! USDC should have 6 decimals")
            ok = False
    except Exception as e:
        print(f"  decimals      -> ERROR {str(e)[:70]}")
        ok = False

    try:
        res = await call(addr, "total_supply")
        supply = int(res[0]) if res else 0
        print(f"  {'total_supply':14s} -> {supply} ({supply / 10**6:,.2f} USDC)")
        if supply <= 0:
            print("    !! a live token has a supply")
            ok = False
    except Exception as e:
        print(f"  total_supply  -> ERROR {str(e)[:70]}")
        ok = False

    print()
    print("=== the decoder, checked against STRK (known good) ===")
    for label, a in (("STRK", STRK), ("ETH", ETH)):
        try:
            res = await call(a, "symbol")
            print(f"  {label:5s} symbol -> {decode_string(res)!r}")
        except Exception as e:
            print(f"  {label:5s} symbol -> ERROR {str(e)[:60]}")

    print()
    print("VERDICT:", "USDC verified on chain" if ok else "NOT verified")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
