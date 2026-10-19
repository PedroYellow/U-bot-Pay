"""
U-bot Pay — chain layer (Avalanche Fuji C-Chain, USDC).
- send_usdc(to, amount)           → tx hash, confirmed in ~1s
- balance(address)                → AVAX + USDC
- transfer_with_authorization()   → EIP-3009 (x402 style) for agent-to-agent payments
"""
import os, time
from decimal import Decimal
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_typed_data

RPC = os.environ.get("AVAX_RPC", "https://api.avax-test.network/ext/bc/C/rpc")
CHAIN_ID = int(os.environ.get("AVAX_CHAIN_ID", "43113"))
USDC = Web3.to_checksum_address(os.environ.get("USDC_ADDRESS", "0x5425890298aed601595a70AB815c96711a31Bc65"))
EXPLORER = os.environ.get("EXPLORER", "https://testnet.snowtrace.io")
NETWORK_NAME = "avalanche-mainnet" if CHAIN_ID == 43114 else "avalanche-fuji"

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"name": "a", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint8"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable", "inputs": [{"name": "to", "type": "address"}, {"name": "v", "type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"name": "transferWithAuthorization", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"}, {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"}, {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"},
                {"name": "v", "type": "uint8"}, {"name": "r", "type": "bytes32"}, {"name": "s", "type": "bytes32"}], "outputs": []},
    {"name": "name", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "version", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]},
]

w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 20}))
usdc = w3.eth.contract(address=USDC, abi=ERC20_ABI)
_DEC = None


def decimals() -> int:
    global _DEC
    if _DEC is None:
        _DEC = usdc.functions.decimals().call()
    return _DEC


def to_units(amount) -> int:
    return int(Decimal(str(amount)) * (10 ** decimals()))


def from_units(v: int) -> Decimal:
    return Decimal(v) / (10 ** decimals())


def balance(address: str) -> dict:
    a = Web3.to_checksum_address(address)
    return {"address": a,
            "avax": float(Web3.from_wei(w3.eth.get_balance(a), "ether")),
            "usdc": float(from_units(usdc.functions.balanceOf(a).call()))}


def account():
    return Account.from_key(os.environ["UBOT_PRIVATE_KEY"])


REVENUE_ADDRESS = os.environ.get("UBOT_REVENUE_ADDRESS")  # where fees go (optional)


