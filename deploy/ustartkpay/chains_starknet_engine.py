"""Starknet engine — read paths first.

Starknet is not EVM-compatible, so this is a new engine rather than a
profile for the existing `evm` one. The differences are load-bearing:

  - an account is a CONTRACT, not a key. The key signs; the contract
    executes. A Starknet account address is derived from the public
    key, the salt and the class hash.
  - fees are paid in STRK, and the fee token is set per transaction
  - a transfer goes through the account's __execute__ entry point,
    which takes a list of calls, not a single ERC20 transfer
  - the token contract is called with Cairo 1 selectors: sha256 of the
    entry point name, low 250 bits

This first version implements the READ paths only:

  - decimals(), symbol(), name() of a token
  - balance_of(address) of a token
  - the treasury's own address

The send path is deliberately absent. A read-only engine that says so
is more useful than a send path that has never settled a real payment
on Starknet. `has_signer()` reports whether an account exists.

The Starknet string encoding is worth spelling out, because getting it
wrong looks plausible rather than broken. Two encodings are in use:

  - a ByteArray (what the USDC contract uses) comes back as ONE felt
    with the text right-aligned in its low bytes
  - a legacy felt array (what STRK uses) is
    [offset, felt_length, ...payload], 31 bytes a felt

Both are handled by decode_string().
"""
import logging
import os

log = logging.getLogger("ustartpay.starknet")

# The Starknet token list registry address used in most guides no
# longer has a contract at it ("Contract not found"), so the token list
# has to come from an indexer and every address has to be verified
# against the chain. That is what deploy/verify_starknet_usdc.py does.

# The fee token. STRK pays for every transaction on Starknet.
STRK = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"

# USDC on Starknet mainnet, verified on chain:
#   name() "USD Coin", symbol() "USDC", decimals() 6
USDC = "0x053c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8"

MASK = (1 << 251) - 1
BYTES_PER_FELT = 31


def decode_string(felts):
    """Decode a Starknet string. Two encodings are in use.

    A ByteArray - what the USDC contract uses - comes back as ONE felt
    whose bytes are the string, right-aligned in its low bytes.

    A legacy felt array - what STRK uses - is [offset, data, length]:
    the data felt comes BEFORE the length, and the bytes are
    right-aligned in the felt's high end. So STRK's symbol arrives as
    [0, 0x5354524b, 4] - the felt IS "STRK" and the length is 4.

    Getting this backwards yields a string of NUL bytes with the
    length at the end, which is what a first attempt produced, and it
    looks plausible rather than broken.
    """
    if not isinstance(felts, list) or not felts:
        return ""

    # ByteArray: one felt, the bytes are the string
    if len(felts) == 1:
        b = int(felts[0]).to_bytes(32, "big")
        return b.strip(b"\x00").decode("ascii", "replace")

    # legacy: [offset, data, length]
    if len(felts) >= 3:
        data = int(felts[1]).to_bytes(32, "big").strip(b"\x00")
        length = int(felts[2])
        if data:
            return data[:length].decode("ascii", "replace") if length else data.decode("ascii", "replace")

    # a plain felt array with no length marker
    out = bytearray()
    for f in felts:
        out += int(f).to_bytes(32, "big").strip(b"\x00")
    return out.decode("ascii", "replace")


