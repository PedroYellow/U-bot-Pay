#!/usr/bin/env python3
"""Point every default chain id in the Tron api.py at the profile's.

The Tron deployment's api.py came from U-bot Pay, and it carried
Avalanche's chain id (43114) in several places. On Utronpay that
chain is not in the profile at all, so a request with the default
chain id raised:

    chains.engine.NotSupported: chain 43114 is not in this profile

One of those places had already been changed - from 43114 to 0,
because TRON's chain id was believed to be 0. It is not. The profile
says:

    chain_id: 728126428

and that is what `chain.CHAIN_ID` reports, and what the registry
indexes its engines by. The 0 came from LI.FI's era, where the Tron
chain id was 0.

So every default becomes the profile's own chain id, read from the
profile rather than hardcoded - because hardcoding it is how the
Avalanche value got here in the first place.

The three places:

    resolve_user(user_key, chain_id=43114)
    WalletNonceRequest.chain_id = 43114
    WalletVerifyRequest.chain_id = 43114

The last two are the wallet-auth payloads. A browser signing a nonce
for Tron sends its own chain id, so the default only matters when it
does not send one - and then it should be Tron's, not Avalanche's.

Usage: fix_tron_chain_id_defaults.py <api.py> [profile]
"""
import re
import shutil
import sys


def profile_chain_id(path):
    m = re.search(r"^\s*chain_id:\s*(\d+)", open(path).read(), re.M)
    return int(m.group(1)) if m else None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/opt/utronpay/api.py"
    profile = sys.argv[2] if len(sys.argv) > 2 else \
        "/opt/utronpay/profiles/utronpay.yaml"

    chain_id = profile_chain_id(profile)
    if not chain_id:
        print("  the profile's chain id could not be read")
        return 1
    print(f"  the profile's chain id: {chain_id}")

    with open(path, encoding="utf-8") as f:
        src = f.read()

    before = src

    # the wallet-auth payloads and resolve_user
    src = src.replace("chain_id: int = 43114", "chain_id: int = %d" % chain_id)
    src = src.replace("chain_id: int = 0,", "chain_id: int = %d," % chain_id)

    # the balance model: ge=0 was there because TRON's chain id was
    # believed to be 0
    src = re.sub(
        r"chain_id: int = Field\(default=0, ge=0,",
        "chain_id: int = Field(default=%d, ge=0," % chain_id,
        src)

    if src == before:
        print("  nothing to change")
        return 0

    # the comments around them said TRON's chain id is 0, which is
    # what this change corrects
    src = src.replace(
        "# The default is this deployment's chain, TRON mainnet (0).",
        "# The default is this deployment's chain, TRON mainnet.")
    src = src.replace(
        "# ge=0, because TRON's chain id is 0 and ge=1 rejected it.",
        "# The lower bound stays 0 so a caller can still ask for chain 0.")
    src = src.replace(
        'description="chain to read balances from; default TRON mainnet (0)")',
        'description="chain to read balances from; default TRON mainnet")')
    src = src.replace(
        "# The default is this deployment's chain, TRON mainnet (0).",
        "# The default is this deployment's chain, TRON mainnet.")

    try:
        compile(src, path, "exec")
    except SyntaxError as e:
        print(f"  it would NOT parse: {e}")
        return 1

    shutil.copy2(path, path + ".bak-chainid")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"  {path}: every default chain id is now {chain_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
