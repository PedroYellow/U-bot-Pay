#!/usr/bin/env python3
"""Fall back to getSignatureStatuses when getTransaction answers null.

The Solana engine read a transaction's status with getTransaction:

    r = await c.get_transaction(signature, ...)
    if getattr(r, "value", None) is None:
        return {"status": "pending", ...}

The comment says why getTransaction was chosen: "the public endpoint
answers null for a signature that IS on chain (it only keeps recent
statuses in memory), which would make every settled payment read as
pending forever."

But that is only half true. getTransaction drops OLD transactions,
and getSignatureStatuses answers null for RECENT ones it has not
finalised yet. So a payment from a while ago read as pending forever,
which is the opposite failure.

getSignatureStatuses with searchTransactionHistory=True finds the old
one - it asks the node to look through its history rather than only
its recent cache. Verified against the public mainnet RPC:

    getSignatureStatuses -> confirmationStatus "finalized", err null

So the lookup tries getTransaction first (it carries the error detail
and the slot), and falls back to getSignatureStatuses when it comes
back empty.

Usage: fix_solana_tx_status.py <solana.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usolpay/chains/solana.py"

src = open(path, encoding="utf-8").read()

OLD = '''            try:
                r = await c.get_transaction(
                    signature, max_supported_transaction_version=0)
            except Exception:
                return {"status": "pending", "error": None, "slot": None}
            tx = getattr(r, "value", None)
            if tx is None:
                return {"status": "pending", "error": None, "slot": None}'''

NEW = '''            try:
                r = await c.get_transaction(
                    signature, max_supported_transaction_version=0)
            except Exception:
                r = None
            tx = getattr(r, "value", None) if r is not None else None
            if tx is None:
                # getTransaction answers null for a transaction the node
                # has aged out of its cache. getSignatureStatuses with
                # searchTransactionHistory looks through the history
                # instead, and finds it.
                #
                # The two fail in opposite directions: getTransaction
                # drops OLD transactions and getSignatureStatuses
                # answers null for RECENT ones it has not finalised.
                # So a settled payment from a while ago read as pending
                # forever.
                try:
                    st = await c.get_signature_statuses(
                        [signature], search_transaction_history=True)
                    entries = getattr(st, "value", None) or []
                    if entries:
                        e = entries[0]
                        conf = getattr(e, "confirmation_status", None)
                        err = getattr(e, "err", None)
                        if conf is not None:
                            return {
                                "status": "failed" if err is not None
                                          else "confirmed",
                                "error": str(err) if err is not None else None,
                                "slot": getattr(e, "slot", None),
                            }
                except Exception:
                    pass
                return {"status": "pending", "error": None, "slot": None}'''

if OLD not in src:
    print("  the tx_status block was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: tx_status now falls back to getSignatureStatuses")
