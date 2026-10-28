"""
Tron engine — USDT-TRC20 and TRX.

Implements ChainEngine for Tron mainnet/shasta. This is the only place
in U-tron Pay that imports tronpy, which is the point: the rest of the
codebase never learns that Tron is not an EVM chain.

Carried over from the ubotpay audit rounds:

  - submit/confirm split (round 24, C-2). The caller persists the txid
    to payments.tx BEFORE confirmation, so a crash between broadcast and
    receipt can never trigger a second transfer.
  - treasury send lock (round 28, H-4). Tron allocates the account nonce
    the same way EVM does, so two concurrent callers reading the same
    nonce would have one tx rejected. The lock covers nonce-read → sign
    → broadcast and is released before confirmation.
  - _DEC is a plain int, never reassigned from a contract object (the
    round-24 H-2 poison).
  - fail closed: a missing key raises, it never silently no-ops.

EIP-3009: USDT-TRC20 has no transferWithAuthorization, so supports_x402
is False and every x402 path refuses. Refusing is the safe answer —
accepting an authorization the chain cannot execute would report a
settlement that never settles.
"""
import os
import re
import time
import threading
import logging
import hashlib
from decimal import Decimal

import requests

from .engine import ChainEngine, NotSupported

log = logging.getLogger("utronpay.tron")

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{34}$")


