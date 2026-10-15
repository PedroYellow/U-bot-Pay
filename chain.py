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


def send_usdc(to: str, amount, memo_hash: str | None = None) -> dict:
    """Plain ERC-20 transfer from the U-bot treasury wallet. Returns hash + latency."""
    acct = account()
    to = Web3.to_checksum_address(to)
    t0 = time.time()
    tx = usdc.functions.transfer(to, to_units(amount)).build_transaction({
        "from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": CHAIN_ID, "gas": 90_000, "maxFeePerGas": w3.to_wei(30, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei")})
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    return {"tx": h.hex(), "ok": rcpt.status == 1, "block": rcpt.blockNumber,
            "latency_s": round(time.time() - t0, 2), "explorer": f"{EXPLORER}/tx/{h.hex()}"}


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
