"""
U-bot Pay — chain layer (Avalanche Fuji C-Chain, USDC).
- send_usdc(to, amount)           → tx hash, confirmed in ~1s
- balance(address)                → AVAX + USDC
- transfer_with_authorization()   → EIP-3009 (x402 style) for agent-to-agent payments
"""
import os, time, re, threading
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


def submit_usdc(to: str, amount, fee: float = 0.0) -> dict:
    """Sign and broadcast the transfer(s); return the tx_hash(es) immediately.

    Round 25 audit C-2: send_usdc used to wait_for_transaction_receipt
    before returning. If the process died between send and wait, no
    payment row had any tx_hash, and the reaper recycled the row to
    pending_approval — meaning a second /approve would mint a fresh
    nonce and send a SECOND transfer for the same amount. Splitting the
    submit from the confirm lets the caller attach the tx_hash to the
    payment row before the chain confirm completes.

    Round 28 audit H-4: the entire get_transaction_count + _transfer
    sequence runs inside `_treasury_lock`. Without the lock, two
    concurrent callers (/approve, _execute, two retried of either) read
    the same nonce and both sign with it; one of the two transactions is
    rejected by the chain with "nonce too low" and the caller's gas is
    burnt. The lock makes the read-modify-write atomic per-treasury.

    Returns:
      {"tx": "<hex>", "fee_tx": "<hex or None>"}
    The values are 0x-prefixed hex strings. The strings are ready to be
    persisted to payments.tx at this exact point; the receipt comes later
    from confirm_usdc(tx).
    """
    with _treasury_lock:
        acct = account()
        nonce = w3.eth.get_transaction_count(acct.address)
        h = _transfer(acct, to, amount, nonce)
        h_fee = None
        if fee and fee >= 0.01 and REVENUE_ADDRESS:
            h_fee = _transfer(acct, REVENUE_ADDRESS, fee, nonce + 1)
    return {"tx": h.hex(), "fee_tx": h_fee.hex() if h_fee else None,
            "fee_usdc": fee if h_fee else 0.0}


# Round 28 audit H-4: serialise treasury writes. Two concurrent
# callers (/approve and _execute, or two retried of the same caller)
# would otherwise read the same get_transaction_count value and sign
# two transactions with the same nonce, leaking one of the two fees
# on-chain. The lock makes get_transaction_count + _transfer atomic.
_treasury_lock = threading.Lock()


def confirm_usdc(tx: str, fee_tx: str | None = None, timeout: int = 60) -> dict:
    """Wait for a previously submitted transfer to mine and produce a result.

    Mirrors what send_usdc used to return after wait_for_transaction_receipt,
    but only against a known tx_hash. Used by /approve to decide
    settled vs failed even when the previous process died mid-flight.
    """
    t0 = time.time()
    rcpt = w3.eth.wait_for_transaction_receipt(tx, timeout=timeout)
    if fee_tx:
        w3.eth.wait_for_transaction_receipt(fee_tx, timeout=timeout)
    return {
        "tx": tx,
        "ok": rcpt.status == 1,
        "block": rcpt.blockNumber,
        "latency_s": round(time.time() - t0, 2),
        "explorer": f"{EXPLORER}/tx/{tx}",
        "fee_tx": fee_tx,
        "fee_explorer": f"{EXPLORER}/tx/{fee_tx}" if fee_tx else None,
    }


def send_usdc(to: str, amount, memo_hash: str | None = None, fee: float = 0.0) -> dict:
    """Recipient gets `amount` in full. If fee > 0 and a revenue wallet is set, a second transfer
    pays the fee to UBOT_REVENUE_ADDRESS (sent back-to-back, confirmed together).

    Historical convenience wrapper: submit+confirm in one call. The
    /approve and _execute paths now use submit_usdc()/confirm_usdc()
    directly so they can attach the tx_hash to the payments row before
    wait_for_transaction_receipt completes — that closes the round-25
    C-2 double-charge window.
    """
    sub = submit_usdc(to, amount, fee)
    return confirm_usdc(sub["tx"], sub.get("fee_tx"))


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


