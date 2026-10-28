#!/usr/bin/env python3
"""Fund a Starknet Sepolia address through the Foundation's public agent API.

The faucet has an API for scripts and agents, with no auth. It is gated
by a proof of work instead, which is solved locally:

    POST /api/public-agent/pow/challenge
      { "userAddress": "0x..." }
    -> { data: { challengeId, powInputPrefix, difficulty } }

    find a nonce so that sha256(powInputPrefix + nonce) has `difficulty`
    leading zero BITS

    POST /api/public-agent/faucet/request
      { "userAddress": "0x...", "challengeId": "<id>", "nonce": "<nonce>" }
    -> { data: { requestId, pollAfterSeconds } }

    GET /api/public-agent/faucet/status/<requestId>
    -> { data: { jobStatus, txHash, pollAfterSeconds } }

The bit-level check matters: the difficulty is a number of bits, not a
number of hex zeros. A whole-hex-zeros check solves the wrong problem
and the request is rejected.

This funds SEPOLIA - it is the testnet. A mainnet treasury needs real
STRK, which has to come from StarkGate or the Foundation.

Usage:
    python3 fund_starknet_sepolia.py <address>
    python3 fund_starknet_sepolia.py <address> --json
"""
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.faucet.starknet.io"


def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "curl/8"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {str(e)[:100]}"}


def get(path):
    req = urllib.request.Request(BASE + path,
                                 headers={"User-Agent": "curl/8"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {str(e)[:100]}"}


def leading_zero_bits(data: bytes) -> int:
    """Count the leading zero bits of a digest."""
    n = 0
    for byte in data:
        if byte == 0:
            n += 8
            continue
        # count the zeros within this byte
        n += 8 - byte.bit_length()
        break
    return n


def solve_pow(prefix: str, difficulty: int):
    """Find a nonce so sha256(prefix + nonce) has `difficulty` leading zero bits.

    The difficulty is in BITS. A whole-hex-zeros check solves a
    different, easier problem, and the faucet rejects the answer.
    """
    target = difficulty
    nonce = 0
    while True:
        h = hashlib.sha256((prefix + str(nonce)).encode()).digest()
        if leading_zero_bits(h) >= target:
            return nonce, leading_zero_bits(h)
        nonce += 1
        if nonce % 500_000 == 0:
            print(f"    ... {nonce} nonces tried", flush=True)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    address = sys.argv[1].strip()
    as_json = "--json" in sys.argv

    # A Starknet address is 63 hex digits after the 0x - the field is
    # 251 bits, not 256 like an EVM address. Checking for 66 (the EVM
    # length) rejects every valid Starknet address.
    body = address[2:] if address.startswith("0x") else ""
    if not (address.startswith("0x") and len(body) == 63
            and all(c in "0123456789abcdefABCDEF" for c in body)):
        print(f"  {address!r} does not look like a Starknet address")
        return 2

    print(f"=== funding {address} ===")

    status, body = post("/api/public-agent/pow/challenge",
                        {"userAddress": address})
    print(f"  challenge: HTTP {status}")
    # The endpoint answers 201 Created for a new challenge. Accepting
    # only 200 made the script bail before it ever solved the proof of
    # work, and the failure looked like a crash rather than a check.
    if status not in (200, 201):
        print(f"  {json.dumps(body)[:300]}")
        return 1

    data = body.get("data", {})
    challenge_id = data.get("challengeId")
    prefix = data.get("powInputPrefix")
    difficulty = data.get("difficulty")
    print(f"  challengeId: {challenge_id}")
    print(f"  difficulty:  {difficulty} leading zero bits")

    if not challenge_id or prefix is None or difficulty is None:
        print("  the challenge response was not what was expected")
        return 1

    print("  solving the proof of work...")
    t0 = time.time()
    nonce, bits = solve_pow(prefix, int(difficulty))
    print(f"  solved in {time.time() - t0:.1f}s: nonce={nonce} ({bits} bits)")

    status, body = post("/api/public-agent/faucet/request", {
        "userAddress": address,
        "challengeId": challenge_id,
        "nonce": str(nonce),
    })
    print(f"  request: HTTP {status}")
    if status not in (200, 201, 202):
        print(f"  {json.dumps(body)[:300]}")
        return 1

    data = body.get("data", {})
    request_id = data.get("requestId")
    wait = int(data.get("pollAfterSeconds") or 5)
    print(f"  requestId: {request_id}")

    for _ in range(40):
        time.sleep(wait)
        status, body = get(f"/api/public-agent/faucet/status/{request_id}")
        data = body.get("data", {})
        job = data.get("jobStatus")
        wait = int(data.get("pollAfterSeconds") or wait or 5)
        print(f"    {job}...")
        if job in ("confirmed", "failed", "rejected", "expired"):
            tx = data.get("txHash")
            if job == "confirmed":
                print(f"  confirmed: {tx}")
                if as_json:
                    print(json.dumps({"ok": True, "txHash": tx,
                                      "address": address}, indent=2))
                return 0
            print(f"  {job}: {json.dumps(body)[:300]}")
            return 1

    print("  timed out waiting for the faucet")
    return 1


if __name__ == "__main__":
    sys.exit(main())
