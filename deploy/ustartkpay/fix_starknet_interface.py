#!/usr/bin/env python3
"""Give the Starknet engine the interface methods it was missing.

`class StarknetEngine:` inherits from nothing, so it does not get
ChainEngine's methods. One of them broke a public endpoint:

    AttributeError: 'StarknetEngine' object has no attribute 'explorer'.
    Did you mean: '_explorer'?

so /bridge/chains answered 500 on the Starknet deployment while
ubotpay and usonicpay answered 200 - because their engines inherit
from ChainEngine and Starknet's does not.

Six were missing:

    explorer                <- the one that broke /bridge/chains
    send                    <- submit then confirm
    account
    sign_authorization      <- EIP-3009, EVM-only
    verify_authorization    <- EIP-3009, EVM-only
    settle_authorization    <- EIP-3009, EVM-only

The x402 ones raise NotSupported, exactly as the base class does, and
with the same reason: transferWithAuthorization is an EVM-only
primitive and Starknet is not EVM. Saying so plainly is better than
an AttributeError.

`explorer()` returns the explorer base URL, the same as the base
class's implementation.

Usage: fix_starknet_interface.py <starknet.py>
"""
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/data/workspace/ubotpay/deploy/ustartkpay/chains_starknet_engine.py"

ANCHOR = """    def has_signer(self) -> bool:"""

NEW = '''    # ── the ChainEngine interface ───────────────────────────────────
    # This engine inherits from nothing, so it does not get
    # ChainEngine's methods. Six of them were missing, and one broke
    # a public endpoint: /bridge/chains answered 500 here with
    #
    #     AttributeError: 'StarknetEngine' object has no attribute
    #     'explorer'. Did you mean: '_explorer'?
    #
    # while ubotpay and usonicpay answered 200 - because their
    # engines inherit from ChainEngine and this one does not.
    #
    # So the surface is provided here explicitly. The x402 methods
    # raise NotSupported with the same reason the base class uses:
    # transferWithAuthorization is an EVM-only primitive, and Starknet
    # is not EVM. Saying that plainly beats an AttributeError.

    def explorer(self) -> str:
        """The block explorer's base URL."""
        return getattr(self, "_explorer", "")

    def account(self):
        """The account object, if this deployment has a signer.

        There is no account object until the account contract is
        deployed, so this is None before that and the KeyPair's
        address after it.
        """
        return getattr(self, "_account_address", "") or None

    def send(self, to: str, amount, memo_hash: str | None = None,
             fee: float = 0.0, symbol: str | None = None) -> dict:
        """Submit then confirm, which is what the base class does.

        `symbol` says which token to pay in. It travels all the way
        from the API to the engine here too: without it every payment
        would use the wrong fee token.
        """
        sub = self.submit(to, amount, fee)
        if not sub.get("ok"):
            return sub
        return self.confirm(sub["tx"], sub.get("fee_tx"))

    def sign_authorization(self, *a, **k) -> dict:
        raise NotSupported(
            f"{self.name} has no transferWithAuthorization; x402 permits are "
            f"an EVM-only primitive."
        )

    def verify_authorization(self, auth: dict, *, expected_payee=None,
                             min_value=None):
        if not self.supports_x402:
            return False, f"unsupported on {self.name}"
        return False, "not implemented"

    def settle_authorization(self, auth: dict) -> dict:
        return {"ok": False, "tx": None, "error": f"unsupported on {self.name}",
                "reason": f"{self.name} cannot execute an EIP-3009 authorization",
                "explorer": None, "latency_s": 0.0}

    def has_signer(self) -> bool:'''


def main():
    src = open(PATH, encoding="utf-8").read()

    if "def explorer(self) -> str:" in src:
        print("  the interface methods are already there")
        return

    if ANCHOR not in src:
        print("  the anchor was not found")
        sys.exit(1)

    src = src.replace(ANCHOR, NEW, 1)

    # NotSupported has to be available in this module. The engine is
    # a STANDALONE file - it is copied into chains/ as starknet.py -
    # so it cannot do a relative import of the base class. The
    # exception is defined locally, exactly as the other engines'
    # messages describe it.
    if "class NotSupported" not in src:
        anchor = 'log = logging.getLogger("ustartpay.starknet")'
        if anchor in src:
            src = src.replace(
                anchor,
                anchor + '''


class NotSupported(RuntimeError):
    """A chain cannot do what was asked of it.

    The same name as the base class's, so the messages read the same
    whichever engine raised them.
    """''', 1)
            print("  the local NotSupported was added")
        else:
            print("  WARNING: could not add NotSupported")

    open(PATH, "w", encoding="utf-8").write(src)

    try:
        compile(src, PATH, "exec")
        print(f"{PATH}: the interface methods were added, it parses")
    except SyntaxError as e:
        print(f"{PATH}: it does NOT parse: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
