"""
EVM engine — one implementation, any EVM chain.

This is the engine that makes the whitelabel real. Avalanche, Base,
Sonic, Arbitrum, Polygon, Linea, Scroll, Somnia, Canton's EVM
side: all of them are a config entry, because they all speak the same
protocol over JSON-RPC with the same ERC-20 interface.

Written now even though U-tron Pay runs on Tron, because that is the
whole point — Somnia next week is a YAML file, not a fork.

Carried over from the ubotpay audit rounds, do not regress:

  - submit/confirm split (round 24, C-2): the tx hash is returned
    before confirmation so the caller can persist it to payments.tx. A
    crash between broadcast and receipt must never cause a second
    transfer for the same payment.
  - treasury send lock (round 28, H-4): get_transaction_count + sign +
    broadcast run atomically. Released before the receipt wait so a
    slow node does not serialise outgoing payments.
  - decimals are read into a LOCAL variable, never a module global.
    Round 24 H-2: _balance_usdc assigned a Contract object to the
    global _DEC, and the next chain.decimals() call returned that
    object, killing to_units() with
    TypeError: unsupported operand type(s) for ** or pow(): 'int' and
    'Contract'. One chain's balance lookup broke another chain's
    payments because they shared a process. Here there is no shared
    mutable global to poison.
  - EIP-3009 verification runs locally BEFORE gas is spent (round 26
    C-4): a stolen x-api-key plus a malformed authorization used to
    cost the treasury gas.
"""
import os
import re
import time
import threading
import logging
from decimal import Decimal

from .engine import ChainEngine, NotSupported

log = logging.getLogger("utronpay.evm")

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "a", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint8"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "v", "type": "uint256"}],
     "outputs": [{"type": "bool"}]},
    {"name": "transferWithAuthorization", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
                {"name": "v", "type": "uint8"}, {"name": "r", "type": "bytes32"},
                {"name": "s", "type": "bytes32"}], "outputs": []},
    {"name": "name", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "version", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "string"}]},
]


