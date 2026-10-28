#!/usr/bin/env python3
"""Finish the chain-id literal cleanup on the Tron deployment.

Two pydantic models carried AVALANCHE's chain id:

    class WalletNonceRequest(BaseModel):
        chain_id: int = 43114

    class WalletVerifyRequest(BaseModel):
        chain_id: int = 43114

43114 is the main deployment's chain. On a Tron deployment a request
that omitted the chain id was resolved against Avalanche: the nonce
was minted for one chain and the user was looked up on another.

The value now comes from chain.CHAIN_ID, which resolves through the
registry to the engine, and the engine takes it from the profile. So
it is a fact about the deployment rather than a literal in a model.

A pydantic field default is evaluated at class-definition time, so
the default is None and the value is resolved at request time - the
current value, not the one at import.

Usage: fix_wallet_chain_models.py <api.py>
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/api.py"

src = open(path, encoding="utf-8").read()
n = 0

# ── the two models ────────────────────────────────────────────────
OLD_FIELD = '''    chain_id: int = 43114
    signature: str     # 0x-prefixed 65-byte EIP-191 signature'''

NEW_FIELD = '''    # The chain id is the deployment's own, not Avalanche's. It was
    # 43114 - the main deployment's - so a request that omitted it was
    # resolved against the wrong chain. A pydantic default is
    # evaluated at class-definition time, so it is None here and the
    # value is resolved at request time.
    chain_id: int | None = None
    signature: str     # 0x-prefixed 65-byte EIP-191 signature'''

if OLD_FIELD in src:
    src = src.replace(OLD_FIELD, NEW_FIELD, 1)
    n += 1

# the nonce request model
OLD_NONCE = '''    chain_id: int = 43114
    address: str'''

if OLD_NONCE in src:
    src = src.replace(OLD_NONCE, '''    # The deployment's own chain id, not Avalanche's - see above.
    chain_id: int | None = None
    address: str''', 1)
    n += 1

# any remaining literal in a model
src = re.sub(r'(# The deployment\'s own chain id[^\n]*\n\s*)chain_id: int = 43114',
             r'\1chain_id: int | None = None', src)

# ── the uses ──────────────────────────────────────────────────────
OLD_USE1 = '''    nonce = core.new_wallet_nonce(addr_key, req.chain_id)
    return {"nonce": nonce, "expires_in": 600, "chain_id": req.chain_id}'''

NEW_USE1 = '''    # The chain id resolves to the deployment's own when the caller
    # omitted it.
    _chain_id = req.chain_id if req.chain_id is not None else chain.CHAIN_ID
    nonce = core.new_wallet_nonce(addr_key, _chain_id)
    return {"nonce": nonce, "expires_in": 600, "chain_id": _chain_id}'''

if OLD_USE1 in src:
    src = src.replace(OLD_USE1, NEW_USE1, 1)
    n += 1

OLD_USE2 = '''    cur = core.get_user_by_wallet(req.address, req.chain_id)'''

NEW_USE2 = '''    # The chain id resolves to the deployment's own when the caller
    # omitted it - so a verification is never resolved against
    # Avalanche on a Tron deployment.
    _chain_id = req.chain_id if req.chain_id is not None else chain.CHAIN_ID
    cur = core.get_user_by_wallet(req.address, _chain_id)'''

if OLD_USE2 in src:
    src = src.replace(OLD_USE2, NEW_USE2, 1)
    n += 1

open(path, "w", encoding="utf-8").write(src)

left = re.findall(r"chain_id\s*:\s*int\s*=\s*43114", src)
if left:
    print(f"  {len(left)} literal chain_id default(s) remain")
    for m in re.finditer(r"chain_id\s*:\s*int\s*=\s*43114", src):
        line = src[:m.start()].count("\n") + 1
        print(f"    line {line}")

try:
    compile(src, path, "exec")
    print(f"{path}: {n} change(s), it parses")
except SyntaxError as e:
    print(f"{path}: {n} change(s) but it does NOT parse: {e}")
