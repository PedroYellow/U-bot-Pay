"""
chain.py — the compatibility facade over the whitelabel engine.

api.py, core.py, tg.py, dataagent.py and bridge.py all do `import chain`
and call chain.send_usdc(...), chain.balance(...), chain.to_units(...).
This module keeps that surface working while delegating every call to
the engine selected by the active profile.

So the app code is chain-agnostic, and a new deployment is a YAML file.
If you find yourself editing this file to add a chain, the profile is
the right place instead.

Loading order matters: the engine is built lazily on first use, so
importing this module never pulls in tronpy or web3 unless the profile
actually asks for that SDK. That is what keeps a Tron deployment from
importing web3 (and vice versa).
"""
import os
import logging

from chains.engine import NotSupported
from chains.registry import Registry, load_profile, active_profile_name
from config import cfg, pop_cfg, set_cfg, del_cfg

log = logging.getLogger("utronpay.chain")

_registry = None


def _reg() -> Registry:
    global _registry
    if _registry is None:
        name = cfg("PROFILE") or active_profile_name()
        prof = load_profile(name)
        _registry = Registry(prof)
        log.info("chain: profile %s → %s on %s",
                 name, _registry.primary.stable_symbol, _registry.primary.name)
    return _registry


def _eng():
    return _reg().primary


def reload_profile():
    """Drop the cached registry. Used by tests and by a SIGHUP reload."""
    global _registry
    _registry = None


# ── identity, read from the profile so nothing is hardcoded ──
# PEP 562 module __getattr__: a descriptor's __get__ is only invoked for
# class attributes, not module attributes, so this is the mechanism that
# actually works for `chain.USDC` resolving against the active engine.
_DEFAULTS = {
    "USDC": None, "STABLE_SYMBOL": "USDC", "NATIVE_SYMBOL": "ETH",
    "REVENUE_ADDRESS": "",
    "EXPLORER": "", "NETWORK_NAME": "", "CHAIN_ID": 0,
    # The treasury's own address, as the engine sees it. On Solana it
    # comes from the signer, so it exists only when the engine has a
    # key -- which is exactly the question a startup check wants to
    # ask.
    "WALLET_ADDRESS": "",
    # Does the active engine have a signer? A read-only deployment
    # does not, and saying so plainly is more useful than a warning
    # about an env var that by design never holds the key.
    "HAS_SIGNER": False,
}


def __getattr__(name):
    if name in _DEFAULTS:
        attr = {"USDC": "stable_address", "STABLE_SYMBOL": "stable_symbol",
                "NATIVE_SYMBOL": "native_symbol",
                "REVENUE_ADDRESS": "_revenue", "EXPLORER": "_explorer",
                "NETWORK_NAME": "name", "CHAIN_ID": "_chain_id",
                "WALLET_ADDRESS": "_treasury_address",
                "HAS_SIGNER": "_has_signer"}[name]
        val = getattr(_eng(), attr, None)
        if name == "HAS_SIGNER":
            # HAS_SIGNER is a property on the engine, not a plain
            # attribute; calling it is what answers the question.
            try:
                val = bool(_eng().has_signer())
            except Exception:
                val = False
        return _DEFAULTS[name] if val is None else val
    raise AttributeError("module 'chain' has no attribute %r" % name)


def __dir__():
    return sorted(list(globals()) + list(_DEFAULTS))


# ── units ──
def decimals() -> int:
    return _eng().decimals()


def to_units(amount, symbol: str | None = None) -> int:
    """Human amount -> base units, for a given token.

    The symbol matters: USDC and USDT both have 6 decimals today, but
    a token with 9 would inherit 6 silently and the payment would be
    a hundred times too large.
    """
    return _eng().to_units(amount, symbol)


def from_units(units, symbol: str | None = None):
    return _eng().from_units(units, symbol)


def tx_status(sig: str) -> dict:
    """The on-chain status of a signature.

    "confirmed" means the transaction is on chain with no error.
    "failed" means it is on chain and it reverted. "pending" means
    the chain does not know it yet -- which for a public RPC is
    normal for a few seconds and means the payment has NOT been
    rejected.
    """
    return _eng().tx_status(sig)


def build_unsigned(from_address: str, to: str, amount_u: int,
                   symbol: str | None = None) -> dict:
    """Build a transfer for the USER to sign in their own wallet.

    The web flow: the backend builds, the wallet signs, the browser
    broadcasts. The backend never holds the user's key.
    """
    return _eng().build_unsigned(from_address, to, amount_u, symbol)


# ── validation ──
def is_address(addr: str) -> bool:
    return _eng().is_address(addr)


def is_tx_hash(value: str) -> bool:
    return _eng().is_tx_hash(value)


# ── reads ──
def balance(address: str) -> dict:
    return _eng().balance(address)


def balance_at(address: str, chain_id=None) -> dict:
    return _reg().engine_for(chain_id).balance(address)


# The token the deployment settles in by default. api.py uses it when
# the user names a currency but not a token.
def primary_symbol() -> str:
    return _reg().primary_symbol


# ── money movement ──
def submit_usdc(to: str, amount, fee: float = 0.0,
                symbol: str | None = None) -> dict:
    return _eng().submit(to, amount, fee, symbol=symbol)


def confirm_usdc(tx: str, fee_tx: str | None = None, timeout: int = 60) -> dict:
    return _eng().confirm(tx, fee_tx, timeout)


def send_usdc(to: str, amount, memo_hash: str | None = None,
              fee: float = 0.0, symbol: str | None = None) -> dict:
    return _eng().send(to, amount, fee, symbol=symbol)


# ── x402 / EIP-3009 ──
def sign_authorization(*a, **k) -> dict:
    return _eng().sign_authorization(*a, **k)


def verify_authorization(auth: dict, *, expected_payee=None, min_value=None):
    return _eng().verify_authorization(auth, expected_payee=expected_payee,
                                       min_value=min_value)


def settle_authorization(auth: dict) -> dict:
    return _eng().settle_authorization(auth)


# ── registry shape used by api.py / the wallet selector ──
def chains() -> list:
    return _reg().chains()


def get_chain(chain_id=None):
    return _reg().engine_for(chain_id)


class Chain:
    """Thin shim so code that introspects `.name` / `.usdc` / `.evm`
    keeps working against an engine."""

    def __init__(self, engine):
        self._e = engine
        self.id = engine.chain_id()
        self.name = engine.name
        self.native = engine.native_symbol
        self.explorer = engine.explorer()
        self.usdc = engine.stable_address
        self.evm = engine.evm
        self.enabled = True


def verify_bridge_tx(*a, **k) -> dict:
    """Tron has no tx signing, so there is nothing to verify. Returning a
    structured refusal keeps /bridge/{id}/confirm from raising."""
    return {"ok": False, "error": "no_tx_signing_on_this_chain",
            "exists": False, "from": None, "to": None, "value": None}


__all__ = [
    "USDC", "STABLE_SYMBOL", "REVENUE_ADDRESS", "EXPLORER",
    "NETWORK_NAME", "CHAIN_ID",
    "decimals", "to_units", "from_units",
    "balance", "balance_at", "is_address", "is_tx_hash",
    "submit_usdc", "confirm_usdc", "send_usdc",
    "sign_authorization", "verify_authorization", "settle_authorization",
    "chains", "get_chain", "Chain", "verify_bridge_tx",
    "reload_profile", "NotSupported",
]