class EvmEngine(ChainEngine):
    sdk = "web3"
    evm = True
    supports_x402 = True
    stable_symbol = "USDC"

    def __init__(self, *, chain_id: int, name: str, rpc: str,
                 stable_address: str, explorer: str, native_symbol: str = "ETH",
                 private_key_hex: str | None = None,
                 revenue_address: str | None = None,
                 stable_symbol: str | None = None,
                 key_path: str | None = None,
                 gas: int = 90_000, max_fee_gwei: int = 30,
                 priority_gwei: int = 1, timeout: int = 20):
        from web3 import Web3

        self.Web3 = Web3
        self._chain_id = chain_id
        self.name = name
        self.rpc = rpc
        self.stable_address = Web3.to_checksum_address(stable_address)
        self._explorer = explorer.rstrip("/")
        self.native_symbol = native_symbol
        if stable_symbol:
            self.stable_symbol = stable_symbol
        # The key comes from a 0600 file when a path is given, not from
        # the environment. An exported secret lands in /proc/<pid>/environ,
        # which is world-readable on the host and shows up in `ps eww`,
        # `systemctl show`, and crash dumps.
        self._key_path = key_path or ""
        self._key_hex = ""
        if key_path:
            try:
                with open(key_path, "r", encoding="utf-8") as fh:
                    self._key_hex = fh.read().strip()
            except OSError:
                # A missing key must not stop the service from booting:
                # before the treasury is funded there is nothing to sign,
                # and the read paths are useful. Sends are refused with a
                # message that names the file.
                log.warning(
                    "no treasury key at %s; payments cannot be signed", key_path)
        elif private_key_hex:
            self._key_hex = private_key_hex
        if self._key_hex and not self._key_hex.startswith("0x"):
            self._key_hex = "0x" + self._key_hex
        self._revenue = revenue_address or ""
        self._gas, self._fee_gwei, self._pri_gwei = gas, max_fee_gwei, priority_gwei
        self._lock = threading.Lock()
        self.w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": timeout}))
        self.token = self.w3.eth.contract(address=self.stable_address, abi=ERC20_ABI)

    # ── units ──
    def decimals(self) -> int:
        return int(self.token.functions.decimals().call())

    def to_units(self, amount) -> int:
        return int(Decimal(str(amount)) * (10 ** self.decimals()))

    def from_units(self, units: int) -> Decimal:
        return Decimal(int(units)) / (10 ** self.decimals())

    # ── validation ──
    def is_address(self, addr: str) -> bool:
        try:
            return bool(self.Web3.is_address(addr))
        except Exception:
            return False

    def is_tx_hash(self, value: str) -> bool:
        return isinstance(value, str) and bool(
            re.fullmatch(r"(0x)?[0-9a-fA-F]{64}", value.strip()))

    # ── reads ──
    def balance(self, address: str) -> dict:
        a = self.Web3.to_checksum_address(address)
        # Local binding, never a module global — see module docstring (H-2).
        tok = self.w3.eth.contract(address=self.stable_address, abi=ERC20_ABI)
        dec = int(tok.functions.decimals().call())
        raw = int(tok.functions.balanceOf(a).call())
        stable = float(Decimal(raw) / (10 ** dec))
        native = float(self.Web3.from_wei(self.w3.eth.get_balance(a), "ether"))
        out = {"address": a, "native": self.native_symbol,
               "native_balance": native, "chain": self.name,
               "chain_id": self._chain_id,
               self.stable_symbol.lower(): stable,
               # generic alias so chain-agnostic code can read it
               "stable_balance": stable}
        out[self.stable_symbol.lower()] = stable
        return out

    # ── signing ──
    def account(self):
        from eth_account import Account
        raw = self._key_hex[2:] if self._key_hex.startswith("0x") else self._key_hex
        if not raw:
            raise RuntimeError("private key not configured for %s" % self.name)
        return Account.from_key("0x" + raw if not raw.startswith("0x") else raw)

    def _transfer(self, acct, to: str, amount, nonce: int):
        tx = self.token.functions.transfer(
            self.Web3.to_checksum_address(to), self.to_units(amount)
        ).build_transaction({
            "from": acct.address, "nonce": nonce, "chainId": self._chain_id,
            "gas": self._gas,
            "maxFeePerGas": self.w3.to_wei(self._fee_gwei, "gwei"),
            "maxPriorityFeePerGas": self.w3.to_wei(self._pri_gwei, "gwei"),
        })
        return self.w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)

    def submit(self, to: str, amount, fee: float = 0.0) -> dict:
        with self._lock:
            acct = self.account()
            nonce = self.w3.eth.get_transaction_count(acct.address)
            h = self._transfer(acct, to, amount, nonce)
            fee_tx = None
            if fee and fee >= 0.01 and self._revenue:
                fee_tx = self._transfer(acct, self._revenue, fee, nonce + 1)
        return {"tx": h.hex(), "fee_tx": fee_tx.hex() if fee_tx else None,
                "fee_usdc": fee if fee_tx else 0.0}

    def confirm(self, tx: str, fee_tx: str | None = None, timeout: int = 60) -> dict:
        t0 = time.time()
        rcpt = self.w3.eth.wait_for_transaction_receipt(tx, timeout=timeout)
        if fee_tx:
            self.w3.eth.wait_for_transaction_receipt(fee_tx, timeout=timeout)
        return {"tx": tx, "ok": rcpt.status == 1, "block": rcpt.blockNumber,
                "latency_s": round(time.time() - t0, 2),
                "explorer": f"{self._explorer}/tx/{tx}",
                "fee_tx": fee_tx,
                "fee_explorer": f"{self._explorer}/tx/{fee_tx}" if fee_tx else None}

    # ── EIP-3009 / x402 ──
    def sign_authorization(self, payer_key: str, to: str, amount,
                           valid_seconds: int = 600) -> dict:
        from eth_account import Account
        from eth_account.messages import encode_typed_data
        acct = Account.from_key(payer_key)
        nonce = os.urandom(32)
        now = int(time.time())
        msg = self._auth_message(acct.address, to, amount, now + valid_seconds, nonce)
        sig = acct.sign_message(encode_typed_data(full_message=msg))
        return {"from": acct.address, "to": self.Web3.to_checksum_address(to),
                "value": self.to_units(amount), "validAfter": 0,
                "validBefore": now + valid_seconds, "nonce": "0x" + nonce.hex(),
                "v": sig.v, "r": "0x" + sig.r.to_bytes(32, "big").hex(),
                "s": "0x" + sig.s.to_bytes(32, "big").hex()}

    def _auth_message(self, sender: str, to: str, amount, valid_before: int, nonce: bytes) -> dict:
        return {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"}],
                "TransferWithAuthorization": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validBefore", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"}],
            },
            "primaryType": "TransferWithAuthorization",
            "domain": {"name": self.token.functions.name().call(),
                       "version": self.token.functions.version().call(),
                       "chainId": self._chain_id,
                       "verifyingContract": self.stable_address},
            "message": {"from": self.Web3.to_checksum_address(sender),
                        "to": self.Web3.to_checksum_address(to),
                        "value": self.to_units(amount), "validAfter": 0,
                        "validBefore": valid_before, "nonce": nonce},
        }

    def verify_authorization(self, auth: dict, *, expected_payee=None,
                             min_value=None):
        from eth_account import Account
        from eth_account.messages import encode_typed_data

        required = ("from", "to", "value", "validAfter", "validBefore",
                    "nonce", "r", "s", "v")
        missing = [k for k in required if k not in auth or auth[k] in (None, "")]
        if missing:
            return False, "malformed authorization"
        try:
            nonce_hex = auth["nonce"][2:] if auth["nonce"].startswith("0x") else auth["nonce"]
            if len(bytes.fromhex(nonce_hex)) != 32:
                return False, "malformed authorization"
            for k in ("r", "s"):
                raw = auth[k][2:] if auth[k].startswith("0x") else auth[k]
                if len(bytes.fromhex(raw)) != 32:
                    return False, "malformed authorization"
        except (ValueError, TypeError, AttributeError):
            return False, "malformed authorization"
        try:
            if expected_payee is not None and \
                    self.Web3.to_checksum_address(auth["to"]) != \
                    self.Web3.to_checksum_address(expected_payee):
                return False, "wrong payee"
            if min_value is not None and int(auth["value"]) < int(min_value):
                return False, "amount too low"
            now = int(time.time())
            if int(auth["validBefore"]) < now:
                return False, "expired"
            if int(auth["validAfter"]) > now:
                return False, "not yet valid"
            msg = self._auth_message(auth["from"], auth["to"], auth["value"],
                                     int(auth["validBefore"]),
                                     bytes.fromhex(nonce_hex))
            sig = (bytes.fromhex(auth["r"][2:]) + bytes.fromhex(auth["s"][2:])
                   + bytes([int(auth["v"])]))
            signer = Account.recover_message(
                encode_typed_data(full_message=msg), signature=sig)
            if signer.lower() != auth["from"].lower():
                return False, "bad signature"
            return True, signer
        except Exception as e:
            log.warning("verify_authorization failed: %s: %s",
                        type(e).__name__, str(e)[:160])
            return False, "verify error"

    def settle_authorization(self, auth: dict) -> dict:
        ok, why = self.verify_authorization(auth)
        if not ok:
            log.warning("settle_authorization refused: %s", why)
            return {"ok": False, "tx": None, "error": "auth rejected",
                    "reason": why, "explorer": None, "latency_s": 0.0}
        acct = self.account()
        t0 = time.time()
        nonce = self.w3.eth.get_transaction_count(acct.address)
        tx = self.token.functions.transferWithAuthorization(
            auth["from"], auth["to"], int(auth["value"]),
            int(auth["validAfter"]), int(auth["validBefore"]),
            bytes.fromhex(auth["nonce"][2:]), int(auth["v"]),
            bytes.fromhex(auth["r"][2:]), bytes.fromhex(auth["s"][2:])
        ).build_transaction({
            "from": acct.address, "nonce": nonce, "chainId": self._chain_id,
            "gas": 120_000,
            "maxFeePerGas": self.w3.to_wei(self._fee_gwei, "gwei"),
            "maxPriorityFeePerGas": self.w3.to_wei(self._pri_gwei, "gwei")})
        h = self.w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
        rcpt = self.w3.eth.wait_for_transaction_receipt(h, timeout=60)
        return {"tx": h.hex(), "ok": rcpt.status == 1,
                "latency_s": round(time.time() - t0, 2),
                "explorer": f"{self._explorer}/tx/{h.hex()}"}
    # ── can this deployment sign? ──
    def has_signer(self) -> bool:
        """Whether a treasury key is available.

        A read-only deployment does not have one, and saying so plainly
        is more useful than a warning about an env var that by design
        never holds the key.
        """
        return bool(self._key_hex)

    @property
    def _treasury_address(self) -> str:
        """The treasury's own address, as this engine sees it.

        The facade reads this attribute directly - chain.WALLET_ADDRESS
        resolves against it - so it is a property and not a method.
        """
        if not self._key_hex:
            return ""
        try:
            from eth_account import Account
            return Account.from_key(self._key_hex).address
        except Exception:
            return ""
