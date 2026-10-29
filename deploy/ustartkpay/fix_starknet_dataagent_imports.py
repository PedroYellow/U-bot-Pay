#!/usr/bin/env python3
"""Make dataagent.py use the EVM stub instead of the real SDKs.

The change

    - from web3 import Web3
    - from eth_account import Account
    - from eth_account.messages import encode_typed_data
    + from evm_stub import Web3, Account, encode_typed_data

Why

On the Starknet deployment:

  - Web3 is used EXACTLY ONCE, for to_checksum_address
  - Account is imported and NEVER USED
  - encode_typed_data is imported and NEVER USED

So the module pulls in a whole EVM SDK to use one checksum helper -
and that SDK cannot be installed beside starknet-py:

    starknet-py 0.30.0  ->  eth-keyfile <1.0.0, >=0.8.1
    eth-account 0.14.0  ->  eth-keyfile >=0.10

Incompatible. pip resolves to eth-keyfile 0.9.1, eth-account breaks,
and pip exits 120. That is the failure that stopped the deployment.

The stub does the same thing Solana's web3_stub.py already does: it
provides the names with the behaviour the caller needs, and the
EIP-55 checksum is skipped because keccak-256 is not in this venv.
That is correct rather than a shortcut - the value goes on to
chain.is_address, which is the engine's own validator and the one
that decides whether a payment goes anywhere.

What it does NOT change

The behaviour of the module. to_checksum_address returns the same
lowercase hex it would have returned without the EIP-55 checksum
mixed-case. On a non-EVM chain the checksum is meaningless anyway.

Usage: fix_starknet_dataagent_imports.py <dataagent.py>
"""
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/opt/ustartkpay/dataagent.py"

OLD = """from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_typed_data"""

NEW = """# The EVM helpers, as a STUB. This deployment is not EVM.
#
# The real SDKs cannot be installed beside starknet-py: starknet-py
# wants eth-keyfile<1.0.0 and eth-account wants eth-keyfile>=0.10,
# which are incompatible, and pip resolves to a version that breaks
# eth-account. So web3 and eth_account are NOT in this venv.
#
# Only Web3.to_checksum_address is used here, and the stub's version
# of it is correct for a non-EVM chain - the value goes on to
# chain.is_address, which is the engine's own validator.
#
# Account and encode_typed_data are imported and never used; the stub
# raises if either is ever called, so a future use fails loudly at the
# point of use rather than silently.
from evm_stub import Web3, Account, encode_typed_data"""


def main():
    src = open(PATH, encoding="utf-8").read()

    if OLD not in src:
        if "from evm_stub import" in src:
            print("  it already uses the stub")
            return
        print("  the import block was not found")
        sys.exit(1)

    src = src.replace(OLD, NEW, 1)
    open(PATH, "w", encoding="utf-8").write(src)

    try:
        compile(src, PATH, "exec")
        print(f"{PATH}: the stub is in place, it parses")
    except SyntaxError as e:
        print(f"{PATH}: it does NOT parse: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
