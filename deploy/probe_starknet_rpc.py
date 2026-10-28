#!/usr/bin/env python3
"""Probe the Starknet public RPCs from wherever this runs.

The first list tried - blastapi - is dead: it answers 403 with
"Blast API is no longer available. Please update your integration".
Lava and 1rpc answer 410 with the same message. Those endpoints are
still quoted in half the guides online.

The ones that work:
    https://starknet.api.onfinality.io/public
    https://starknet-rpc.publicnode.com
    https://starknet-mainnet-rpc.itrocket.net

Chain id on mainnet is 0x534e5f4d41494e, which is "SN_MAIN" in ASCII.
"""
import json
import sys
import urllib.error
import urllib.request

URLS = [
    "https://starknet.api.onfinality.io/public",
    "https://starknet-rpc.publicnode.com",
    "https://starknet-mainnet-rpc.itrocket.net",
    "https://starknet.drpc.org",
    "https://public.1rpc.io/starknet",
    "https://starknet-mainnet.public.blastapi.io/rpc/v0_8",
]


def rpc(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "method": method,
                       "params": params, "id": 1}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "curl/8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        if "error" in d:
            return None, str(d["error"].get("message", ""))[:70]
        return d.get("result"), None
    except urllib.error.HTTPError as e:
        body = e.read()[:140].decode("utf-8", "replace")
        return None, f"HTTP {e.code} {body}"
    except Exception as e:
        return None, f"{type(e).__name__} {str(e)[:60]}"


def main():
    print("=== chain id ===")
    working = []
    for u in URLS:
        cid, err = rpc(u, "starknet_chainId", [])
        if cid:
            ascii_cid = bytes.fromhex(cid[2:]).decode("ascii", "replace")
            print(f"  OK   {u[:52]:54s} -> {cid} ({ascii_cid})")
            working.append(u)
        else:
            print(f"  --   {u[:52]:54s} -> {err}")

    if not working:
        print("\nno working RPC")
        return 1

    print("\n=== block number ===")
    for u in working:
        blk, err = rpc(u, "starknet_blockNumber", [])
        print(f"  {u[:52]:54s} -> {blk if blk else err}")

    print("\n=== the fee token (STRK) ===")
    # STRK on mainnet is a well-known constant. Read its balance for an
    # address that holds a lot of it, to prove the address is right.
    STRK = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"
    for u in working:
        bal, err = rpc(u, "starknet_getStorageAt", [
            STRK, "0x2fd23d9182193775423497fc0c472e156c57c69e4089a1967fb288a2d84e914",
            "0x033b2e3f9d8f8b4c1e4b3f2c1a5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8"])
        print(f"  {u[:52]:54s} -> {str(bal)[:70] if bal else err}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