def verify_authorization(auth: dict, *, expected_payee: str | None = None,
                       min_value: int | None = None) -> tuple[bool, str]:
    """Recover the signer of an EIP-3009 TransferWithAuthorization and
    check the field shapes the contract will require.

    Centralised here so /x402/settle (which only needs x-api-key) cannot
    be used to settle authorisations the contract's TransferWithAuthorization
    would itself reject, only faster. Round 26 audit C-4 fix: prior to
    this round the relayer accepted any auth dict that happened to match
    the field names — the cryptographic check happened on-chain at
    transferWithAuthorization time, but only after gas had been spent.
    Combined with a leaked x-api-key this meant an attacker with a
    stolen-signed auth could move funds with the treasury paying gas.

    Returns (ok, reason). reason is a short string suitable for an HTTP
    400; details are in the log.
    """
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
        if expected_payee is not None:
            if Web3.to_checksum_address(auth["to"]) != Web3.to_checksum_address(expected_payee):
                return False, "wrong payee"
        if min_value is not None:
            if int(auth["value"]) < int(min_value):
                return False, "amount too low"
        now = int(time.time())
        if int(auth["validBefore"]) < now:
            return False, "expired"
        if int(auth["validAfter"]) > now:
            return False, "not yet valid"
        msg = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "TransferWithAuthorization": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validBefore", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                ],
            },
            "primaryType": "TransferWithAuthorization",
            "domain": {
                "name": usdc.functions.name().call(),
                "version": usdc.functions.version().call(),
                "chainId": CHAIN_ID,
                "verifyingContract": USDC,
            },
            "message": {
                "from": Web3.to_checksum_address(auth["from"]),
                "to": Web3.to_checksum_address(auth["to"]),
                "value": int(auth["value"]),
                "validAfter": int(auth["validAfter"]),
                "validBefore": int(auth["validBefore"]),
                "nonce": bytes.fromhex(nonce_hex),
            },
        }
        sig = (bytes.fromhex(auth["r"][2:]) + bytes.fromhex(auth["s"][2:])
               + bytes([int(auth["v"])]))
        signer = Account.recover_message(encode_typed_data(full_message=msg), signature=sig)
        if signer.lower() != auth["from"].lower():
            return False, "bad signature"
        return True, signer
    except Exception as e:
        log.warning("verify_authorization failed: %s: %s", type(e).__name__, str(e)[:160])
        return False, "verify error"