def _transfer(acct, to, amount, nonce):
    tx = usdc.functions.transfer(Web3.to_checksum_address(to), to_units(amount)).build_transaction({
        "from": acct.address, "nonce": nonce, "chainId": CHAIN_ID, "gas": 90_000,
        "maxFeePerGas": w3.to_wei(30, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei")})
    return w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)


def send_usdc(to: str, amount, memo_hash: str | None = None, fee: float = 0.0) -> dict:
    """Recipient gets `amount` in full. If fee > 0 and a revenue wallet is set, a second transfer
    pays the fee to UBOT_REVENUE_ADDRESS (sent back-to-back, confirmed together)."""
    acct = account()
    t0 = time.time()
    nonce = w3.eth.get_transaction_count(acct.address)
    h = _transfer(acct, to, amount, nonce)
    h_fee = None
    if fee and fee >= 0.01 and REVENUE_ADDRESS:
        h_fee = _transfer(acct, REVENUE_ADDRESS, fee, nonce + 1)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    if h_fee: w3.eth.wait_for_transaction_receipt(h_fee, timeout=60)
    return {"tx": h.hex(), "ok": rcpt.status == 1, "block": rcpt.blockNumber,
            "latency_s": round(time.time() - t0, 2), "explorer": f"{EXPLORER}/tx/{h.hex()}",
            "fee_tx": h_fee.hex() if h_fee else None, "fee_explorer": f"{EXPLORER}/tx/{h_fee.hex()}" if h_fee else None, "fee_usdc": fee if h_fee else 0.0}


# ───────── EIP-3009 / x402-style: payer signs offline, payee (or anyone) submits ─────────
def sign_authorization(payer_key: str, to: str, amount, valid_seconds=600) -> dict:
    acct = Account.from_key(payer_key)
    nonce = os.urandom(32)
    now = int(time.time())
    msg = {
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                             {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
            "TransferWithAuthorization": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                                          {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
                                          {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}]},
        "primaryType": "TransferWithAuthorization",
        "domain": {"name": usdc.functions.name().call(), "version": usdc.functions.version().call(),
                   "chainId": CHAIN_ID, "verifyingContract": USDC},
        "message": {"from": acct.address, "to": Web3.to_checksum_address(to), "value": to_units(amount),
                    "validAfter": 0, "validBefore": now + valid_seconds, "nonce": nonce}}
    sig = acct.sign_message(encode_typed_data(full_message=msg))
    return {"from": acct.address, "to": Web3.to_checksum_address(to), "value": to_units(amount),
            "validAfter": 0, "validBefore": now + valid_seconds, "nonce": "0x" + nonce.hex(),
            "v": sig.v, "r": "0x" + sig.r.to_bytes(32, "big").hex(), "s": "0x" + sig.s.to_bytes(32, "big").hex()}


def settle_authorization(auth: dict) -> dict:
    """Submit a signed EIP-3009 authorization (relayer = U-bot wallet pays gas)."""
    acct = account()
    t0 = time.time()
    tx = usdc.functions.transferWithAuthorization(
        auth["from"], auth["to"], int(auth["value"]), int(auth["validAfter"]), int(auth["validBefore"]),
        bytes.fromhex(auth["nonce"][2:]), int(auth["v"]), bytes.fromhex(auth["r"][2:]), bytes.fromhex(auth["s"][2:])
    ).build_transaction({"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address), "chainId": CHAIN_ID,
                         "gas": 120_000, "maxFeePerGas": w3.to_wei(30, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei")})
    h = w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    return {"tx": h.hex(), "ok": rcpt.status == 1, "latency_s": round(time.time() - t0, 2), "explorer": f"{EXPLORER}/tx/{h.hex()}"}

# ───────── multi-chain registry (Nivel 2 / 3) ─────────
# Mantiene compatibilidad con todo el código existente (default Avalanche C-Chain).
# Una chain = (chain_id, rpc, usdc_address, explorer, name, native_currency).
# Las funciones `send_usdc` y `balance` aceptan chain_id opcional; sin él, usan
# las globales de Avalanche.

from dataclasses import dataclass

@dataclass(frozen=True)
class Chain:
    id: int
    name: str
    rpc: str
    usdc: str
    explorer: str
    native: str = "AVAX"     # ETH on EVM chains, MATIC on Polygon, etc.
    evm: bool = True
    enabled: bool = True     # turn on/off per chain without code change

# Built-in registry. To enable a new chain, just add it here.
_BUILTIN_CHAINS = {
    43114: Chain(43114, "Avalanche C-Chain", os.environ.get("AVAX_RPC", "https://api.avax.network/ext/bc/C/rpc"),
                 os.environ.get("USDC_ADDRESS", "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"),
                 os.environ.get("EXPLORER", "https://snowtrace.io"), "AVAX"),
    43113: Chain(43113, "Avalanche Fuji (testnet)", os.environ.get("AVAX_RPC", "https://api.avax-test.network/ext/bc/C/rpc"),
                 os.environ.get("USDC_ADDRESS", "0x5425890298aed601595a70AB815c96711a31Bc65"),
                 os.environ.get("EXPLORER", "https://testnet.snowtrace.io"), "AVAX"),
    8453:  Chain(8453, "Base", "https://mainnet.base.org",
                 "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "https://basescan.org", "ETH"),
    137:   Chain(137, "Polygon", "https://polygon-rpc.com",
                 "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", "https://polygonscan.com", "MATIC"),
    42161: Chain(42161, "Arbitrum One", "https://arb1.arbitrum.io/rpc",
                 "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "https://arbiscan.io", "ETH"),
    1:     Chain(1, "Ethereum", "https://eth.merkle.io",
                 "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "https://etherscan.io", "ETH"),
    10:    Chain(10, "Optimism", "https://mainnet.optimism.io",
                 "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", "https://optimistic.etherscan.io", "ETH"),
}

def chains() -> list[dict]:
    """Public list of enabled chains for the wallet selector. Excludes disabled entries."""
    return [{"id": c.id, "name": c.name, "native": c.native, "explorer": c.explorer}
            for c in _BUILTIN_CHAINS.values() if c.enabled]

def get_chain(chain_id: int = 43114) -> Chain:
    """Resolve a chain by id, default Avalanche. Raises if unknown or disabled."""
    c = _BUILTIN_CHAINS.get(chain_id)
    if not c:
        raise ValueError(f"unknown chain_id={chain_id}")
    if not c.enabled:
        raise ValueError(f"chain {c.name} is registered but disabled")
    return c

# Cache of Web3/contract instances per chain_id, so RPC nodes aren't reset between calls.
_CHAIN_RUNTIME: dict[int, tuple] = {}

def _runtime(chain_id: int) -> tuple:
    if chain_id in _CHAIN_RUNTIME:
        return _CHAIN_RUNTIME[chain_id]
    c = get_chain(chain_id)
    w3_local = Web3(Web3.HTTPProvider(c.rpc, request_kwargs={"timeout": 20}))
    usdc_local = w3_local.eth.contract(Web3.to_checksum_address(c.usdc), abi=ERC20_ABI)
    _CHAIN_RUNTIME[chain_id] = (c, w3_local, usdc_local)
    return _CHAIN_RUNTIME[chain_id]

def balance_at(address: str, chain_id: int = 43114) -> dict:
    """Balance on a specific chain. Returns address, native (AVAX/ETH), and USDC."""
    c, w3l, _ = _runtime(chain_id)
    a = Web3.to_checksum_address(address)
    return {"address": a, "chain": c.name, "chain_id": chain_id,
            "native": c.native, "native_balance": float(Web3.from_wei(w3l.eth.get_balance(a), "ether")),
            "usdc": float(_balance_usdc(w3l, c, a))}

def _balance_usdc(w3l, c, a) -> Decimal:
    global _DEC
    _DEC = w3l.eth.contract(Web3.to_checksum_address(c.usdc), abi=[{"name":"decimals","type":"function","stateMutability":"view","inputs":[],"outputs":[{"type":"uint8"}], "constant":True}])
    dec = _DEC.functions.decimals().call()
    raw = _CHAIN_RUNTIME[c.id][2].functions.balanceOf(a).call()
    return Decimal(raw) / (10 ** dec)
