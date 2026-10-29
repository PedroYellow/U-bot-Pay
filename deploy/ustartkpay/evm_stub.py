"""
The EVM helpers dataagent.py needs, for a deployment that is not EVM.

The problem

Starknet, Solana and Tron are not EVM. dataagent.py was carried over
from U-bot Pay and imports three EVM things at module level:

    from web3 import Web3
    from eth_account import Account
    from eth_account.messages import encode_typed_data

On this deployment:

  - Web3 is used EXACTLY ONCE, on line 24, for
    to_checksum_address(DATAAGENT_ADDRESS)
  - Account is imported and NEVER USED
  - encode_typed_data is imported and NEVER USED

So the module imports a whole EVM SDK to use one checksum helper, and
that SDK is not installable here:

    starknet-py 0.30.0  ->  eth-keyfile <1.0.0, >=0.8.1
    eth-account 0.14.0  ->  eth-keyfile >=0.10

Those are incompatible - 0.10 satisfies the first only if the ceiling
were >=0.10. So installing web3 alongside starknet-py resolves to
eth-keyfile 0.9.1, which breaks eth-account, and pip exits 120:

    eth-account 0.14.0 requires eth-keyfile>=0.10, but you have
    eth-keyfile 0.9.1 which is incompatible.

The two SDKs cannot share an interpreter. That is the same rule the
engine registry follows, and the same thing that broke U-bot Pay's
payments once before.

The fix

This stub. It provides the three names with the behaviour dataagent
actually needs, and nothing more.

to_checksum_address returns the lowercase hex WITHOUT the EIP-55
checksum, because EIP-55 needs keccak-256 which is not in this venv.
That is correct rather than a shortcut: the only caller passes the
value on to chain.is_address, which is the engine's own validator and
the one that decides whether a payment goes anywhere. On Starknet an
EVM-shaped address is refused by the Starknet engine, which is the
right outcome - the recipient is not a Starknet account.

Account and encode_typed_data raise if they are ever called. They are
not called on this deployment, and an import that raised at module
level would break the service. Raising at CALL time means a future
use fails loudly at the point of use rather than silently.
"""
import re

# A 0x-prefixed 20-byte hex string. The EVM shape, and the only shape
# this stub recognises.
_EVM_HEX = re.compile(r"^0x[0-9a-fA-F]{40}$")


class Web3Exception(Exception):
    """Present so `except Web3Exception` in carried-over code works."""


class Web3:
    """The EVM helpers dataagent.py needs, and nothing else."""

    @staticmethod
    def is_address(addr) -> bool:
        """Is this a well-formed EVM address?

        Not: "is this a valid recipient". On this deployment the answer
        to that is the engine's own validation - base58 for Solana, 63
        hex digits for Starknet. This only answers the narrower
        question api.py asks about its own input.
        """
        return bool(isinstance(addr, str) and _EVM_HEX.match(addr))

    @staticmethod
    def to_checksum_address(addr) -> str:
        """EIP-55 checksum. Only meaningful for an EVM address.

        EIP-55 needs keccak-256, which is not installed in this venv.
        Returning the bare lowercase hex is acceptable here: the only
        caller passes the value on to chain.is_address, which refuses
        anything that is not a Starknet address. Returning the address
        unchanged is therefore correct rather than a shortcut.
        """
        if not isinstance(addr, str):
            raise ValueError("address must be a string")
        s = addr.lower().replace("0x", "")
        if len(s) != 40:
            raise ValueError("address must be 40 hex chars (20 bytes)")
        return "0x" + s

    @staticmethod
    def from_wei(value, unit) -> float:
        """Not used on this deployment. Fails loudly if it ever is."""
        raise NotImplementedError(
            "this deployment is not EVM; there is no wei.")

    @staticmethod
    def to_wei(value, unit) -> int:
        """Not used on this deployment. Fails loudly if it ever is."""
        raise NotImplementedError(
            "this deployment is not EVM; there is no wei.")


class Account:
    """Not used on this deployment. Fails loudly if it ever is."""

    @staticmethod
    def from_key(*a, **k):
        raise NotImplementedError(
            "this deployment is not EVM; there is no private-key account.")


def encode_typed_data(*a, **k):
    """Not used on this deployment. Fails loudly if it ever is."""
    raise NotImplementedError(
        "this deployment is not EVM; there is no EIP-712 encoding.")
