#!/usr/bin/env python3
"""Give the tx-status fallback a client with a long enough timeout.

getSignatureStatuses with searchTransactionHistory=True walks the
node's history, and on the public mainnet endpoint that takes longer
than the default httpx timeout. The call raised:

    SolanaRpcException <class 'httpx2.ReadTimeout'> raised in
    "GetSignatureStatuses" endpoint request

and the fallback returned "pending" - the same answer the bug had.

So the fallback builds its own client with a longer timeout. The
cached client is not reused: it belongs to the current event loop and
to the failover pool's endpoint choice, and this call wants the
primary endpoint with a longer deadline.

Usage: fix_tx_status_timeout.py <solana.py>
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/usolpay/chains/solana.py"

src = open(path, encoding="utf-8").read()

OLD = '''                try:
                    st = await c.get_signature_statuses(
                        [signature], search_transaction_history=True)'''

NEW = '''                try:
                    # The history scan is slow, and the default client
                    # timeout is not enough for it - the call raised a
                    # ReadTimeout and the fallback answered "pending",
                    # which is the bug it was meant to fix. So this
                    # call gets its own client with a longer deadline.
                    #
                    # It is a separate client on purpose: the cached
                    # one belongs to the current event loop and to the
                    # failover pool's endpoint choice, and this wants
                    # the primary endpoint with more time.
                    _slow = _sol["AsyncClient"](self._rpc_url, timeout=45)
                    try:
                        st = await _slow.get_signature_statuses(
                            [signature], search_transaction_history=True)
                    finally:
                        try:
                            await _slow.close()
                        except Exception:
                            pass'''

if OLD not in src:
    print("  the fallback was not found")
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
open(path, "w", encoding="utf-8").write(src)
print(f"{path}: the fallback gets a client with a 45s timeout")
