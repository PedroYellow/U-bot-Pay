"""
DataAgent — a third-party AI service that charges per request via x402 (HTTP 402 + EIP-3009 on Avalanche).
Runs standalone on :8091. Payee wallet = DATAAGENT_ADDRESS (any address; it only receives).

Flow:
  GET /report?company=X                     → 402 + payment requirements (JSON + X-PAYMENT-REQUIRED header)
  GET /report?company=X  X-PAYMENT: <b64>   → verifies EIP-3009 authorization, settles it on-chain via the
                                              relayer (U-bot treasury pays gas), returns the report + tx.
"""
import os, json, base64, time, hashlib, logging, asyncio
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
_seen_nonces: set[str] = set()


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
        if auth["nonce"] in _seen_nonces: return False, "nonce reused"
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
        if signer.lower() != auth["from"].lower(): return False, f"bad signature (recovered {signer})"
        return True, signer
    except Exception as e:
        return False, f"verify error: {e}"


async def make_report(company: str) -> dict:
    """The actual paid service: a short investor-style brief. LLM if key present, else canned."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return {"company": company, "summary": f"{company}: demo report (set ANTHROPIC_API_KEY for a live one).", "risk": "n/a"}
    c = AsyncAnthropic(api_key=key)
    r = await c.messages.create(model=os.environ.get("UBOT_MODEL", "claude-sonnet-4-6"), max_tokens=350,
        messages=[{"role": "user", "content": f"You are DataAgent, a paid due-diligence API. Give a 4-line investor brief on '{company}' as JSON with keys: summary (1 sentence), thesis_fit (1 sentence), red_flags (list of max 3 short strings), score_0_100 (int). Return ONLY JSON. If you don't know the company, say so honestly in summary and score 0."}])
    txt = r.content[0].text.strip().strip("`")
    if txt.startswith("json"): txt = txt[4:]
    try: return {"company": company, **json.loads(txt)}
    except Exception: return {"company": company, "summary": txt[:300]}


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
    # settle on-chain (relayer pays gas). In production this can be batched / async.
    try:
        settle = await asyncio.to_thread(chain.settle_authorization, auth)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"settlement failed: {str(e)[:160]}"})
    _seen_nonces.add(auth["nonce"])
    rep = await make_report(company)
    receipt = {"payer": auth["from"], "payee": PAYEE, "amount_usdc": PRICE_USDC, **settle}
    return JSONResponse(content={"report": rep, "payment": receipt},
                        headers={"X-PAYMENT-RESPONSE": base64.b64encode(json.dumps(receipt).encode()).decode()})


@app.get("/health")
def health():
    return {"ok": True, "network": chain.NETWORK_NAME, "payee": PAYEE, "payee_usdc": chain.balance(PAYEE)["usdc"], "price_usdc": PRICE_USDC}
