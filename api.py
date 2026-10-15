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
import httpx
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import core, chain, brain, x402_client
from brain import say

log = logging.getLogger("ubotpay")
logging.basicConfig(level=logging.INFO)
core.init()

app = FastAPI(title="U-bot Pay", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

API_KEY = os.environ.get("UBOT_API_KEY", "")
NOTIFY = None   # set by telegram bot at import time: async fn(telegram_id, text)

# in-memory conversation state per user: pending quote awaiting "confirm"
_pending: dict[int, dict] = {}


def auth(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "bad api key")


def resolve_user(user_key: str) -> dict:
    """user_key = telegram id (int) or 'demo'."""
    if user_key in ("demo", "", None):
        u = core.get_user(telegram_id=0) or core.upsert_user(0, "Demo User", "en", os.environ.get("UBOT_WALLET_ADDRESS"))
        return u
    u = core.get_user(telegram_id=int(user_key))
    if not u:
        raise HTTPException(404, "unknown user; say hi on Telegram first")
    return u


class Turn(BaseModel):
    user_key: str = "demo"
    text: str
    channel: str = "voice"
    lang_hint: str | None = None


@app.get("/health")
def health():
    try:
        b = chain.balance(os.environ["UBOT_WALLET_ADDRESS"])
    except Exception as e:
        b = {"error": str(e)[:80]}
    return {"ok": True, "chain": "avalanche-fuji", "treasury": b, "ts": int(time.time())}


@app.post("/turn")
async def turn(t: Turn, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
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
        b = chain.balance(user["address"] or os.environ["UBOT_WALLET_ADDRESS"])
        local = round(b["usdc"] / core.FX["THB"], 0)
        return {"say": say(lang, "balance", usdc=b["usdc"], local=int(local), ccy="baht" if lang != "th" else "บาท"), "action": "balance", "data": b}

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

    # ── agent-to-agent: U-bot hires DataAgent and pays via x402 ──
    if intent == "agent_query":
        q = (it.get("query") or it.get("recipient") or "").strip()
        if not q:
            return {"say": it.get("reply") or "Which company should I research?", "action": "ask", "data": {}}
        url = f"{os.environ.get('DATAAGENT_URL', 'http://127.0.0.1:8091')}/report?company={httpx.utils.quote(q)}"
        try:
            r = await x402_client.pay_and_fetch(url, os.environ["UBOT_PRIVATE_KEY"], max_usdc=float(user.get("daily_limit") or 5))
        except Exception as e:
            return {"say": say(lang, "agent_fail", err=str(e)[:80]), "action": "failed", "data": {"error": str(e)[:200]}}
        if not r.get("ok"):
            err = (r.get("body") or {}).get("error") or r.get("error") or f"HTTP {r.get('status')}"
            return {"say": say(lang, "agent_fail", err=str(err)[:120]), "action": "failed", "data": r}
        rep = r["body"].get("report", {}); pay = r["body"].get("payment", {})
        summary = rep.get("summary", "")
        if rep.get("score_0_100") is not None: summary += f" Score {rep['score_0_100']}/100."
        core.add_contact(uid, "dataagent", "DataAgent (AI service)", pay.get("payee", ""), "—", is_agent=1)
        return {"say": say(lang, "agent_paid", price=r["price_usdc"], lat=pay.get("latency_s", r["latency_s"]), summary=summary),
                "action": "agent_paid", "data": {"query": q, "report": rep, "payment": pay, "x402": {"price_usdc": r["price_usdc"], "payee": r["payee"], "service": r["service"]}}}

    if intent in ("help", "smalltalk", "unknown"):
        return {"say": it.get("reply") or say(lang, "help"), "action": "none", "data": {}}

    return {"say": say(lang, "help"), "action": "none", "data": {}}


def _stage_payment(user, lang, contact, q, memo, channel, invoice_id=None):
    pol = core.check_policy(user, q["amount_usdc"])
    if not pol["allowed"]:
        return {"say": say(lang, "limit", limit=user["daily_limit"]), "action": "blocked", "data": pol}
    _pending[user["id"]] = {"contact": contact, "quote": q, "memo": memo, "channel": channel,
                            "needs_approval": pol["needs_approval"], "invoice_id": invoice_id, "ts": time.time()}
    return {"say": say(lang, "quote", usdc=q["amount_usdc"], name=contact["name"], rate=q["fx_rate"]),
            "action": "confirm_needed", "data": {"quote": q, "to": contact["name"], "needs_approval": pol["needs_approval"]}}


async def _execute(user, lang, p):
    pay = core.create_payment(user["id"], p["contact"], p["quote"], p["memo"], p["channel"], p["needs_approval"])
    if p["needs_approval"]:
        approver = user.get("approver_telegram_id")
        if NOTIFY and approver:
            asyncio.create_task(NOTIFY(approver, f"🔐 Approval needed\n{user['name']} wants to send {pay['amount_usdc']} USDC to {p['contact']['name']}.\nReply: /approve {pay['id']}  or  /reject {pay['id']}"))
        return {"say": say(lang, "needs_approval", approver="your co-signer"), "action": "pending_approval", "data": pay}
    try:
        r = await asyncio.to_thread(chain.send_usdc, p["contact"]["address"], p["quote"]["amount_usdc"], pay["memo_hash"])
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
    r = await asyncio.to_thread(chain.send_usdc, pay["to_address"], pay["amount_usdc"], pay["memo_hash"])
    core.mark(pay["id"], "settled" if r["ok"] else "failed", r["tx"], r["latency_s"], approved_by=int(a.approver_key))
    if NOTIFY: asyncio.create_task(NOTIFY(user["telegram_id"], f"✅ Approved & sent {pay['amount_usdc']} USDC to {pay['contact_alias']} in {r['latency_s']}s\n{r['explorer']}"))
    return {"ok": True, "status": "settled", **r}


@app.get("/balance/{key}")
def balance(key: str):
    u = resolve_user(key); return chain.balance(u["address"] or os.environ["UBOT_WALLET_ADDRESS"])


@app.get("/history/{key}")
def hist(key: str):
    return core.history(resolve_user(key)["id"], 20)


@app.get("/invoices/{key}")
def inv(key: str):
    return core.open_invoices(resolve_user(key)["id"])


# ───────── x402-style agent payments (EIP-3009) ─────────
class X402Quote(BaseModel):
    service: str
    price_usdc: float

@app.post("/x402/quote")
def x402_quote(q: X402Quote):
    """A service agent announces its price. Returns a 402-like payload the payer agent signs."""
    return JSONResponse(status_code=402, content={"x402": 1, "chain": "avalanche-fuji", "asset": chain.USDC,
                                                  "payTo": os.environ["UBOT_WALLET_ADDRESS"], "amount_usdc": q.price_usdc,
                                                  "scheme": "eip3009-transferWithAuthorization", "service": q.service})

class X402Settle(BaseModel):
    authorization: dict

@app.post("/x402/settle")
async def x402_settle(s: X402Settle):
    r = await asyncio.to_thread(chain.settle_authorization, s.authorization)
    return r


class AgentQuery(BaseModel):
    company: str
    user_key: str = "demo"

@app.post("/agent/query")
async def agent_query(q: AgentQuery, x_api_key: str | None = Header(default=None)):
    """U-bot hires DataAgent: GET → 402 → sign EIP-3009 → retry with X-PAYMENT → report + on-chain receipt."""
    auth(x_api_key)
    url = f"{os.environ.get('DATAAGENT_URL', 'http://127.0.0.1:8091')}/report?company={httpx.utils.quote(q.company)}"
    return await x402_client.pay_and_fetch(url, os.environ["UBOT_PRIVATE_KEY"])


@app.post("/demo/seed")
def seed(x_api_key: str | None = Header(default=None)):
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
    if not core.open_invoices(uid):
        core.add_invoice(uid, "VietSupply Co.", demo, 1240.0, "Invoice #VS-2041 packaging", 5)
        core.add_invoice(uid, "Somchai Restaurant", demo, 86.5, "Team dinner 12 Oct", 3)
        core.add_invoice(uid, "Bangkok Office Rent", demo, 2600.0, "October rent", 9)
    core.set_policy(uid, daily_limit=5000, dual_threshold=2000)
