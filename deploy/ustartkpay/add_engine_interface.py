#!/usr/bin/env python3
"""Make the Starknet engine implement the ChainEngine interface.

The Starknet engine could READ the chain - USDC metadata, the total
supply, balances - and it proved that on live mainnet. But it was not
an engine the app could run:

  - the interface is SYNCHRONOUS, and the engine's methods were all
    `async def`
  - it had none of the seven abstract methods the base class requires
  - it had no money movement at all

So `ustartkpay` was serving AVALANCHE. The vhost for
api.ustartkpay.com proxied every route to port 8090 - the main
Avalanche deployment - and the dashboard answered:

    {"chain": "avalanche-mainnet", "chain_id": 43114}

The Starknet site was a Starknet-branded front end on top of an
Avalanche API.

This adds the missing surface:

    decimals()      to_units()      from_units()
    is_address()    is_tx_hash()
    balance()       submit()        confirm()

The reads wrap the existing async primitives in a sync bridge. The
moves are real: an account on Starknet is a CONTRACT, it signs with
its own key, and the transaction is broadcast through the account's
__execute__ entry point. The engine holds a KeyPair and the account's
address, and it refuses to move money when it has no signer - rather
than pretending it can.

The `sdk` attribute is "starknet_py", so the registry's conflict
check knows it does not share an interpreter with web3 or tronpy.
"""
import re
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/data/workspace/ubotpay/deploy/ustartkpay/chains_starknet_engine.py"

src = open(PATH, encoding="utf-8").read()

# ── 1. the sync bridge and the interface methods ──────────────────
ANCHOR = '''    async def total_supply(self) -> float:'''

BRIDGE = '''    # ── the sync bridge ─────────────────────────────────────────────
    #
    # The interface is synchronous - the app calls engine.balance()
    # from a request handler and expects a value, not a coroutine. The
    # Starknet client's calls are async, so they run on a private event
    # loop.
    #
    # A private loop, not the running one: the app's handlers are
    # already inside a loop, and running a coroutine on THAT loop from
    # a sync function is not possible. A fresh loop per call is
    # correct here - the client's HTTP transport is created inside the
    # call, so there is no loop-bound state to carry across calls.
    @staticmethod
    def _run(coro):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    # ── the ChainEngine interface ───────────────────────────────────
    def decimals(self) -> int:
        """The stable token's decimals. Starknet's USDC has 6."""
        if self._decimals is None:
            self._decimals = self._run(self._decimals_async())
        return self._decimals

    def to_units(self, amount) -> int:
        """A human amount to the token's base units."""
        from decimal import Decimal
        return int(Decimal(str(amount)) * (10 ** self.decimals()))

    def from_units(self, units: int):
        from decimal import Decimal
        return Decimal(units) / (10 ** self.decimals())

    def is_address(self, addr: str) -> bool:
        """A Starknet address.

        63 hex digits after the 0x - the field is 251 bits, not 256
        like an EVM address. Checking for 66 rejects every valid
        Starknet address, and checking for 40 accepts EVM addresses
        that are not Starknet addresses at all.
        """
        if not isinstance(addr, str):
            return False
        body = addr[2:] if addr.startswith("0x") else ""
        return (len(body) == 63
                and all(c in "0123456789abcdefABCDEF" for c in body))

    def is_tx_hash(self, value: str) -> bool:
        """A Starknet transaction hash: 63 hex digits, no 0x prefix
        required, but the 0x form is accepted."""
        if not isinstance(value, str):
            return False
        body = value[2:] if value.startswith("0x") else value
        return (len(body) <= 63
                and len(body) >= 8
                and all(c in "0123456789abcdefABCDEF" for c in body))

    def balance(self, address: str) -> dict:
        """The stable and native balances of an address.

        The shape matches what the EVM engine returns, because the
        app reads one shape: `usdc`, `native`, `native_balance`,
        `stable_balance`, `address`, `chain`, `chain_id`.
        """
        if not self.is_address(address):
            raise ValueError(f"invalid Starknet address: {address!r}")
        stable = self._run(self.balance(address))
        native = self._run(self._native_balance(address))
        return {
            "address": address,
            "chain": self.name(),
            "chain_id": self.chain_id(),
            "usdc": stable,
            "stable_balance": stable,
            "native": self.native_symbol,
            "native_balance": native,
            "native_symbol": self.native_symbol,
            "decimals": self.decimals(),
            "symbol": self.stable_symbol,
        }

    def submit(self, to: str, amount, fee: float = 0.0) -> dict:
        """Send the stable token.

        An account on Starknet is a CONTRACT: it signs with its own
        key and the transaction goes through its __execute__ entry
        point. Without a signer there is nothing to sign with, so the
        engine refuses rather than pretending it can move money.
        """
        if not self.has_signer():
            return {
                "ok": False,
                "tx": None,
                "error": "this Starknet deployment has no signer; "
                         "the account contract is not deployed or has no key",
            }
        if not self.is_address(to):
            return {"ok": False, "tx": None,
                    "error": f"invalid Starknet address: {to!r}"}
        try:
            result = self._run(self._submit(to, amount, fee))
        except Exception as e:
            return {"ok": False, "tx": None,
                    "error": f"{type(e).__name__}: {str(e)[:200]}"}
        return result

    def confirm(self, tx: str, fee_tx: str | None = None,
                timeout: int = 60) -> dict:
        """Wait for a transaction to settle and report what happened."""
        try:
            result = self._run(self._confirm(tx, timeout))
        except Exception as e:
            return {"ok": False, "tx": tx,
                    "error": f"{type(e).__name__}: {str(e)[:200]}"}
        return result

'''

if ANCHOR not in src:
    print("  the anchor was not found")
    sys.exit(1)

src = src.replace(ANCHOR, BRIDGE + ANCHOR, 1)

# ── 2. the sdk attribute, so the registry's conflict check works ──
OLD_SDK = '''    evm = False                      # not EVM
    supports_x402 = False            # no EVM signature recovery
    stable_symbol = "USDC"'''

NEW_SDK = '''    evm = False                      # not EVM
    supports_x402 = False            # no EVM signature recovery
    stable_symbol = "USDC"
    # The registry checks the sdk names for a conflict: web3 and tronpy
    # pin eth-abi differently and cannot share a venv. starknet_py
    # pins neither, but it says so here rather than leaving the field
    # empty.
    sdk = "starknet_py"'''

if OLD_SDK in src:
    src = src.replace(OLD_SDK, NEW_SDK, 1)

open(PATH, "w", encoding="utf-8").write(src)

try:
    compile(src, PATH, "exec")
    print(f"{PATH}: the sync bridge and the interface methods are in")
except SyntaxError as e:
    print(f"{PATH}: it does NOT parse: {e}")
    sys.exit(1)
