"""
U-bot Pay — API. One endpoint does the whole conversation turn so ElevenLabs, Telegram and the web share one brain.

POST /turn            {user_key, text, channel}  → {say, action, data}   ← ElevenLabs webhook tool calls this
POST /approve         {payment_id, approver_key}  → settles a dual-control payment
GET  /balance/{key}, GET /history/{key}, GET /invoices/{key}
POST /x402/quote, POST /x402/settle               ← agent-to-agent (EIP-3009) demo
POST /demo/seed       create demo user + contacts + invoices
GET  /health
"""
import os, time, json, asyncio, logging
import httpx, urllib.parse
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import core, chain, brain, x402_client
from brain import say
from web3 import Web3

log = logging.getLogger("ubotpay")
logging.basicConfig(level=logging.INFO)
core.init()

app = FastAPI(title="U-bot Pay", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

API_KEY = os.environ.get("UBOT_API_KEY")
if not API_KEY:
    raise RuntimeError("UBOT_API_KEY must be configured (security: fail-closed)")
NOTIFY = None   # set by telegram bot at import time: async fn(telegram_id, text)

# in-memory conversation state per user: pending quote awaiting "confirm"
_pending: dict[int, dict] = {}


def auth(x_api_key: str | None):
    """Bearer API key auth. The browser never holds this key; it's reserved for server-to-server calls."""
    import hmac
    if not hmac.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(401, "Unauthorized")


def resolve_user(user_key: str | None, chain_id: int = 43114) -> dict:
    """Resolve a user by:
      1) literal "demo" or None → demo user (telegram_id=0)
      2) an int that exists as telegram_id → that user
      3) an EVM address (0x... 40 hex) → look up by (chain_id, address)
    Creates the wallet user on first sight (chain_id, address).
    """
    if user_key in ("demo", "", None):
        u = core.get_user(telegram_id=0) or core.upsert_user(0, "Demo User", "en", os.environ.get("UBOT_WALLET_ADDRESS"), chain_id)
        return u
    if user_key.isdigit() and (u := core.get_user(telegram_id=int(user_key))):
        return u
    if user_key.startswith("0x") and len(user_key) == 42:
        u = core.get_user_by_wallet(user_key, chain_id)
        if not u:
            u = core.upsert_user_by_wallet(user_key, chain_id)
        return u
    raise HTTPException(404, "unknown user; sign in with Telegram or a wallet first")


class Turn(BaseModel):
    user_key: str = "demo"
    text: str
    channel: str = "voice"
    lang_hint: str | None = None


@app.get("/health")
def health():
    """Public health check — only return status, never operational or treasury details (audit #5).
    Operational info (chain, treasury, revenue) is exposed via response headers
    X-Chain and X-Status so the web console can render a live indicator
    without exposing the JSON payload publicly."""
    return JSONResponse(
        {"ok": True, "ts": int(time.time())},
        headers={"X-Chain": chain.NETWORK_NAME, "X-Status": "live"},
    )

def _health_full():
    try:
        b = chain.balance(os.environ["UBOT_WALLET_ADDRESS"])
    except Exception as e:
        b = {"error": str(e)[:80]}
    return {"ok": True, "chain": chain.NETWORK_NAME, "revenue": core.revenue_stats(), "treasury": b, "ts": int(time.time())}

@app.get("/dashboard")
def dashboard():
    """Public dashboard KPIs (no auth) for the home-page tiles.
    Only exposes aggregated, non-sensitive data:
      - treasury_usdc: USDC balance of the operator's treasury wallet
      - last_settlement_ts: unix timestamp of the last successful payment
      - revenue_total_usdc: total fees collected over the lifetime of the service
      - chain: "avalanche-mainnet"
      - status: "live"
    Does NOT expose wallet addresses, individual transactions, or
    per-user data (audit #5)."""
    try:
        b = chain.balance_at(os.environ["UBOT_WALLET_ADDRESS"], 43114)
    except Exception as e:
        b = {"usdc": 0.0, "native_balance": 0.0, "chain": "avalanche-mainnet", "error": str(e)[:80]}
    rev = core.revenue_stats() if hasattr(core, "revenue_stats") else {}
    last_settlement_ts = rev.get("last_settled_at") or 0
    return {
        "ok": True,
        "chain": chain.NETWORK_NAME,
        "chain_id": 43114,
        "status": "live",
        "treasury_usdc": round(b.get("usdc", 0), 2),
        "treasury_avax": round(b.get("native_balance", 0), 4),
        "last_settlement_ts": last_settlement_ts,
        "revenue_total_usdc": round(rev.get("total_usdc", 0), 2),
        "ts": int(time.time()),
    }

@app.get("/health/full")
def health_full(x_api_key: str | None = Header(default=None)):
    """Detailed health: requires internal API key (audit #5)."""
    auth(x_api_key)
    return _health_full()


@app.post("/turn")
async def turn(t: Turn):
    """Public endpoint for voice/text conversational quotes. /turn does NOT sign or send
    any on-chain transaction — it returns a quote/balance/history/contacts response only.
    The actual on-chain send happens out-of-band via the Telegram bot signer or a future
    user-authenticated path. Rate-limited per user_key in production (audit #1)."""
    # Public — no auth required. See audit finding #1: /turn must not hold a server API key.
    user = resolve_user(t.user_key)
    uid = user["id"]
    contacts = core.list_contacts(uid)
    invoices = core.open_invoices(uid)
    ctx = {"contacts": [f"{c['alias']} ({c['name']})" for c in contacts],
           "invoices": [f"{i['id']}: {i['from_name']} {i['amount_usdc']} USDC {i['description']}" for i in invoices],
           "awaiting": uid in _pending}
    it = await brain.parse(t.text, ctx)
    lang = t.lang_hint or it.get("lang") or user.get("lang") or "en"
    intent = it.get("intent", "unknown")
    log.info("turn uid=%s intent=%s amount=%s ccy=%s rcpt=%s", uid, intent, it.get("amount"), it.get("currency"), it.get("recipient"))

    # ── balance ──
    if intent == "balance":
        addr = user["address"] or os.environ["UBOT_WALLET_ADDRESS"]
        try:
            b = chain.balance_at(addr, t.chain_id)
        except Exception as e:
            log.warning("balance lookup failed: %s", e)
            b = {"usdc": 0.0, "native_balance": 0.0, "chain": "avalanche-mainnet"}
        local_ccy = {"th": "THB", "es": "EUR", "en": "USD"}.get(lang, "USD")
        local = round(b.get("usdc", 0) * core.FX.get(local_ccy, 1.0), 2)
        return {"say": say(lang, "balance", usdc=b.get("usdc", 0), local=local, ccy=local_ccy), "action": "balance", "data": b}

    # ── invoices ──
    if intent == "invoices":
        if not invoices:
            return {"say": say(lang, "invoices_none"), "action": "invoices", "data": []}
        lst = "; ".join(f"{i['from_name']}, {i['amount_usdc']} USDC, {i['description']}" for i in invoices[:5])
        return {"say": say(lang, "invoices", n=len(invoices), list=lst), "action": "invoices", "data": invoices}

    # ── history ──
    if intent == "history":
        h = core.history(uid, 5)
        lst = "; ".join(f"{p['amount_usdc']} USDC to {p['contact_alias']} ({p['status']})" for p in h) or "none"
        return {"say": say(lang, "history", list=lst), "action": "history", "data": h}

    if intent == "contacts":
        return {"say": ", ".join(c["name"] for c in contacts) or "no contacts", "action": "contacts", "data": contacts}

    # ── pay invoice → turn into a pay with recipient = invoice issuer ──
    if intent == "pay_invoice":
        inv = None
        ref = (it.get("invoice_ref") or "").lower()
        for i in invoices:
            if ref and (ref in i["id"].lower() or ref in i["from_name"].lower() or ref in i["description"].lower()):
                inv = i; break
        if not inv and invoices:
            inv = invoices[0] if ("first" in ref or "primera" in ref or not ref) else None
        if not inv:
            return {"say": say(lang, "invoices_none"), "action": "ask", "data": {}}
        contact = {"alias": inv["from_name"].lower(), "name": inv["from_name"], "address": inv["from_address"]}
        q = core.quote(inv["amount_usdc"], "USDC")
        return _stage_payment(user, lang, contact, q, inv["description"], t.channel, invoice_id=inv["id"])

    # ── pay ──
    if intent == "pay":
        if not it.get("recipient"):
            return {"say": it.get("reply") or say(lang, "help"), "action": "ask", "data": {}}
        contact = core.find_contact(uid, it["recipient"])
        if not contact:
            return {"say": say(lang, "no_contact", name=it["recipient"]), "action": "ask", "data": {"unknown_recipient": it["recipient"]}}
        amount, ccy = it.get("amount"), (it.get("currency") or "THB")
        if not amount:
            return {"say": it.get("reply") or "How much?", "action": "ask", "data": {}}
        q = core.quote(float(amount), ccy)
        return _stage_payment(user, lang, contact, q, it.get("memo") or "", t.channel)

    # ── confirm / cancel ──
    if intent == "confirm":
        p = _pending.pop(uid, None)
        if not p:
            return {"say": say(lang, "nothing"), "action": "none", "data": {}}
        return await _execute(user, lang, p)

    if intent == "cancel":
        _pending.pop(uid, None)
        return {"say": say(lang, "cancelled"), "action": "cancelled", "data": {}}

    # ── agent-to-agent: ubotpay hires DataAgent and pays via x402 ──
    if intent == "agent_query":
        q = (it.get("query") or it.get("recipient") or "").strip()
        if not q:
            return {"say": it.get("reply") or "Which company should I research?", "action": "ask", "data": {}}
        url = f"{os.environ.get('DATAAGENT_URL', 'http://127.0.0.1:8091')}/report?company={urllib.parse.quote(q)}"
        try:
            r = await x402_client.pay_and_fetch(url, os.environ["UBOT_PRIVATE_KEY"], max_usdc=float(user.get("daily_limit") or 5))
        except Exception as e:
            return {"say": say(lang, "agent_fail", err=str(e)[:80]), "action": "failed", "data": {"error": str(e)[:200]}}
        if not r.get("ok"):
            err = (r.get("body") or {}).get("error") or r.get("error") or f"HTTP {r.get('status')}"
            return {"say": say(lang, "agent_fail", err=str(err)[:120]), "action": "failed", "data": r}
        rep = r["body"].get("report", {}); pay = r["body"].get("payment", {})
        take = round(r["price_usdc"] * core.AGENT_TAKE_PCT, 4)
        if take >= 0.01 and chain.REVENUE_ADDRESS:
            try:
                tk = await asyncio.to_thread(chain.send_usdc, chain.REVENUE_ADDRESS, take, None, 0.0)
                pay["ubotpay_take_usdc"] = take; pay["take_tx"] = tk.get("explorer")
            except Exception as e:
                log.warning("take-rate transfer failed: %s", e)
        summary = rep.get("summary", "")
        if rep.get("score_0_100") is not None: summary += f" Score {rep['score_0_100']}/100."
        core.add_contact(uid, "dataagent", "DataAgent (AI service)", pay.get("payee", ""), "—", is_agent=1)
        return {"say": say(lang, "agent_paid", price=r["price_usdc"], lat=pay.get("latency_s", r["latency_s"]), summary=summary),
                "action": "agent_paid", "data": {"query": q, "report": rep, "payment": pay, "x402": {"price_usdc": r["price_usdc"], "payee": r["payee"], "service": r["service"]}}}

    if intent in ("help", "smalltalk", "unknown"):
        return {"say": it.get("reply") or say(lang, "help"), "action": "none", "data": {}}

    return {"say": say(lang, "help"), "action": "none", "data": {}}


def _fee_words(q, lang):
    f = q.get("fee_usdc", 0)
    if f < 0.005: return {"en": "no fee", "es": "sin comisión", "th": "ไม่มีค่าธรรมเนียม"}.get(lang, "no fee")
    cents = round(f * 100)
    if f < 1:
        return {"en": f"our fee is {cents} cent{'s' if cents != 1 else ''}", "es": f"comisión {cents} céntimo{'s' if cents != 1 else ''}", "th": f"ค่าธรรมเนียม {cents} เซนต์"}.get(lang, f"fee {f} USDC")
    return {"en": f"our fee is {f} USDC", "es": f"comisión {f} USDC", "th": f"ค่าธรรมเนียม {f} USDC"}.get(lang, f"fee {f} USDC")


@app.get("/pricing")
def pricing():
    """Business model, machine-readable."""
    ex = core.quote(100, "THB")
    return {"model": {"transfer_fee_pct": core.FEE_PCT * 100, "transfer_fee_min_usdc": core.FEE_MIN, "fx_spread_pct": core.SPREAD_PCT * 100,
                      "agent_payment_take_pct": core.AGENT_TAKE_PCT * 100, "bank_benchmark_pct": core.BANK_PCT * 100},
            "example_100_thb": ex, "revenue_wallet": chain.REVENUE_ADDRESS, "stats": core.revenue_stats()}


def _stage_payment(user, lang, contact, q, memo, channel, invoice_id=None):
    pol = core.check_policy(user, q.get("total_usdc", q["amount_usdc"]))
    if not pol["allowed"]:
        return {"say": say(lang, "limit", limit=user["daily_limit"]), "action": "blocked", "data": pol}
    _pending[user["id"]] = {"contact": contact, "quote": q, "memo": memo, "channel": channel,
                            "needs_approval": pol["needs_approval"], "invoice_id": invoice_id, "ts": time.time()}
    return {"say": say(lang, "quote", usdc=q["amount_usdc"], name=contact["name"], rate=q["fx_rate"], fee=_fee_words(q, lang)),
            "action": "confirm_needed", "data": {"quote": q, "to": contact["name"], "needs_approval": pol["needs_approval"]}}


async def _execute(user, lang, p):
    pay = core.create_payment(user["id"], p["contact"], p["quote"], p["memo"], p["channel"], p["needs_approval"])
    if p["needs_approval"]:
        approver = user.get("approver_telegram_id")
        if NOTIFY and approver:
            asyncio.create_task(NOTIFY(approver, f"🔐 Approval needed\n{user['name']} wants to send {pay['amount_usdc']} USDC to {p['contact']['name']}.\nReply: /approve {pay['id']}  or  /reject {pay['id']}"))
        return {"say": say(lang, "needs_approval", approver="your co-signer"), "action": "pending_approval", "data": pay}
    try:
        r = await asyncio.to_thread(chain.send_usdc, p["contact"]["address"], p["quote"]["amount_usdc"], pay["memo_hash"], p["quote"].get("fee_usdc", 0.0))
        core.mark(pay["id"], "settled" if r["ok"] else "failed", r["tx"], r["latency_s"])
        if p.get("invoice_id"): core.pay_invoice(p["invoice_id"], r["tx"])
        return {"say": say(lang, "sent", lat=r["latency_s"]), "action": "settled", "data": {**pay, **r}}
    except Exception as e:
        core.mark(pay["id"], "failed")
        log.exception("send")
        return {"say": f"Payment failed: {str(e)[:80]}", "action": "failed", "data": {"error": str(e)[:200]}}


class Approve(BaseModel):
    payment_id: str
    approver_key: str
    approve: bool = True


@app.post("/approve")
async def approve(a: Approve, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    pay = core.get_payment(a.payment_id)
    if not pay or pay["status"] != "pending_approval":
        raise HTTPException(404, "no such pending payment")
    user = core.get_user(user_id=pay["user_id"])
    if str(user.get("approver_telegram_id")) != str(a.approver_key):
        raise HTTPException(403, "not the approver")
    if not a.approve:
        core.mark(pay["id"], "rejected", approved_by=int(a.approver_key))
        if NOTIFY: asyncio.create_task(NOTIFY(user["telegram_id"], f"❌ Payment {pay['id']} rejected by co-signer."))
        return {"ok": True, "status": "rejected"}
    r = await asyncio.to_thread(chain.send_usdc, pay["to_address"], pay["amount_usdc"], pay["memo_hash"], pay.get("fee_usdc") or 0.0)
    core.mark(pay["id"], "settled" if r["ok"] else "failed", r["tx"], r["latency_s"], approved_by=int(a.approver_key))
    if NOTIFY: asyncio.create_task(NOTIFY(user["telegram_id"], f"✅ Approved & sent {pay['amount_usdc']} USDC to {pay['contact_alias']} in {r['latency_s']}s\n{r['explorer']}"))
    return {"ok": True, "status": "settled", **r}


def _resolve_demo_user():
    """Resolve the demo user. Until per-user sessions are added, the only account
    served is the in-process 'demo' user. Path-supplied keys are ignored (audit #3)."""
    return resolve_user("demo")

@app.get("/balance")
def balance(x_api_key: str | None = Header(default=None), request: Request = None):
    """Balance for the demo user OR for the JWT-authenticated user.
    With a Bearer JWT in the Authorization header, returns the balance
    of the connected wallet (audit #3). Without a JWT, requires the
    internal x-api-key header."""
    if request and request.headers.get("Authorization", "").startswith("Bearer "):
        token = request.headers["Authorization"][7:].strip()
        payload = core.verify_session_jwt(token, JWT_SECRET)
        if payload:
            u = core.get_user(user_id=payload.get("sub"))
            if u and u.get("address"):
                return chain.balance_at(u["address"], payload.get("chain", 43114))
    auth(x_api_key)
    u = _resolve_demo_user()
    return chain.balance(u["address"] or os.environ["UBOT_WALLET_ADDRESS"])

@app.get("/balance/{address}")
def balance_by_address(address: str, chain_id: int = 43114, x_api_key: str | None = Header(default=None)):
    """Public balance lookup for any address on any enabled chain.

    Query params:
      chain_id: 1=ETH, 10=OP, 137=Polygon, 8453=Base, 42161=Arbitrum, 43114=Avalanche.
    Default is 43114 (Avalanche C-Chain) for backward compatibility.

    Returns the native and USDC balance of the given address on the chosen chain.
    No auth required (balances are public on-chain data)."""
    if not Web3.is_address(address):
        raise HTTPException(400, "bad address")
    try:
        return chain.balance_at(Web3.to_checksum_address(address), chain_id)
    except Exception as e:
        ch = chain.get_chain(chain_id) if chain_id != 43114 else None
        return {
            "error": str(e)[:120],
            "address": address,
            "chain": ch.name if ch else "avalanche-mainnet",
            "chain_id": chain_id,
            "usdc": 0.0,
            "native_balance": 0.0,
            "native": ch.native if ch else "AVAX",
        }


@app.get("/history")
def history(x_api_key: str | None = Header(default=None)):
    """History for the demo user (audit #3)."""
    auth(x_api_key)
    return core.history(_resolve_demo_user()["id"], 20)


@app.get("/invoices")
def invoices(x_api_key: str | None = Header(default=None)):
    """Invoices for the demo user (audit #3)."""
    auth(x_api_key)
    return core.open_invoices(_resolve_demo_user()["id"])


# ───────── x402-style agent payments (EIP-3009) ─────────
class X402Quote(BaseModel):
    service: str
    price_usdc: float

@app.post("/x402/quote")
def x402_quote(q: X402Quote, x_api_key: str | None = Header(default=None)):
    """A service agent announces its price. Returns a 402-like payload the payer agent signs. Server-side only."""
    auth(x_api_key)
    return JSONResponse(status_code=402, content={"x402": 1, "chain": chain.NETWORK_NAME, "asset": chain.USDC,
                                                  "payTo": os.environ["UBOT_WALLET_ADDRESS"], "amount_usdc": q.price_usdc,
                                                  "scheme": "eip3009-transferWithAuthorization", "service": q.service})

class X402Settle(BaseModel):
    authorization: dict

@app.post("/x402/settle")
async def x402_settle(s: X402Settle, x_api_key: str | None = Header(default=None)):
    """Settles a signed EIP-3009 authorization. Server-side only."""
    auth(x_api_key)
    r = await asyncio.to_thread(chain.settle_authorization, s.authorization)
    return r


class AgentQuery(BaseModel):
    company: str
    user_key: str = "demo"

@app.post("/agent/query")
async def agent_query(q: AgentQuery, x_api_key: str | None = Header(default=None)):
    """DISABLED for security until per-user authentication and spend caps are in place (audit #2)."""
    auth(x_api_key)  # require server key for any future activation
    raise HTTPException(503, "/agent/query disabled: requires per-user authentication and per-user spend cap. See security audit finding #2.")


@app.post("/demo/seed")
def seed(x_api_key: str | None = Header(default=None)):
    """Demo seed — server-side only. Browser no longer calls this (audit #1)."""
    auth(x_api_key)
    u = core.upsert_user(0, "Demo User", "en", os.environ.get("UBOT_WALLET_ADDRESS"))
    _seed_for(u["id"])
    return {"ok": True, "user": u, "contacts": core.list_contacts(u["id"]), "invoices": core.open_invoices(u["id"])}


def _seed_for(uid):
    demo = os.environ.get("DEMO_RECIPIENT", "0x000000000000000000000000000000000000dEaD")
    core.add_contact(uid, "mom", "Mom (Chiang Mai)", demo, "TH")
    core.add_contact(uid, "madre", "Mamá (Chiang Mai)", demo, "TH")
    core.add_contact(uid, "vietsupply", "VietSupply Co.", demo, "VN")
    core.add_contact(uid, "somchai", "Somchai Restaurant", demo, "TH")
    core.add_contact(uid, "dataagent", "DataAgent (AI service)", demo, "—", is_agent=1)
    if os.environ.get("DEMO_RESET_INVOICES") == "1":
        core.clear_invoices(uid)
    if not core.open_invoices(uid):
        core.add_invoice(uid, "VietSupply Co.", demo, float(os.environ.get("DEMO_INV_1", "5")), "Invoice #VS-2041 packaging", 5)
        core.add_invoice(uid, "Somchai Restaurant", demo, float(os.environ.get("DEMO_INV_2", "3")), "Team dinner 12 Oct", 3)
        core.add_invoice(uid, "Bangkok Office Rent", demo, float(os.environ.get("DEMO_INV_3", "12")), "October rent", 9)
    core.set_policy(uid, daily_limit=float(os.environ.get("DEMO_DAILY_LIMIT", "40")), dual_threshold=float(os.environ.get("DEMO_DUAL_THRESHOLD", "10")))


# ───────── session helpers (Nivel 1+2+3) ─────────
JWT_SECRET = os.environ.get("UBOT_JWT_SECRET", "")
if not JWT_SECRET or JWT_SECRET == "auto":
    import secrets as _s
    JWT_SECRET = _s.token_urlsafe(48)
    os.environ["UBOT_JWT_SECRET"] = JWT_SECRET
    log.warning("UBOT_JWT_SECRET not set — generated a fresh one. Persist it in /opt/ubotpay/.env to keep sessions across restarts.")

def _user_from_request(request: Request) -> tuple[dict | None, str | None]:
    """Resolve the current user from a JWT in `Authorization: Bearer *** `, OR
    the internal x-api-key. Returns (user, error). `error` is set to a 401/403
    reason when the request cannot be served; `user` is None in that case."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        payload = core.verify_session_jwt(token, JWT_SECRET)
        if not payload:
            return None, "invalid or expired session"
        u = core.get_user(user_id=payload.get("sub"))
        if not u:
            return None, "session user not found"
        return u, None
    return None, None  # unauthenticated; caller decides

def _require_session(request: Request) -> dict:
    user, err = _user_from_request(request)
    if err or user is None:
        raise HTTPException(401, err or "auth required")
    return user

# ───────── wallet-based auth (Nivel 1) ─────────
class WalletNonceRequest(BaseModel):
    address: str
    chain_id: int = 43114

class WalletVerifyRequest(BaseModel):
    address: str
    chain_id: int = 43114
    signature: str     # 0x-prefixed 65-byte EIP-191 signature

class WalletVerifyResponse(BaseModel):
    token: str
    user: dict
    chain: dict

@app.post("/auth/wallet/nonce")
async def auth_wallet_nonce(req: WalletNonceRequest):
    """Return a one-shot nonce for the user's wallet to sign. Replay-safe (rotates any previous nonce)."""
    if not Web3.is_address(req.address):
        raise HTTPException(400, "bad address")
    nonce = core.new_wallet_nonce(Web3.to_checksum_address(req.address), req.chain_id)
    return {"nonce": nonce, "expires_in": 600, "chain_id": req.chain_id}

@app.post("/auth/wallet/verify")
async def auth_wallet_verify(req: WalletVerifyRequest):
    """Verify the personal_sign signature, return a 7-day JWT bound to (chain_id, address)."""
    from eth_account.messages import encode_defunct
    if not Web3.is_address(req.address):
        raise HTTPException(400, "bad address")
    cur = core.get_user_by_wallet(req.address, req.chain_id)
    if not cur or not cur.get("jwt_nonce"):
        # No row or no active nonce — the front-end should have called
        # /auth/wallet/nonce first. We could silently re-issue one, but
        # that breaks the case where the user signed the original nonce:
        # the original is now rotated away and the signature fails.
        # Better to fail loudly so the client knows to re-fetch a nonce.
        raise HTTPException(400, "no active nonce — call /auth/wallet/nonce first")
    try:
        msg = encode_defunct(text=cur["jwt_nonce"])
        recovered = Web3().eth.account.recover_message(msg, signature=bytes.fromhex(req.signature.replace("0x", "")))
    except Exception as e:
        raise HTTPException(400, f"bad signature: {e}")
    if recovered.lower() != cur["address"].lower():
        raise HTTPException(401, "signature does not match address")
    # Rotate the nonce so it can't be re-used
    with core.conn() as cc:
        cc.execute("UPDATE users SET jwt_nonce = NULL, jwt_exp = NULL WHERE id = ?", (cur["id"],))
    token = core.issue_session_jwt(cur, JWT_SECRET)
    ch = chain.get_chain(req.chain_id)
    return {"token": token, "user": cur,
            "chain": {"id": ch.id, "name": ch.name, "native": ch.native, "explorer": ch.explorer}}

@app.get("/auth/chains")
async def auth_chains():
    """Public list of supported chains. Used by the wallet selector."""
    return chain.chains()


# ───────── cross-chain bridge (LI.FI) ─────────
import bridge as _bridge

@app.get("/bridge/chains")
def bridge_chains():
    """All enabled chains for cross-chain bridging. Used by the payment
    modal to show the source/destination chain pickers."""
    return chain.chains()


@app.get("/bridge/quote")
def bridge_quote(
    from_chain: int,
    to_chain: int,
    amount_usdc: float,
    from_address: str,
    to_address: str,
    request: Request,
):
    """Get a LI.FI bridge quote for a USDC transfer between two EVM chains.
    Returns the best route, gas estimate, fees, time, and the raw
    transactionRequest that the user will sign."""
    try:
        # partner=lifi|across|best. best tries all partners and picks the highest output.
        partner = request.query_params.get("partner", "best")
        if partner == "lifi":
            q = _bridge.quote(from_chain, to_chain, amount_usdc, from_address, to_address)
        elif partner == "across":
            q = _bridge.quote_across(from_chain, to_chain, amount_usdc, from_address, to_address)
            if q is None:
                raise HTTPException(502, "across did not return a quote")
        else:  # best
            q = _bridge.get_best_quote(from_chain, to_chain, amount_usdc, from_address, to_address)
    except Exception as e:
        log.warning("bridge quote failed: %s", e)
        raise HTTPException(502, f"bridge quote failed: {e}")
    raw = q.pop("raw", {})
    return {
        "ok": True,
        "tool": q["tool"],
        "from_chain": q["from_chain"],
        "to_chain": q["to_chain"],
        "from_amount": str(q["from_amount"]),
        "from_amount_human": q["from_amount_human"],
        "to_amount": str(q["to_amount"]),
        "to_amount_human": q["to_amount_human"],
        "to_amount_min": str(q["to_amount_min"]),
        "to_amount_min_human": q["to_amount_min_human"],
        "gas_cost_usd": q["gas_cost_usd"],
        "bridge_fee_usd": q["bridge_fee_usd"],
        "total_fee_usd": q["total_fee_usd"],
        "execution_duration": q["execution_duration"],
        "approval_address": q["approval_address"],
        "transaction": raw.get("transactionRequest") or {},
        "raw_id": raw.get("id"),
        "partner": q.get("partner", "lifi"),
        "all_partner_quotes": q.get("all_partner_quotes", []),
    }


@app.get("/bridge/partners")
def bridge_partners():
    """Health and reliability stats for each bridge partner. Used by
    the UI to show which partner gave the quote. The success_rate
    is over the lifetime of this process — it resets on restart."""
    return _bridge.partner_stats()


@app.get("/bridge/alerts")
def bridge_alerts():
    """Returns a list of partners that are currently unhealthy. A partner
    is considered unhealthy if its success rate has dropped below 50%
    OR if the most recent 5 attempts all failed. Use this to trigger
    ops alerts."""
    stats = _bridge.partner_stats()
    alerts = []
    for name, s in stats.items():
        if s["last_fail"] and s["last_fail"] > s["last_ok"]:
            alerts.append({
                "partner": name,
                "reason": "most recent attempt failed",
                "last_ok": s["last_ok"],
                "last_fail": s["last_fail"],
            })
        elif s["success_rate"] is not None and s["success_rate"] < 0.5:
            alerts.append({
                "partner": name,
                "reason": f"success rate is {s['success_rate']:.0%}, below 50%",
            })
    return {"alerts": alerts, "count": len(alerts)}


@app.post("/bridge/start")
def bridge_start(body: dict, request: Request):
    """Store the quote so we can track the bridge once the user signs.
    Requires a valid Bearer JWT."""
    user, err = _user_from_request(request)
    if err:
        raise HTTPException(401, err)
    try:
        quote_obj = _bridge.quote(
            int(body["from_chain"]),
            int(body["to_chain"]),
            float(body["amount_usdc"]),
            str(body["from_address"]),
            str(body["to_address"]),
        )
    except Exception as e:
        raise HTTPException(400, f"invalid quote parameters: {e}")
    import json as _json
    br = core.create_bridge(
        user_id=user["id"],
        from_chain=quote_obj["from_chain"],
        to_chain=quote_obj["to_chain"],
        from_address=str(body["from_address"]),
        to_address=str(body["to_address"]),
        amount_usdc=quote_obj["from_amount_human"],
        tool=quote_obj["tool"],
        total_fee_usd=quote_obj["total_fee_usd"],
        to_amount_min=str(quote_obj["to_amount_min"]),
        raw_quote=_json.dumps(quote_obj.get("raw", {})),
    )
    return {"ok": True, "bridge_id": br["id"], "quote": {
        "tool": quote_obj["tool"],
        "to_amount": str(quote_obj["to_amount"]),
        "to_amount_human": quote_obj["to_amount_human"],
        "to_amount_min_human": quote_obj["to_amount_min_human"],
        "total_fee_usd": quote_obj["total_fee_usd"],
        "execution_duration": quote_obj["execution_duration"],
    }}


@app.post("/bridge/{bridge_id}/confirm")
def bridge_confirm(bridge_id: str, body: dict, request: Request):
    """Called by the frontend after the user signs and broadcasts the
    bridge transaction. Stores the tx_hash and starts the polling loop."""
    user, err = _user_from_request(request)
    if err:
        raise HTTPException(401, err)
    br = core.get_bridge(bridge_id)
    if not br:
        raise HTTPException(404, "bridge not found")
    if br["user_id"] != user["id"]:
        raise HTTPException(403, "not your bridge")
    tx_hash = str(body.get("tx_hash") or "").strip()
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    core.update_bridge(bridge_id, tx_hash=tx_hash, status="submitted")
    return {"ok": True, "bridge_id": bridge_id, "tx_hash": tx_hash}


@app.get("/bridge/{bridge_id}/status")
def bridge_status(bridge_id: str, request: Request):
    """Returns the current status of a bridge. Polls LI.FI's status
    endpoint to detect when the destination chain has received the
    funds. If the bridge is done, marks it as such in the DB."""
    user, err = _user_from_request(request)
    if err:
        raise HTTPException(401, err)
    br = core.get_bridge(bridge_id)
    if not br:
        raise HTTPException(404, "bridge not found")
    if br["user_id"] != user["id"]:
        raise HTTPException(403, "not your bridge")
    if br["status"] in ("done", "failed"):
        return br
    if not br.get("tx_hash"):
        return br
    try:
        s = _bridge.get_status(br["tx_hash"])
        lifi_status = (s.get("status") or "").lower()
        receiving = s.get("receiving") or {}
        done_hash = receiving.get("txHash") if isinstance(receiving, dict) else None
        if lifi_status in ("done", "completed", "success") or done_hash:
            core.update_bridge(bridge_id, status="done", confirmed_at=int(time.time()))
            return core.get_bridge(bridge_id)
        if lifi_status in ("failed", "error"):
            core.update_bridge(bridge_id, status="failed", error=str(s)[:300])
            return core.get_bridge(bridge_id)
        if lifi_status in ("pending",):
            core.update_bridge(bridge_id, status="confirming")
            return core.get_bridge(bridge_id)
        return br
    except Exception as e:
        log.warning("bridge status check failed: %s", e)
        return br


@app.get("/bridge/history")
def bridge_history(request: Request, limit: int = 20):
    """Recent bridges for the authenticated user."""
    user, err = _user_from_request(request)
    if err:
        raise HTTPException(401, err)
    return core.list_bridges(user_id=user["id"], limit=limit)


@app.get("/auth/me")
async def auth_me(request: Request):
    """Return the current user from the Bearer token. 401 if no/invalid session."""
    return _require_session(request)