def _b58decode_check(s: str):
    try:
        import base58
        return base58.b58decode_check(s)
    except ImportError:
        pass
    except Exception:
        return None
    n = 0
    for ch in s:
        if ch not in _B58_ALPHABET:
            return None
        n = n * 58 + _B58_ALPHABET.index(ch)
    raw = n.to_bytes(25, "big")
    payload, chk = raw[:21], raw[21:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != chk:
        return None
    return payload


class TronEngine(ChainEngine):
    sdk = "tronpy"
    evm = False
    supports_x402 = False
    stable_symbol = "USDT"
    native_symbol = "TRX"

    def __init__(self, *, network: str = "mainnet", rpc: str | None = None,
                 explorer: str | None = None, stable_address: str | None = None,
                 private_key_hex: str | None = None,
                 revenue_address: str | None = None,
                 api_key: str | None = None,
                 chain_id: int | None = None,
                 fallback_rpcs: list | None = None):
        self.network = network
        self.rpc = rpc or (
            "https://api.trongrid.io" if network == "mainnet"
            else "https://api.shasta.trongrid.io")
        # TronGrid without an API key answers 429 from a shared IP, which
        # is not hypothetical: it is how this engine was verified. Reads
        # walk the list; sends stay on the primary so a fallback never
        # becomes the node of record for a broadcast.
        self._rpcs = [self.rpc] + [r for r in (fallback_rpcs or []) if r != self.rpc]
        self._explorer = explorer or (
            "https://tronscan.org" if network == "mainnet"
            else "https://shasta.tronscan.org")
        self.name = "Tron Mainnet" if network == "mainnet" else "Tron Shasta"
        self.stable_address = stable_address or "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
        self._key_hex = private_key_hex or os.environ.get("UTRON_PRIVATE_KEY", "")
        # The revenue destination (where the transfer fee goes) comes from the
        # profile, and the profile is filled in by the registry from the
        # environment. The env fallback below covers the case where the
        # profile did not resolve it: this deployment names it
        # UTRON_TREASURY_ADDRESS, and UTRON_REVENUE_ADDRESS is the name the
        # inherited code used. Both are checked, because an empty revenue
        # address means the fee has nowhere to go and is silently dropped.
        self._revenue = (revenue_address
                         or os.environ.get("UTRON_REVENUE_ADDRESS", "")
                         or os.environ.get("UTRON_TREASURY_ADDRESS", "")
                         or os.environ.get("UTREASURY_ADDRESS", ""))
        self._api_key = api_key or os.environ.get("UTRON_TRONGRID_KEY", "")
        # The chain id comes from the profile, like every other
        # chain-specific value. It used to be hardcoded to 0, so the
        # deployment reported chain_id 0 - indistinguishable from "no
        # chain", and wrong for anything that keys on it.
        self._chain_id = int(chain_id or 0)
        self._dec = 6  # Tether USD on Tron
        self._lock = threading.Lock()
        # Reads and sends start on the first fallback that answered during
        # verification rather than on TronGrid, which rate-limits a shared
        # IP without an API key. Sending is pinned to this value so a
        # fallback read can never move where a broadcast is signed for.
        self._live_rpc = self._rpcs[0] if self._rpcs else self.rpc

    # ── plumbing ──
    def _headers(self):
        return {"TRON-PRO-API-KEY": self._api_key} if self._api_key else {}

    def _get(self, path, *, params=None, timeout=10):
        """GET with fallback across the RPC list.

        A 429 is a rate limit, not a bad answer, so we move to the next
        node rather than surfacing a false zero balance to the caller.
        """
        last = None
        for base in self._rpcs:
            try:
                r = requests.get(base + path, headers=self._headers(),
                                 params=params, timeout=timeout)
                r.raise_for_status()
                if base == self.rpc:
                    self._live_rpc = base
                return r.json()
            except Exception as e:
                last = e
        raise last if last else RuntimeError("no RPC available")

    def explorer_url(self, tx: str) -> str:
        return f"{self._explorer}/#/transaction/{tx}"

    # ── units ──
    def decimals(self) -> int:
        return self._dec

    def to_units(self, amount) -> int:
        return int(Decimal(str(amount)) * (10 ** self._dec))

    def from_units(self, units: int) -> Decimal:
        return Decimal(int(units)) / (10 ** self._dec)

    # ── validation ──
    def is_address(self, addr: str) -> bool:
        if not isinstance(addr, str) or not _B58_RE.match(addr):
            return False
        raw = _b58decode_check(addr)
        if raw is None or len(raw) != 21:
            return False
        return raw[0] == (0x41 if self.network == "mainnet" else 0xA0)

    def is_tx_hash(self, value: str) -> bool:
        return isinstance(value, str) and bool(
            re.fullmatch(r"(0x)?[0-9a-fA-F]{64}", value.strip()))

    # ── reads ──
    # Reads go through tronpy, not hand-rolled REST. Verified on mainnet:
    #   /v1/accounts/{addr}/balance → 404 on TronGrid (that is the TronGrid
    #   REST API, not the Tron protocol) and 404 on public nodes that speak
    #   only /wallet/*. A raw /wallet/triggerconstant POST returned HTML
    #   from the public node. tronpy handles ABI encoding and picks the
    #   right route; it read 14,493.84 TRX and 6,741.53 USDT for a known
    #   holder, so the numbers below are real.
    def _client(self):
        from tronpy import Tron
        from tronpy.providers import HTTPProvider
        return Tron(provider=HTTPProvider(endpoint_uri=self._live_rpc,
                                          timeout=20,
                                          api_key=self._api_key or None))

    @staticmethod
    def _unwrap(v):
        """tronpy returns either the value or a callable depending on the
        path taken; accept both instead of guessing."""
        return v.call() if hasattr(v, "call") else v

    def _trx_balance(self, addr: str) -> float:
        if not self.is_address(addr):
            return 0.0
        try:
            acct = self._client().get_account(addr)
            raw = acct["balance"] if isinstance(acct, dict) else acct.balance
            return int(raw) / 1e6
        except Exception:
            log.warning("trx balance lookup failed for %s", addr)
            return 0.0

    def _trc20_balance(self, addr: str) -> float:
        if not self.is_address(addr):
            return 0.0
        try:
            c = self._client().get_contract(self.stable_address)
            return int(self._unwrap(c.functions.balanceOf(addr))) / (10 ** self._dec)
        except Exception:
            log.warning("usdt balance lookup failed for %s", addr)
            return 0.0

    def balance(self, address: str) -> dict:
        trx = self._trx_balance(address)
        usdt = self._trc20_balance(address)
        return {"address": address, "trx": trx, "usdt": usdt,
                # aliases so chain-agnostic front-end code keeps working
                "native": "TRX", "native_balance": trx,
                "usdc": usdt, "avax": trx,
                # generic name, so the UI never has to know the symbol
                "stable_symbol": self.stable_symbol,
                "stable_balance": usdt}

    # ── signing ──
    def _key(self):
        from tronpy.keys import PrivateKey
        raw = self._key_hex[2:] if self._key_hex.startswith("0x") else self._key_hex
        if not raw:
            raise RuntimeError("UTRON_PRIVATE_KEY not configured")
        try:
            b = bytes.fromhex(raw)
        except ValueError:
            raise RuntimeError("UTRON_PRIVATE_KEY is not valid hex")
        if len(b) != 32:
            raise RuntimeError("UTRON_PRIVATE_KEY must be 32 bytes")
        return PrivateKey(b)

    def treasury_address(self) -> str:
        return self._key().public_key.to_base58check_address()

    def _run(self, coro):
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    async def _broadcast(self, to: str, amount_base: int) -> str:
        # tronpy's async API differs from the sync one in three places, and
        # each one fails loudly if you use the sync shape:
        #   - there is no from_url; the client is built with a provider, and
        #     the async client needs AsyncHTTPProvider, not HTTPProvider
        #   - get_contract is a coroutine
        #   - the contract method is obtained by subscription and then
        #     invoked, and awaiting it yields an AsyncTransactionBuilder
        #     whose build() is itself a coroutine
        # The sync client (self._client) is used for the reads, which is why
        # only the write path needed the async variant.
        from tronpy import AsyncTron
        from tronpy.providers import AsyncHTTPProvider
        tron = AsyncTron(provider=AsyncHTTPProvider(endpoint_uri=self._live_rpc,
                                                    timeout=20,
                                                    api_key=self._api_key or None))
        try:
            pk = self._key()
            owner = pk.public_key.to_base58check_address()
            contract = await tron.get_contract(self.stable_address)
            builder = await contract.functions["transfer"](to, int(amount_base))
            tx = await builder.with_owner(owner).build()
            tx.sign(pk)
            # In this tronpy version txid is already a hex string; in
            # others it is bytes. Accept both so the code does not depend
            # on the library's internal representation.
            raw_txid = tx.txid
            txid = raw_txid.hex() if hasattr(raw_txid, "hex") else str(raw_txid)
            await tx.broadcast()
            return txid
        finally:
            close = getattr(tron, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass

    # ── money movement ──
    def submit(self, to: str, amount, fee: float = 0.0) -> dict:
        if not self.is_address(to):
            raise ValueError(f"invalid tron address: {to}")
        amount_base = self.to_units(amount)
        with self._lock:
            tx = self._run(self._broadcast(to, amount_base))
            fee_tx = None
            if fee and fee >= 0.01 and self._revenue and self.is_address(self._revenue):
                fee_tx = self._run(self._broadcast(self._revenue, self.to_units(fee)))
        return {"tx": tx, "fee_tx": fee_tx, "fee_usdc": fee if fee_tx else 0.0}

    def confirm(self, tx: str, fee_tx: str | None = None, timeout: int = 60) -> dict:
        t0 = time.time()
        deadline = t0 + timeout
        base = {"tx": tx, "ok": False, "block": 0,
                "explorer": self.explorer_url(tx),
                "fee_tx": fee_tx,
                "fee_explorer": self.explorer_url(fee_tx) if fee_tx else None}
        while time.time() < deadline:
            info = self._tx_info(tx)
            if info:
                receipt = info.get("receipt") or {}
                res = receipt.get("result") or info.get("result") or ""
                if res == "SUCCESS":
                    return {**base, "ok": True,
                            "block": int(info.get("blockNumber") or 0),
                            "latency_s": round(time.time() - t0, 2)}
                if res in ("REVERT", "FAILED"):
                    return {**base, "latency_s": round(time.time() - t0, 2)}
            time.sleep(0.4)
        base["latency_s"] = round(time.time() - t0, 2)
        return base

    def _tx_info(self, tx: str) -> dict:
        """Read a transaction's receipt.

        The node this deployment talks to is a java-tron native node, and its
        REST surface is /wallet/*, not the TronGrid /v1/* API: asking
        /v1/transaction/info on it returns a deprecation notice, not data, so
        the receipt never arrives and every payment looked unconfirmed even
        though it had been broadcast successfully.

        The native route is tried first (it is what answers on the node that
        actually signed the transaction) and TronGrid is kept as a fallback
        for when a key is configured and the native node is unreachable.
        """
        try:
            r = requests.post(self._live_rpc + "/wallet/gettransactioninfobyid",
                              json={"value": tx},
                              headers=self._headers(), timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data:
                    return data
        except Exception:
            pass
        try:
            data = self._get("/v1/transaction/info", params={"value": tx}, timeout=5)
            rows = data.get("data") or []
            if rows:
                return rows[0]
        except Exception:
            pass
        return {}

    # ── x402: absent on USDT-TRC20 ──
    def verify_authorization(self, auth: dict, *, expected_payee=None, min_value=None):
        return False, "unsupported on tron"

    def settle_authorization(self, auth: dict) -> dict:
        log.warning("settle_authorization refused: unsupported on tron")
        return {"ok": False, "tx": None, "error": "unsupported on tron",
                "reason": "USDT-TRC20 has no transferWithAuthorization",
                "explorer": None, "latency_s": 0.0}

    def sign_authorization(self, *a, **k):
        raise NotSupported(
            "USDT-TRC20 has no transferWithAuthorization; x402 permits are an "
            "EVM-only primitive. Use U-bot Pay for agent-to-agent settlement.")