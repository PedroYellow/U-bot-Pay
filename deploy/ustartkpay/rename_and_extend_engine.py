#!/usr/bin/env python3
"""Rename the async reads and add the ones the interface needs.

The interface is synchronous and the engine's reads were `async def`,
so adding the sync methods would have shadowed them - and
`self.balance(address)` inside the sync `balance()` would have called
itself.

So the async primitives are renamed with an _async suffix, which is
what they are, and the sync methods wrap them:

    decimals()      ->  _decimals_async()
    balance()       ->  _balance_async()

And the ones that did not exist are added:

    _native_balance_async()   the STRK balance
    _submit()                 sign and broadcast a transfer
    _confirm()                wait for it to settle

The signing is the Starknet part that is genuinely different. An
account is a CONTRACT: it holds its own key, and a transfer is
executed by calling the account's __execute__ entry point with the
transfer call inside it. The account signs the transaction with its
own key, so the engine needs the KeyPair and the account's address.

Usage: rename_and_extend_engine.py <engine.py>
"""
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "/data/workspace/ubotpay/deploy/ustartkpay/chains_starknet_engine.py"

src = open(PATH, encoding="utf-8").read()

# ── 1. rename the async primitives ────────────────────────────────
RENAMES = [
    ("    async def decimals(self) -> int:",
     "    async def _decimals_async(self) -> int:"),
    ("    async def balance(self, address: str) -> float:",
     "    async def _balance_async(self, address: str) -> float:"),
    ("    async def total_supply(self) -> float:",
     "    async def _total_supply_async(self) -> float:"),
]

for old, new in RENAMES:
    if old in src:
        src = src.replace(old, new, 1)
    else:
        print(f"  the anchor was not found: {old.strip()}")

# the calls inside the renamed methods
src = src.replace("            dec = await self.decimals()",
                  "            dec = await self._decimals_async()")

# ── 2. the new async primitives ───────────────────────────────────
ANCHOR = "    async def _total_supply_async(self) -> float:"

ADDED = '''    async def _native_balance_async(self, address: str) -> float:
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
            client=self.client,
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
        client = self.client
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

'''

if ANCHOR in src and "_native_balance_async" not in src:
    src = src.replace(ANCHOR, ADDED + ANCHOR, 1)
else:
    print("  the anchor for the new primitives was not found, or they exist")

# ── 3. the key file in the constructor ────────────────────────────
OLD_INIT = '''                 account_address: str | None = None,
                 fee_token_address: str = STRK,'''

NEW_INIT = '''                 account_address: str | None = None,
                 key_file: str | None = None,
                 fee_token_address: str = STRK,'''

if OLD_INIT in src:
    src = src.replace(OLD_INIT, NEW_INIT, 1)
else:
    print("  the constructor signature was not found")

OLD_FIELD = '''        self._account_address = account_address or ""'''
NEW_FIELD = '''        self._account_address = account_address or ""
        # The account's key, by PATH. It is never read from the
        # environment: an env var is visible in /proc/<pid>/environ, in
        # ps eww and in crash dumps. The file is mode 0600.
        self._key_file = key_file or ""'''

if OLD_FIELD in src:
    src = src.replace(OLD_FIELD, NEW_FIELD, 1)
else:
    print("  the account field was not found")

open(PATH, "w", encoding="utf-8").write(src)

try:
    compile(src, PATH, "exec")
    print(f"{PATH}: it parses")
except SyntaxError as e:
    print(f"{PATH}: it does NOT parse: {e}")
    sys.exit(1)
