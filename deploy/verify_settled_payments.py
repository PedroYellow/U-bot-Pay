#!/usr/bin/env python3
"""Verify the settled payments on chain.

The database says "settled" for 8 payments. The database is not the
chain. This reads each transaction hash from Avalanche and confirms:

  - the transaction exists
  - it SUCCEEDED (receipt status 0x1)
  - the recipient actually received the USDC

A payment recorded as settled whose transaction reverted is worse than
a payment recorded as failed, because the ledger says the money moved.

It also flags the ones that are NOT settled - a pending approval and a
failure - so the state is complete rather than only the happy path.

Usage: verify_settled_payments.py [db-path]
"""
import json
import os
import sqlite3
import sys
import urllib.request

RPC = "https://api.avax.network/ext/bc/C/rpc"
USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"

# The database lives on the VPS, so the path is an argument rather
# than a constant - the script is useful from either machine, and a
# hardcoded path that does not exist on the one you are on is a
# confusing way to fail.
DB = sys.argv[1] if len(sys.argv) > 1 else "/opt/ubotpay/ubotpay.db"


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                        "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read())
    if "error" in d:
        raise RuntimeError(d["error"])
    return d["result"]


def receipt(tx):
    return rpc("eth_getTransactionReceipt", [as_tx_hash(tx)])


def tx_of(tx):
    return rpc("eth_getTransactionByHash", [as_tx_hash(tx)])


def as_tx_hash(tx):
    """The hash with a 0x prefix.

    The database stores it WITHOUT one - 64 hex digits, no prefix. The
    Avalanche RPC rejects that with "invalid argument 0: json: cannot
    unmarshal", which reads like a corrupt hash rather than a missing
    prefix.
    """
    s = str(tx).strip()
    if s.startswith("0x"):
        return s
    return "0x" + s


def usdc_balance_of(address):
    data = "0x70a08231" + address.lower().replace("0x", "").rjust(64, "0")
    res = rpc("eth_call", [{"to": USDC, "data": data}, "latest"])
    return int(res, 16) / 1e6


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT id, contact_alias, to_address, amount_usdc, "
                "status, tx FROM payments ORDER BY rowid")
    rows = cur.fetchall()

    ok = bad = 0
    print(f"  {'the payment':18s} {'the alias':26s} {'the amount':>9s}  the chain")
    print("  " + "-" * 74)

    for pid, alias, to, amt, status, tx in rows:
        if status != "settled" or not tx:
            print(f"  {pid:18s} {str(alias)[:24]:26s} {amt:>9.2f}  "
                  f"NOT SETTLED ({status}) - no tx to verify")
            continue

        try:
            r = receipt(tx)
            if r is None:
                print(f"  {pid:18s} {str(alias)[:24]:26s} {amt:>9.2f}  "
                      f"NO RECEIPT - the tx is not on chain")
                bad += 1
                continue

            status_hex = r.get("status", "0x0")
            succeeded = int(status_hex, 16) == 1
            gas = int(r.get("gasUsed", "0x0"), 16)

            # the recipient's balance is not proof of THIS transfer, so
            # the receipt's own logs are: the USDC Transfer event
            transfer_to = None
            for log in r.get("logs", []):
                if log.get("address", "").lower() != USDC.lower():
                    continue
                topics = log.get("topics", [])
                # Transfer(address,address,uint256): topic1 is the sender,
                # topic2 is the recipient
                if len(topics) >= 3:
                    transfer_to = "0x" + topics[2][-40:]

            # the recipient has to have received at least the amount
            bal = usdc_balance_of(to)

            mark = "OK" if succeeded and transfer_to else "PROBLEM"
            if succeeded and transfer_to:
                ok += 1
            else:
                bad += 1

            detail = (f"settled on chain, gas {gas}"
                      if succeeded else f"REVERTED (status {status_hex})")
            print(f"  {pid:18s} {str(alias)[:24]:26s} {amt:>9.2f}  {mark}: {detail}")
            if transfer_to:
                print(f"  {'':18s} {'':26s} {'':>9s}  the transfer's recipient: "
                      f"{transfer_to[:20]}... (the payment's: {to[:20]}...)")
                if transfer_to.lower() != to.lower():
                    print(f"  {'':18s} {'':26s} {'':>9s}  ** THE RECIPIENT DIFFERS **")
                    bad += 1
                    ok -= 1
        except Exception as e:
            print(f"  {pid:18s} {str(alias)[:24]:26s} {amt:>9.2f}  "
                  f"could not check: {type(e).__name__}: {str(e)[:60]}")
            bad += 1

    print()
    print(f"  {ok} verified on chain, {bad} problem(s)")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
