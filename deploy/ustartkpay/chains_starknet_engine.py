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

    def __init__(self, *, rpc: str, stable_address: str,
                 explorer: str = "", native_symbol: str = "STRK",
                 account_address: str | None = None,
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

    async def decimals(self) -> int:
        if self._decimals is None:
            res = await self._call(self.stable_address, "decimals")
            self._decimals = int(res[0])
        return self._decimals

    async def token_symbol(self) -> str:
        return decode_string(await self._call(self.stable_address, "symbol"))

    async def token_name(self) -> str:
        return decode_string(await self._call(self.stable_address, "name"))

    async def balance(self, address: str) -> float:
        """The token balance of an address, in human units."""
        raw = await self._call(self.stable_address, "balance_of",
                               [int(address, 16)])
        dec = await self.decimals()
        return int(raw[0]) / (10 ** dec)

    async def total_supply(self) -> float:
        raw = await self._call(self.stable_address, "total_supply")
        dec = await self.decimals()
        return int(raw[0]) / (10 ** dec)