def settle_authorization(auth: dict) -> dict:
    """Submit a signed EIP-3009 authorization (relayer = U-bot wallet pays gas).

    Round 26 audit C-4: every caller MUST go through this function —
    it now verifies shape + signature locally BEFORE spending any gas.
    `verify_authorization` is exposed separately so /report on the
    dataagent side can run the same checks against its own allowlist
    (payee, min_value).
    """
    ok, _why = verify_authorization(auth)
    if not ok:
        # Echo a generic message to the caller; the detail goes to the log
        # so a verifier leak cannot be turned into an oracle.
        log.warning("settle_authorization refused: %s", _why)
        return {"ok": False, "tx": None, "error": "auth rejected", "reason": _why,
                "explorer": None, "latency_s": 0.0}
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
    # ZK / Optimistic L2s (added Phase 8). All use ETH as native gas.
    324:   Chain(324, "zkSync Era", "https://mainnet.era.zksync.io",
                 "0x1d17CBcF0D6D143135aE902365D2E5e2A16538D4", "https://explorer.zksync.io", "ETH"),
    # Sonic (chain 146). Added 20 Oct 2026.
    # USDC address verified on-chain before being written here — the addresses
    # that circulate on the web are wrong. Confirmed by reading the contract:
    #   eth_getCode  -> 1798 bytes of bytecode (not an empty account)
    #   decimals()   -> 6
    #   symbol()     -> "USDC"
    #   totalSupply  -> 110,605,454 USDC in circulation
    # LI.FI quotes Avalanche -> Sonic via Polymer, which is what makes this a
    # bridge endpoint rather than just a name in a dropdown.
    146:   Chain(146, "Sonic", "https://rpc.soniclabs.com",
                 "0x29219dd400f2Bf60E5a23d13Be72B486D4038894", "https://sonicscan.org", "S"),
    # Solana. Not EVM. The id is LI.FI's internal one, since there is no numeric
    # CAIP-2 chain id for Solana and the RPC field is unused for a
    # destination-only chain — reads of this chain's balances are not supported,
    # only bridging into it. Listed so it appears as a destination in the UI.
    1151111081099710: Chain(1151111081099710, "Solana", "",
                 "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "https://solscan.io", "SOL"),
    59144: Chain(59144, "Linea", "https://rpc.linea.build",
                 "0x176211869cA2b568f2A7D4EE941E073a821EE1ff", "https://lineascan.build", "ETH"),
    534352: Chain(534352, "Scroll", "https://rpc.scroll.io",
                 "0x06eFdBFf2a14a7c8E15944D1F4A48F9F95F663A4", "https://scrollscan.com", "ETH"),
    5000:  Chain(5000, "Mantle", "https://rpc.mantle.xyz",
                 "0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9", "https://mantlescan.info", "MNT"),
    # Phase 9 chains (L1 mobile + Worldcoin L2 + Uniswap L2)
    42220: Chain(42220, "Celo", "https://forno.celo.org",
                 "0xcebA9300f2b948710d2653dD7B07f33A8B32118C", "https://celoscan.io", "CELO"),
    480:   Chain(480, "World Chain", "https://worldchain-mainnet.g.alchemy.com/public",
                 "0x79A02482A880bCE3F13e09Da970dC34db4CD24d1", "https://worldscan.org", "ETH"),
    130:   Chain(130, "Unichain", "https://mainnet.unichain.org",
                 "0x078D782b760474a361dDA0AF3839290b0EF57AD6", "https://uniscan.xyz", "ETH"),
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
    """Balance on a specific chain. Returns address, native (AVAX/ETH), and USDC.

    Destination-only chains (Solana) have no JSON-RPC here, so asking for their
    balances would otherwise surface as a confusing web3 address-parsing error.
    Say plainly what is and is not supported.
    """
    c, w3l, _ = _runtime(chain_id)
    if not c.rpc:
        raise ValueError(
            f"{c.name} is a bridge destination only — this app can send to it "
            f"but cannot read balances from it (no JSON-RPC endpoint is "
            f"configured for a non-EVM chain)")
    a = Web3.to_checksum_address(address)
    return {"address": a, "chain": c.name, "chain_id": chain_id,
            "native": c.native, "native_balance": float(Web3.from_wei(w3l.eth.get_balance(a), "ether")),
            "usdc": float(_balance_usdc(w3l, c, a))}


# 32-byte hex, optionally 0x-prefixed. Anything else is not a transaction hash.
_TX_HASH_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


def is_tx_hash(value: str) -> bool:
    """Cheap syntactic check before we spend an RPC round-trip."""
    return bool(value) and bool(_TX_HASH_RE.match(value.strip()))


def verify_bridge_tx(tx_hash: str, from_address: str, chain_id: int,
                     expected_to: str | None = None) -> dict:
    """Confirm a bridge transaction really exists and really came from the user.

    Audit 20 Oct (round 4): /bridge/{id}/confirm used to accept any string as a
    tx_hash. A bogus hash moved the bridge to "submitted", LI.FI never saw it,
    the error was swallowed, and the bridge sat in "submitted" forever while the
    UI claimed the money was on its way.

    Returns a dict with:
        ok       — bool, safe to mark the bridge submitted
        error    — machine-readable reason when ok is False
        value    — the native value sent, in ether units
        to       — the destination of the transaction
    Never raises: an RPC failure returns ok=False with error='rpc', so a flaky
    node can't be mistaken for a fake transaction.
    """
    out = {"ok": False, "error": None, "value": None, "to": None,
           "exists": False, "from": None}
    h = (tx_hash or "").strip()
    if not _TX_HASH_RE.match(h):
        out["error"] = "malformed_tx_hash"
        return out
    if not h.startswith("0x"):
        h = "0x" + h

    try:
        _, w3l, _ = _runtime(chain_id)
    except Exception as e:
        out["error"] = f"unknown_chain:{e}"[:80]
        return out

    try:
        tx = w3l.eth.get_transaction(h)
    except Exception as e:
        # Node unreachable or errored. NOT the same as "tx does not exist".
        out["error"] = "rpc_error"
        out["detail"] = str(e)[:120]
        return out

    if tx is None:
        out["error"] = "tx_not_found"
        return out

    out["exists"] = True
    out["from"] = tx.get("from")
    out["to"] = tx.get("to")
    out["value"] = float(Web3.from_wei(tx.get("value", 0), "ether"))

    # The sender must be the address the bridge was created for, otherwise
    # someone is reporting a transaction that isn't their bridge.
    try:
        expected_from = Web3.to_checksum_address(from_address)
        actual_from = Web3.to_checksum_address(tx.get("from") or "")
    except Exception:
        out["error"] = "bad_address"
        return out

    if actual_from != expected_from:
        out["error"] = "sender_mismatch"
        return out

    # If we know where the USDC is supposed to go, and the tx is a plain ERC-20
    # transfer, the recipient should match too. (Approve-and-bridge flows send
    # to an allowance contract instead, so this is only checked when they differ
    # from the bridge contract we already know about.)
    if expected_to and out["to"]:
        try:
            if Web3.to_checksum_address(out["to"]) != Web3.to_checksum_address(expected_to):
                out["to_mismatch"] = True
        except Exception:
            pass

    out["ok"] = True
    return out

def _balance_usdc(w3l, c, a) -> Decimal:
    """Fetch USDC balance on chain `c` for address `a`.

    Round 24 audit: this used to assign a Contract object to the global _DEC.
    After it ran, the next chain.decimals() returned that Contract object, and
    send_usdc() / to_units() crashed with TypeError on the next payment. The
    the cascade was 'visible' because two unrelated chains share one Python
    process: looking up a Base balance killed Avalanche payments. Fixed by
    binding the contract to a local variable; the global _DEC stays as the
    int it is supposed to be.
    """
    usdc_local = w3l.eth.contract(
        Web3.to_checksum_address(c.usdc),
        abi=[{"name": "decimals", "type": "function", "stateMutability": "view",
              "inputs": [], "outputs": [{"type": "uint8"}]}],
    )
    dec = usdc_local.functions.decimals().call()
    raw = _CHAIN_RUNTIME[c.id][2].functions.balanceOf(a).call()
    return Decimal(raw) / (10 ** dec)
