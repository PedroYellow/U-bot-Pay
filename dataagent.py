"""
DataAgent — a third-party AI service that charges per request via x402 (HTTP 402 + EIP-3009 on Avalanche).
Runs standalone on :8091. Payee wallet = DATAAGENT_ADDRESS (any address; it only receives).

Flow:
  GET /report?company=X                     → 402 + payment requirements (JSON + X-PAYMENT-REQUIRED header)
  GET /report?company=X  X-PAYMENT: <b64>   → verifies EIP-3009 authorization, settles it on-chain via the
                                              relayer (U-bot treasury pays gas), returns the report + tx.
"""
import os, json, base64, time, hashlib, logging, asyncio, sqlite3, threading
from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_typed_data
from anthropic import AsyncAnthropic

import chain  # reuse U-bot chain layer (same USDC, same RPC)

log = logging.getLogger("dataagent")
PRICE_USDC = float(os.environ.get("DATAAGENT_PRICE_USDC", "0.5"))
PAYEE = Web3.to_checksum_address(os.environ.get("DATAAGENT_ADDRESS", "0x000000000000000000000000000000000000dEaD"))
AGENT_ID_8004 = os.environ.get("DATAAGENT_ERC8004_ID", "unregistered")

app = FastAPI(title="DataAgent (x402)", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── nonce store ────────────────────────────────────────────────────────────
# Audit 20 Oct round 8: nonces used to live in a module-level set, so every
# restart wiped the replay protection. An EIP-3009 authorization stays valid
# until validBefore, so any authorization captured in that window could be
# replayed after a deploy or a crash. The set is now backed by SQLite, with the
# in-memory set kept as a hot cache in front of it.
#
# The nonce is recorded AFTER settlement on purpose: if settlement fails the
# authorization is not burned, so the payer can retry with a fresh one. The
# chain's own ERC-20 nonce is what actually prevents a double settle.
_NONCE_DB = os.environ.get("DATAAGENT_NONCE_DB",
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataagent_nonces.db"))
_seen_nonces: set[str] = set()
_nonce_lock = threading.Lock()


def _init_nonce_store() -> None:
    with sqlite3.connect(_NONCE_DB) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS seen_nonces(
                        nonce TEXT PRIMARY KEY,
                        seen_at INTEGER NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_nonce_seen ON seen_nonces(seen_at)")


def _nonce_seen(nonce: str) -> bool:
    with _nonce_lock:
        if nonce in _seen_nonces:
            return True
    with sqlite3.connect(_NONCE_DB) as c:
        row = c.execute("SELECT 1 FROM seen_nonces WHERE nonce = ?", (nonce,)).fetchone()
    if row:
        with _nonce_lock:
            _seen_nonces.add(nonce)
        return True
    return False


def _remember_nonce(nonce: str) -> None:
    """Persist a nonce. Returns False if another worker got there first, which
    means the authorization was replayed and must not be settled."""
    now = int(time.time())
    with _nonce_lock:
        _seen_nonces.add(nonce)
    with sqlite3.connect(_NONCE_DB) as c:
        cur = c.execute("INSERT OR IGNORE INTO seen_nonces(nonce, seen_at) VALUES(?, ?)", (nonce, now))
        first_come = cur.rowcount == 1
    # Nonces older than the longest authorization we issue are dead weight.
    try:
        with sqlite3.connect(_NONCE_DB) as c:
            c.execute("DELETE FROM seen_nonces WHERE seen_at < ?", (now - 7 * 86400,))
    except Exception:
        pass
    return first_come


def _forget_nonce(nonce: str) -> None:
    """Release a claimed nonce after settlement failed, so the payer can retry
    with the same authorization instead of it being burned."""
    with _nonce_lock:
        _seen_nonces.discard(nonce)
    try:
        with sqlite3.connect(_NONCE_DB) as c:
            c.execute("DELETE FROM seen_nonces WHERE nonce = ?", (nonce,))
    except Exception as e:
        log.warning("could not release nonce: %s", e)


_init_nonce_store()


def requirements(resource: str) -> dict:
    return {"x402Version": 1, "accepts": [{
        "scheme": "exact", "network": chain.NETWORK_NAME, "asset": chain.USDC, "payTo": PAYEE,
        "maxAmountRequired": str(chain.to_units(PRICE_USDC)), "amount_usdc": PRICE_USDC,
        "resource": resource, "description": f"DataAgent company report ({PRICE_USDC} USDC per query)",
        "mimeType": "application/json", "maxTimeoutSeconds": 60,
        "extra": {"name": chain.usdc.functions.name().call(), "version": chain.usdc.functions.version().call(),
                  "settlement": "eip3009-transferWithAuthorization", "agentIdentity": AGENT_ID_8004}}]}


def verify(auth: dict) -> tuple[bool, str]:
    """Recover signer of the EIP-712 TransferWithAuthorization and check fields."""
    try:
        if Web3.to_checksum_address(auth["to"]) != PAYEE: return False, "wrong payee"
        if int(auth["value"]) < chain.to_units(PRICE_USDC): return False, "amount too low"
        if int(auth["validBefore"]) < time.time(): return False, "expired"
        if _nonce_seen(auth["nonce"]): return False, "nonce reused"
        msg = {"types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                          {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
                         "TransferWithAuthorization": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                                                       {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
                                                       {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}]},
               "primaryType": "TransferWithAuthorization",
               "domain": {"name": chain.usdc.functions.name().call(), "version": chain.usdc.functions.version().call(),
                          "chainId": chain.CHAIN_ID, "verifyingContract": chain.USDC},
               "message": {"from": auth["from"], "to": auth["to"], "value": int(auth["value"]), "validAfter": int(auth["validAfter"]),
                           "validBefore": int(auth["validBefore"]), "nonce": bytes.fromhex(auth["nonce"][2:])}}
        sig = bytes.fromhex(auth["r"][2:]) + bytes.fromhex(auth["s"][2:]) + bytes([int(auth["v"])])
        signer = Account.recover_message(encode_typed_data(full_message=msg), signature=sig)
        if signer.lower() != auth["from"].lower(): return False, "bad signature"
        return True, signer
    except Exception as e:
        # Audit 20 Oct round 8: the message used to interpolate the recovered
        # address, telling an attacker which address a forged signature
        # actually belonged to. Keep the reason generic; the detail is in the log.
        log.warning("verify failed: %s: %s", type(e).__name__, str(e)[:160])
        return False, "verify error"


async def make_report(company: str) -> dict:
    """The actual paid service: a short investor-style brief.

    Audit 20 Oct round 11: the no-key path existed but a *broken* key did not.
    An ANTHROPIC_API_KEY that is present but invalid (truncated, expired, wrong
    account) raised straight out of `messages.create`, which propagated to a
    500 — after settlement had already taken the 0.5 USDC. The payer got an
    error and no report. Falling back to the canned brief keeps the contract:
    if we took the money, we answer.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"company": company, "summary": f"{company}: demo report (set ANTHROPIC_API_KEY for a live one).", "risk": "n/a"}
    try:
        c = AsyncAnthropic(api_key=key)
        r = await c.messages.create(model=os.environ.get("UBOT_MODEL", "claude-sonnet-4-6"), max_tokens=350,
            messages=[{"role": "user", "content": f"You are DataAgent, a paid due-diligence API. Give a 4-line investor brief on '{company}' as JSON with keys: summary (1 sentence), thesis_fit (1 sentence), red_flags (list of max 3 short strings), score_0_100 (int). Return ONLY JSON. If you don't know the company, say so honestly in summary and score 0."}])
        txt = r.content[0].text.strip().strip("`")
        if txt.startswith("json"): txt = txt[4:]
        try:
            return {"company": company, **json.loads(txt)}
        except Exception:
            return {"company": company, "summary": txt[:300]}
    except Exception as e:
        # Settled already. Answer anyway rather than 500 on a paid request.
        log.warning("report generation failed (%s: %s); serving the canned brief",
                    type(e).__name__, str(e)[:160])
        return {"company": company,
                "summary": f"{company}: live brief unavailable ({type(e).__name__}); demo report.",
                "risk": "n/a", "degraded": True}


@app.get("/")
def root(request: Request):
    if "text/html" in request.headers.get("accept", "") and os.path.exists("/var/www/ubotpay/agent.html"):
        return FileResponse("/var/www/ubotpay/agent.html")
    return {"service": "DataAgent", "price_usdc": PRICE_USDC, "payTo": PAYEE, "protocol": f"x402 / EIP-3009 on {chain.NETWORK_NAME}",
            "erc8004": AGENT_ID_8004, "try": "GET /report?company=Sequoia"}


@app.get("/report")
async def report(request: Request, company: str = "Unknown", x_payment: str | None = Header(default=None)):
    resource = str(request.url)
    if not x_payment:
        req = requirements(resource)
        return JSONResponse(status_code=402, content=req, headers={"X-PAYMENT-REQUIRED": base64.b64encode(json.dumps(req).encode()).decode()})
    try:
        payload = json.loads(base64.b64decode(x_payment))
        auth = payload.get("payload", {}).get("authorization", payload.get("authorization", payload))
    except Exception:
        return JSONResponse(status_code=400, content={"error": "malformed X-PAYMENT"})
    ok, why = verify(auth)
    if not ok:
        return JSONResponse(status_code=402, content={"error": why, **requirements(resource)})
    # Claim the nonce BEFORE touching the chain, atomically. Audit 20 Oct round 8:
    # the old code added the nonce to an in-memory set only after settle() returned,
    # so two concurrent requests with the same authorization both passed verify()
    # and both settled. INSERT OR IGNORE makes exactly one of them the winner.
    # If settlement then fails we roll the claim back, so the payer is not charged
    # for a report they never got.
    if not _remember_nonce(auth["nonce"]):
        return JSONResponse(status_code=402, content={"error": "nonce reused", **requirements(resource)})
    try:
        settle = await asyncio.to_thread(chain.settle_authorization, auth)
    except Exception as e:
        _forget_nonce(auth["nonce"])
        log.warning("settlement failed, nonce released: %s: %s", type(e).__name__, str(e)[:160])
        return JSONResponse(status_code=502, content={"error": "settlement failed, please retry"})
    rep = await make_report(company)
    receipt = {"payer": auth["from"], "payee": PAYEE, "amount_usdc": PRICE_USDC, **settle}
    return JSONResponse(content={"report": rep, "payment": receipt},
                        headers={"X-PAYMENT-RESPONSE": base64.b64encode(json.dumps(receipt).encode()).decode()})


@app.get("/health")
def health(x_api_key: str | None = Header(default=None)):
    """Liveness plus the public pricing info.

    Audit 20 Oct round 8: this used to return payee_usdc — the exact live
    balance of the wallet that receives payments — with no auth at all. That is
    the same class of leak as the treasury exposure fixed in /dashboard, and it
    contradicted it. The balance now requires the key.
    """
    out = {"ok": True, "network": chain.NETWORK_NAME, "payee": PAYEE, "price_usdc": PRICE_USDC}
    if x_api_key and x_api_key == os.environ.get("DATAAGENT_API_KEY", ""):
        try:
            out["payee_usdc"] = chain.balance(PAYEE)["usdc"]
        except Exception:
            out["payee_usdc"] = None
    return out
