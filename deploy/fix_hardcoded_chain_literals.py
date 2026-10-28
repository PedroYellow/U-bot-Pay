#!/usr/bin/env python3
"""Replace the hardcoded chain-id literals with the deployment's own.

The Tron deployment had AVALANCHE's chain id in two places:

    def resolve_user(user_key: str | None, chain_id: int = 43114) -> dict:
    class WalletVerifyRequest(BaseModel):
        chain_id: int = 43114

43114 is Avalanche. On a Tron deployment a request that omitted the
chain id was therefore resolved against the WRONG chain - the user row
was looked up on Avalanche, and the wallet verification signed over
Avalanche's chain id while the deployment settles on Tron.

That is the same class of bug as the deployment reporting chain_id 0:
a chain id that is a literal instead of a fact about the deployment.

The fix is the same shape as the balance route's default: the value
comes from chain.CHAIN_ID, which resolves through the registry to the
engine - and the engine now takes it from the profile.

The default is evaluated per request, so the value is current.

Usage: fix_hardcoded_chain_literals.py <api.py>
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/api.py"

src = open(path, encoding="utf-8").read()
n = 0

# ── a function parameter default ──────────────────────────────────
OLD1 = "def resolve_user(user_key: str | None, chain_id: int = 43114) -> dict:"
NEW1 = ("def resolve_user(user_key: str | None, chain_id: int | None = None) -> dict:\n"
        "    # The chain id is the deployment's own, not Avalanche's. It was\n"
        "    # 43114 - the main deployment's - so a request that omitted it was\n"
        "    # resolved against the wrong chain.\n"
        "    if chain_id is None:\n"
        "        chain_id = chain.CHAIN_ID")

if OLD1 in src:
    src = src.replace(OLD1, NEW1, 1)
    n += 1
else:
    print("  the resolve_user signature was not found")

# ── a pydantic model field default ────────────────────────────────
OLD2 = '''class WalletVerifyRequest(BaseModel):
    address: str
    chain_id: int = 43114
    signature: str     # 0x-prefixed 65-byte EIP-191 signature'''

NEW2 = '''class WalletVerifyRequest(BaseModel):
    address: str
    # The chain id is the deployment's own, not Avalanche's. It was
    # 43114 - the main deployment's - so a verification signed over
    # Avalanche's chain id while the deployment settles on Tron.
    chain_id: int | None = None
    signature: str     # 0x-prefixed 65-byte EIP-191 signature

    def resolved_chain_id(self) -> int:
        return self.chain_id if self.chain_id is not None else chain.CHAIN_ID'''

if OLD2 in src:
    src = src.replace(OLD2, NEW2, 1)
    n += 1
else:
    print("  the WalletVerifyRequest model was not found")

open(path, "w", encoding="utf-8").write(src)

# any literal left?
left = re.findall(r"chain_id\s*:\s*int\s*=\s*43114", src)
if left:
    print(f"  {len(left)} literal chain_id default(s) remain")

try:
    compile(src, path, "exec")
    print(f"{path}: {n} change(s), it parses")
except SyntaxError as e:
    print(f"{path}: {n} change(s) but it does NOT parse: {e}")