class StarknetEngine:
    """Read-only Starknet engine."""

    evm = False                      # not EVM
    supports_x402 = False            # no EVM signature recovery
    stable_symbol = "USDC"
    # The registry checks the sdk names for a conflict: web3 and tronpy
    # pin eth-abi differently and cannot share a venv. starknet_py
    # pins neither, but it says so here rather than leaving the field
    # empty.
    sdk = "starknet_py"

    def __init__(self, *, rpc: str, stable_address: str,
                 explorer: str = "", native_symbol: str = "STRK",
                 account_address: str | None = None,
                 key_file: str | None = None,
                 fee_token_address: str = STRK,
                 rpc_urls: list | None = None,
                 timeout: int = 30):
        self.rpc = rpc
        self.rpc_urls = list(rpc_urls or [])
        self.stable_address = stable_address
        self._explorer = (explorer or "").rstrip("/")
        self.native_symbol = native_symbol
        self.fee_token_address = fee_token_address
        self._account_address = account_address or ""
        # The account's key, by PATH. It is never read from the
        # environment: an env var is visible in /proc/<pid>/environ, in
        # ps eww and in crash dumps. The file is mode 0600.
        self._key_file = key_file or ""
        self._timeout = timeout
        self._client = None
        self._decimals = None
        self._fee_decimals = None

    # ── the client ────────────────────────────────────────────────────
    @property
    def client(self):
        if self._client is None:
            from starknet_py.net.full_node_client import FullNodeClient
            urls = [self.rpc] + self.rpc_urls
            last = None
            for url in urls:
                try:
                    c = FullNodeClient(node_url=url)
                    self._client = c
                    self._rpc_url = url
                    log.info("starknet: using %s", url)
                    return c
                except Exception as e:
                    last = e
            raise RuntimeError(f"no working Starknet RPC: {last}")
        return self._client

    async def _call(self, contract: str, entry: str, calldata=None):
        from starknet_py.hash.selector import get_selector_from_name
        from starknet_py.net.client_models import Call
        return await self.client.call_contract(
            Call(to_addr=int(contract, 16),
                 selector=get_selector_from_name(entry),
                 calldata=calldata or []),
            block_number="latest")

    async def _call_many(self, calls):
        """Run several calls at once. The RPC supports batching."""
        from starknet_py.hash.selector import get_selector_from_name
        from starknet_py.net.client_models import Call
        return await self.client.call_contract(
            Call(to_addr=int(self.stable_address, 16),
                 selector=get_selector_from_name("balance_of"),
                 calldata=[int(calls, 16)]),
            block_number="latest")

    # ── the engine interface, as the facade expects it ───────────────
    def chain_id(self):
        return "SN_MAIN"

    @property
    def name(self):
        return "Starknet"

    @property
    def _explorer_address(self):
        return self._explorer

    @property
    def _treasury_address(self):
        return self._account_address

    def has_signer(self) -> bool:
        """Whether an account exists.

        A read-only deployment does not have one, and saying so plainly
        is more useful than a warning about an env var that by design
        never holds the key.
        """
        return bool(self._account_address)

    async def _decimals_async(self) -> int:
        if self._decimals is None:
            res = await self._call(self.stable_address, "decimals")
            self._decimals = int(res[0])
        return self._decimals

    async def token_symbol(self) -> str:
        return decode_string(await self._call(self.stable_address, "symbol"))

    async def token_name(self) -> str:
        return decode_string(await self._call(self.stable_address, "name"))

    async def _balance_async(self, address: str) -> float:
        """The token balance of an address, in human units."""
        raw = await self._call(self.stable_address, "balance_of",
                               [int(address, 16)])
        dec = await self._decimals_async()
        return int(raw[0]) / (10 ** dec)

    # ── the sync bridge ─────────────────────────────────────────────
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
        """Run a coroutine to completion from synchronous code.

        Two cases, because they need different things:

        - the caller is synchronous: a fresh event loop, on this thread
        - the caller is ALREADY inside a loop: asyncio refuses to run
          one loop inside another ("Cannot run the event loop while
          another loop is running"), so the coroutine goes to a THREAD
          with its own loop. A thread has no loop of its own, so a
          fresh one inside it is fine.

        The second case is not a rare caller: the test drives the
        engine from an async function, and so would any handler that
        already awaits something.
        """
        import asyncio
        import threading

        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            running = False

        if not running:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()

        # Already inside a loop: run it on a thread.
        box = {}

        def _worker():
            loop = asyncio.new_event_loop()
            try:
                box["value"] = loop.run_until_complete(coro)
            except BaseException as e:      # noqa: BLE001 - carried to the caller
                box["error"] = e
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join()
        if "error" in box:
            raise box["error"]
        return box.get("value")

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
        # The async primitives, not the sync methods - the sync
        # balance() would call itself.
        stable = self._run(self._balance_async(address))
        native = self._run(self._native_balance_async(address))
        return {
            "address": address,
            # `name` is a PROPERTY on this engine and `chain_id` is a
            # method - so calling name() would raise "str object is not
            # callable". The EVM engine has them the other way round,
            # which is the trap.
            "chain": self.name,
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

    async def _native_balance_async(self, address: str) -> float:
        """The STRK balance of an address, in human units.

        STRK is the fee token, so the account needs a balance of it to
        pay for its own transactions - and a treasury needs one to be
        able to move anything at all.
        """
        raw = await self._call(self.fee_token_address, "balance_of",
                               [int(address, 16)])
        if self._fee_decimals is None:
            raw_dec = await self._call(self.fee_token_address, "decimals")
            self._fee_decimals = int(raw_dec[0])
        return int(raw[0]) / (10 ** self._fee_decimals)

    async def _submit(self, to: str, amount, fee: float = 0.0) -> dict:
        """Sign a transfer with the account and broadcast it.

        An account on Starknet is a CONTRACT. The transfer is not sent
        by the account's key directly - it is executed by calling the
        account's own __execute__ entry point, with the transfer call
        inside it. The account signs that outer call with its own key,
        which is what makes the transfer come from the account rather
        than from whoever holds the key.

        The chain_id is part of the signed payload, so it has to be
        the network the transaction is actually for.
        """
        from starknet_py.hash.selector import get_selector_from_name
        from starknet_py.net.client_models import Call
        from starknet_py.net.models.chains import StarknetChainId
        from starknet_py.net.signer.stark_curve_signer import StarkCurveSigner

        key_pair = self._key_pair()
        if key_pair is None:
            return {"ok": False, "tx": None,
                    "error": "this Starknet deployment has no key"}

        dec = await self._decimals_async()
        units = int(amount * (10 ** dec))
        inner = Call(
            to_addr=int(self.stable_address, 16),
            selector=get_selector_from_name("transfer"),
            calldata=[int(to, 16), units, 0],
        )

        chain_id = (StarknetChainId.SEPOLIA
                    if "sepolia" in (self.rpc or "").lower()
                    else StarknetChainId.MAINNET)
        signer = StarkCurveSigner(hex(key_pair.private_key), key_pair, chain_id)

        from starknet_py.net.account.account import Account
        account = Account(
            client=await self.client,
            address=hex(int(self._account_address, 16)),
            signer=signer,
            chain=chain_id,
        )
        response = await account.execute_v3(calls=[inner], auto_estimate=True)
        return {
            "ok": True,
            "tx": response.transaction_hash,
            "error": None,
            "explorer": self._explorer_address(response.transaction_hash),
        }

    async def _confirm(self, tx: str, timeout: int = 60) -> dict:
        """Wait for a transaction to settle."""
        client = await self.client
        receipt = await client.wait_for_tx(tx)
        failure = getattr(receipt, "failure_reason", None)
        return {
            "ok": failure is None,
            "tx": tx,
            "error": str(failure) if failure else None,
            "explorer": self._explorer_address(tx),
            "latency_s": float(getattr(receipt, "execution_time", 0) or 0),
        }

    def _key_pair(self):
        """The account's KeyPair, if this deployment has one.

        The key is read from a file, never from the environment - an
        env var is visible in /proc/<pid>/environ, in ps eww and in
        crash dumps. The file is mode 0600.
        """
        if not self._key_file:
            return None
        try:
            from starknet_py.net.signer.key_pair import KeyPair
            import json
            with open(self._key_file, encoding="utf-8") as fh:
                d = json.load(fh)
            return KeyPair(int(d["private_key"], 16), d["public_key"])
        except Exception:
            return None

    async def _total_supply_async(self) -> float:
        raw = await self._call(self.stable_address, "total_supply")
        dec = await self._decimals_async()
        return int(raw[0]) / (10 ** dec)
