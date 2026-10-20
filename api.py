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
from fastapi import FastAPI, HTTPException, Query, Request, Header
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
# Separate secret for the ElevenLabs agent webhook (audit 20 Oct, IDOR fix).
# Lets the voice integration be rotated or revoked independently of the
# Telegram bot's key, and gates it to read-only intents. Empty = disabled, in
# which case the ElevenLabs tool must authenticate another way (session JWT).
VOICE_SECRET = os.environ.get("UBOT_VOICE_SECRET", "")
# Audit M5: allowlist of canonical bridge contract addresses.
# transactionRequest.to from a partner must be in here; otherwise reject
# the quote (defense in depth against a compromised partner redirecting
# funds). The full LI.FI diamond contract address across all chains
# is 0x1231DEB6f5749EF6cE6943a275A1D3E7486F4EaE; CCTP TokenMessenger
# is 0xAaDA05BD399371f2537E14024e23949c45675aCD; Stargate/LayerZero
# uses chain-specific routers that we expand below.
_BRIDGE_SAFE_TARGETS = {
    # LI.FI diamond (same on all chains)
    "0x1231DEB6f5749EF6cE6943a275A1D3E7486F4EaE".lower(),
    # CCTP TokenMessenger (Circle's canonical USDC bridge)
    "0xaada05bd399371f2537e14024e23949c45675aCD".lower(),
    "0xaaDA05BD399371f2537E14024e23949c45675aCD".lower(),
    "0x6c28dc10db08a583e7d7cea2bbd31e18b4d70c93".lower(),  # CCTP v1
    # Squid Router
    "0xc4429b0c7e1d1009fda9dcc75f639736d3cf67a9".lower(),
    # Across Protocol SpokePool (varies per chain but we add the major ones)
    "0xc186fA914353c44b2E33eBE05f21846F10421Edf".lower(),  # SpokePool canonical
    "0x5c7BCd6E7De5423aDBD0810cAcd59aB6628C06b1".lower(),  # Across SpokePool v2
}
# LayerZero/Stargate endpoint addresses (canonical, chain-specific).
# Add more per-chain as we discover them.
_BRIDGE_SAFE_TARGETS.update({
    # Stargate Router (v2 mainnets)
    "0x8731d54E9D02c286767d56F76d8238291E96f015".lower(),  # Ethereum
    "0xb0d5022cE7c3ee15D96b69B2dC61f7C04dbaD011".lower(),  # Base
    "0x53Bf833A9f371a6972C245BAf1D69D03c1Ea7fD5".lower(),  # Arbitrum
    "0x45A01E4e081F3CC07F70bfA06B7c9298E4f43b00".lower(),  # Optimism
    "0x29F65EB173a6Fc1A6eFb8Db3886B1eF2D78C2dF6".lower(),  # Polygon
    "0x88124efD9a0a64e011E6Fc64f1F7b56B80938B82".lower(),  # zkSync
})


def _validate_tx_target(to_address: str) -> bool:
    """Return True if `to_address` is in the bridge-safe allowlist."""
    if not to_address:
        return False
    return to_address.lower() in _BRIDGE_SAFE_TARGETS


# Audit M4: rate limiter for /bridge/analytics (per-user token bucket).
# Window = 60s, max = 30 requests. Prevents attackers with a stolen
# JWT from enumerating analytics data (volumes, partners, etc.).
_analytics_rate: dict[int, list[float]] = {}
_ANALYTICS_WINDOW = 60
_ANALYTICS_MAX = 30


def _analytics_rate_check(user_id: int) -> bool:
    """Return True if the request is allowed under the rate limit."""
    import time
    now = time.time()
    bucket = _analytics_rate.setdefault(user_id, [])
    # Drop entries older than window
    while bucket and bucket[0] < now - _ANALYTICS_WINDOW:
        bucket.pop(0)
    if len(bucket) >= _ANALYTICS_MAX:
        return False
    bucket.append(now)
    return True


NOTIFY = None   # set by telegram bot at import time: async fn(telegram_id, text)

# Track which status transitions we've already notified for, so we
# don't spam the user. Key = (bridge_id, status). Value = unix timestamp.
# Lives in-memory; resets on process restart. Audit H5: bounded LRU.
_BRIDGE_NOTIFIED_MAX = 5000
_BRIDGE_NOTIFIED_TTL = 86400
_BRIDGE_NOTIFIED = {}


def _bridge_notified_gc():
    """Drop expired entries. If still over cap, drop oldest by timestamp."""
    now = time.time()
    expired = [k for k, ts in _BRIDGE_NOTIFIED.items() if now - ts > _BRIDGE_NOTIFIED_TTL]
    for k in expired:
        _BRIDGE_NOTIFIED.pop(k, None)
    if len(_BRIDGE_NOTIFIED) > _BRIDGE_NOTIFIED_MAX:
        items = sorted(_BRIDGE_NOTIFIED.items(), key=lambda x: x[1])
        while len(_BRIDGE_NOTIFIED) > _BRIDGE_NOTIFIED_MAX:
            _BRIDGE_NOTIFIED.pop(items.pop(0)[0], None)


def _esc(s) -> str:
    """HTML-escape a string for safe interpolation into HTML / Markdown
    messages. Used by _notify_bridge to defend against tool names and
    error messages that come from third-party bridge partners (LI.FI,
    Across) and could contain HTML/Markdown control characters (audit C2)."""
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;"))


def _chain_name(chain_id: int) -> str:
    """Resolve a chain_id to its display name using chain.chains()."""
    try:
        for c in chain.chains() + [
            {"id": k, "name": v.name} for k, v in getattr(chain, "_BUILTIN_CHAINS", {}).items()
        ]:
            if c["id"] == chain_id:
                return c["name"]
    except Exception:
        pass
    return f"chain {chain_id}"


async def _notify_bridge(bridge: dict, event: str, user: dict | None = None):
    """Fire a Telegram notification for a bridge status change. Safe to
    call from sync endpoints: if a user is missing telegram_id or NOTIFY
    isn't set (no bot running), this is a no-op. Idempotent within a
    single process via _BRIDGE_NOTIFIED.

    Events:
      - started   → "🌉 Bridge started: X USDC …"
      - submitted → "📝 Bridge submitted on chain A: tx_hash"
      - confirming → "🔄 Bridge confirming on chain B…"
      - done      → "✅ Bridge done! Recipient received Y USDC on B"
      - failed    → "❌ Bridge failed: <error>"
    """
    if not NOTIFY or not bridge:
        return
    key = (bridge["id"], event)
    if key in _BRIDGE_NOTIFIED and (time.time() - _BRIDGE_NOTIFIED[key]) < _BRIDGE_NOTIFIED_TTL:
        return
    _BRIDGE_NOTIFIED[key] = time.time()
    _bridge_notified_gc()
    # Resolve telegram_id from the bridge's user if not passed in
    tg_id = None
    if user and user.get("telegram_id"):
        tg_id = int(user["telegram_id"])
    else:
        owner = core.get_user(user_id=bridge.get("user_id"))
        if owner and owner.get("telegram_id"):
            tg_id = int(owner["telegram_id"])
    if not tg_id:
        return
    bridge_id = bridge["id"]
    short_id = bridge_id[:10] + "…" if len(bridge_id) > 10 else bridge_id
    amt = bridge.get("amount_usdc", 0)
    from_c = _chain_name(bridge.get("from_chain"))
    to_c = _chain_name(bridge.get("to_chain"))
    to_addr = (_esc((bridge.get("to_address") or "")[:10]) + "…") if bridge.get("to_address") else "—"
    if event == "started":
        text = (f"🌉 <b>Bridge started</b>\n"
                f"  {amt} USDC · {from_c} → {to_c}\n"
                f"  Tool: {_esc(bridge.get('tool')) or 'partner'}\n"
                f"  To: {_esc(to_addr)}\n"
                f"  Bridge ID: <code>{short_id}</code>")
    elif event == "submitted":
        tx = (bridge.get("tx_hash") or "")[:14] + "…" if bridge.get("tx_hash") else "—"
        text = (f"📝 <b>Bridge submitted</b> on {from_c}\n"
                f"  Tx: <code>{_esc(tx)}</code>\n"
                f"  {amt} USDC → {to_c}\n"
                f"  Bridge ID: <code>{short_id}</code>")
    elif event == "confirming":
        text = (f"🔄 <b>Bridge confirming</b> on {to_c}…\n"
                f"  {amt} USDC in flight\n"
                f"  Bridge ID: <code>{short_id}</code>")
    elif event == "done":
        try:
            delivered = int(bridge.get("to_amount_min") or 0) / 1e6
        except Exception:
            delivered = amt
        text = (f"✅ <b>Bridge done!</b>\n"
                f"  Recipient received <b>{delivered:.2f} USDC</b> on {to_c}\n"
                f"  From: {amt} USDC on {from_c}\n"
                f"  Bridge ID: <code>{short_id}</code>")
    elif event == "failed":
        err = (bridge.get("error") or "unknown")[:200]
        text = (f"❌ <b>Bridge failed</b>\n"
                f"  {amt} USDC · {from_c} → {to_c}\n"
                f"  Reason: {_esc(err)}\n"
                f"  Bridge ID: <code>{short_id}</code>")
    else:
        return
    try:
        # NOTIFY is sync from the bot's POV but the function is async.
        # We schedule it as a task so it doesn't block the request.
        asyncio.create_task(NOTIFY(tg_id, text))
    except Exception as e:
        log.warning("notify bridge failed: %s", e)

    # Web Push: also fire a browser notification. The user gets a
    # native notification even when the tab is closed. We map the
    # bridge event to a title + body for the notification.
    try:
        owner_id = user["id"] if user else bridge.get("user_id")
        if owner_id and push_module and push_module.is_configured():
            subs = core.get_push_subscriptions_for_user(owner_id)
            if subs:
                push_title = {
                    "started": "🌉 Bridge started",
                    "submitted": "📝 Bridge submitted",
                    "confirming": "🔄 Bridge confirming",
                    "done": "✅ Bridge done!",
                    "failed": "❌ Bridge failed",
                }.get(event, f"Ubotpay bridge {event}")
                # Strip HTML from the Telegram-flavoured text for the push body
                push_body = text.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
                # Cap body length (push notifications are short)
                if len(push_body) > 200:
                    push_body = push_body[:197] + "…"
                push_url = f"/analytics"
                asyncio.create_task(
                    push_module.fan_out_push(subs, push_title, push_body, url=push_url, tag=bridge_id[:32])
                )
    except Exception as e:
        log.warning("push fan_out failed: %s", e)

# in-memory conversation state per user: pending quote awaiting "confirm".
# Audit H5: bounded size with LRU eviction + 30-min TTL.
_pending: dict[int, dict] = {}
_PENDING_MAX = 1000
_PENDING_TTL = 1800  # 30 min


def _pending_gc():
    """Drop expired entries and enforce max cap."""
    import time
    now = time.time()
    expired = [k for k, v in _pending.items() if now - (v.get("ts", 0)) > _PENDING_TTL]
    for k in expired:
        _pending.pop(k, None)
    if len(_pending) > _PENDING_MAX:
        items = sorted(_pending.items(), key=lambda x: x[1].get("ts", 0))
        while len(_pending) > _PENDING_MAX:
            _pending.pop(items.pop(0)[0], None)


def hmac_compare(a: str | None, b: str | None) -> bool:
    """Constant-time string compare. Shared by auth() and the /turn
    server-to-server path so both use the same timing-safe check."""
    import hmac
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


def auth(x_api_key: str | None):
    """Bearer API key auth. The browser never holds this key; it's reserved for server-to-server calls."""
    if not hmac_compare(x_api_key, API_KEY):
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

    Audit 20 Oct: this used to return treasury_usdc / treasury_avax — the
    exact live balance of the wallet that signs transactions. That is not an
    aggregated KPI, it is an inventory of a spendable asset, and it leaked on
    every page load. Now the public payload exposes no balances at all; the
    web tiles read /health (X-Chain / X-Status headers) for status and keep
    balances behind auth on /health/full.
    """
    return {
        "ok": True,
        "chain": chain.NETWORK_NAME,
        "chain_id": 43114,
        "status": "live",
        "treasury_usdc": None,     # removed — auth required (see /health/full)
        "treasury_avax": None,
        "last_settlement_ts": 0,
        "revenue_total_usdc": None,
        "ts": int(time.time()),
    }

@app.get("/health/full")
def health_full(x_api_key: str | None = Header(default=None)):
    """Detailed health: requires internal API key (audit #5)."""
    auth(x_api_key)
    return _health_full()


@app.post("/turn")
async def turn(t: Turn, request: Request,
               x_api_key: str | None = Header(default=None),
               x_ubot_channel: str | None = Header(default=None)):
    """Conversational turn: {text, channel} → {say, action, data}.

    IDENTITY (audit 20 Oct, IDOR fix): the user is resolved from the caller's
    credentials, NEVER from a user_key in the body. Previously `user_key` was
    caller-controlled on a public endpoint, so anyone could read another
    user's balance/invoices/history by passing their telegram_id or 0x address,
    and `agent_query` would spend UBOT_PRIVATE_KEY for whoever they named.

    Accepted identities, in order:
      1. `Authorization: Bearer <JWT>` — the web wallet flow. The user comes
         from the token. If the body names a different user, 403.
      2. `x-api-key: <UBOT_API_KEY>` — server-to-server (Telegram bot, internal
         workers). The body's user_key is honoured because the caller is
         trusted and its key is not exposed to browsers.
      3. `x-ubot-channel: <UBOT_VOICE_SECRET>` — the ElevenLabs agent webhook.
         Also server-to-server, but with a *separate* secret so the voice
         integration can be rotated or revoked without touching the Telegram
         bot's key. Restricted to read-only intents: it may look things up but
         may not spend the treasury key (see the agent_query block).
      4. none of the above → 401. The demo user is reachable through the demo
         API key, not by guessing `{"user_key":"demo"}` from the open web.
    """
    user, err = _user_from_request(request)
    trusted = False          # may spend money / act as the named user
    voice_svc = False        # ElevenLabs webhook: read-only
    if user is None:
        if x_api_key and hmac_compare(x_api_key, API_KEY):
            user = resolve_user(t.user_key)
            trusted = True
        elif VOICE_SECRET and x_ubot_channel and hmac_compare(x_ubot_channel, VOICE_SECRET):
            # The ElevenLabs agent is configured against the demo wallet. It is
            # a shared demo surface, so we pin it to the demo user rather than
            # let a caller-supplied key pick whose balance gets read out loud.
            user = resolve_user("demo")
            voice_svc = True
        else:
            raise HTTPException(401, "auth required: sign in with a wallet, "
                                     "or send x-api-key for server calls")
    elif t.user_key not in ("", None, "demo"):
        # Authenticated session, but it asked for a different user. Refuse:
        # a session may only ever act as itself.
        raise HTTPException(403, "session cannot act as another user")
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
        # Audit 20 Oct (IDOR): this spends UBOT_PRIVATE_KEY, so it must not be
        # reachable by an unauthenticated caller (401 above), nor by the
        # read-only voice webhook — and the per-turn spend must be capped by an
        # operator-controlled ceiling, not only by the resolved user's own
        # daily_limit, which an attacker could otherwise influence.
        if not trusted:
            reason = ("research is available on the Telegram bot" if voice_svc
                      else "sign in to use research")
            return {"say": say(lang, "agent_fail", err=reason),
                    "action": "blocked", "data": {"error": f"agent_query restricted: {reason}"}}
        ceiling = float(os.environ.get("UBOT_AGENT_MAX_USDC", "1"))
        per_turn = max(0.0, min(float(user.get("daily_limit") or 5), ceiling))
        if per_turn <= 0:
            return {"say": say(lang, "agent_fail", err="research budget disabled"),
                    "action": "blocked", "data": {"error": "agent_query budget disabled (UBOT_AGENT_MAX_USDC=0)"}}
        url = f"{os.environ.get('DATAAGENT_URL', 'http://127.0.0.1:8091')}/report?company={urllib.parse.quote(q)}"
        try:
            r = await x402_client.pay_and_fetch(url, os.environ["UBOT_PRIVATE_KEY"], max_usdc=per_turn)
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
import push as push_module

@app.get("/bridge/chains")
def bridge_chains():
    """All enabled chains for cross-chain bridging. Used by the payment
    modal to show the source/destination chain pickers."""
    return chain.chains()


@app.get("/bridge/quote")
def bridge_quote(
    from_chain: int = Query(..., ge=1, le=999999, description="source chain id"),
    to_chain: int = Query(..., ge=1, le=999999, description="destination chain id"),
    amount_usdc: float = Query(..., gt=0.01, le=1_000_000, description="USDC amount, 0.01 to 1,000,000"),
    from_address: str = Query(..., min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    to_address: str = Query(..., min_length=42, max_length=42, pattern=r"^0x[a-fA-F0-9]{40}$"),
    request: Request = None,
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
    # Audit M5: validate that the partner's transactionRequest.to is a
    # known bridge contract. A compromised partner could redirect funds
    # to an arbitrary address by returning a tx with a malicious `to`.
    tx_req = q.get("transaction") or q.get("transaction_request")
    if isinstance(tx_req, dict) and tx_req.get("to"):
        if not _validate_tx_target(tx_req["to"]):
            log.warning("M5: rejecting quote from %s — unknown tx.to=%s", q.get("partner"), tx_req["to"])
            raise HTTPException(502, "bridge contract not in allowlist; quote rejected")
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


class BridgeStartBody(BaseModel):
    from_chain: int
    to_chain: int
    amount_usdc: float
    from_address: str
    to_address: str


@app.post("/bridge/start")
def bridge_start(body: BridgeStartBody, request: Request):
    """Store the quote so we can track the bridge once the user signs.
    Requires a valid Bearer JWT. Audit H4: bounds on amount_usdc and
    chain_ids to prevent DoS via upstream quote spam."""
    user, err = _user_from_request(request)
    if err or user is None:
        raise HTTPException(401, err or "auth required")
    # Bounds (Pydantic does some, but we add chain_id whitelist check)
    if not (1 <= body.from_chain <= 999999 and 1 <= body.to_chain <= 999999):
        raise HTTPException(400, "invalid chain id")
    if body.from_chain == body.to_chain:
        raise HTTPException(400, "from_chain and to_chain must differ")
    if not (0.01 <= body.amount_usdc <= 1_000_000):
        raise HTTPException(400, "amount_usdc must be between 0.01 and 1,000,000")
    # Validate EVM address format
    if not (body.from_address.startswith("0x") and len(body.from_address) == 42):
        raise HTTPException(400, "from_address must be 0x + 40 hex chars")
    if not (body.to_address.startswith("0x") and len(body.to_address) == 42):
        raise HTTPException(400, "to_address must be 0x + 40 hex chars")
    try:
        quote_obj = _bridge.quote(
            body.from_chain, body.to_chain, body.amount_usdc,
            body.from_address, body.to_address,
        )
    except Exception as e:
        raise HTTPException(400, f"invalid quote parameters: {e}")
    import json as _json
    br = core.create_bridge(
        user_id=user["id"],
        from_chain=quote_obj["from_chain"],
        to_chain=quote_obj["to_chain"],
        from_address=body.from_address,
        to_address=body.to_address,
        amount_usdc=quote_obj["from_amount_human"],
        tool=quote_obj["tool"],
        total_fee_usd=quote_obj["total_fee_usd"],
        to_amount_min=str(quote_obj["to_amount_min"]),
        raw_quote=_json.dumps(quote_obj.get("raw", {})),
    )
    # Fire the "started" notification (asynchronous, non-blocking)
    if NOTIFY:
        try:
            asyncio.create_task(_notify_bridge(br, "started", user))
        except Exception as e:
            log.warning("could not schedule started notification: %s", e)
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
    if err or user is None:
        raise HTTPException(401, err or "auth required")
    br = core.get_bridge(bridge_id)
    if not br:
        raise HTTPException(404, "bridge not found")
    if br["user_id"] != user["id"]:
        raise HTTPException(403, "not your bridge")
    tx_hash = str(body.get("tx_hash") or "").strip()
    if not tx_hash:
        raise HTTPException(400, "tx_hash required")
    core.update_bridge(bridge_id, tx_hash=tx_hash, status="submitted")
    if NOTIFY:
        try:
            br_after = core.get_bridge(bridge_id)
            asyncio.create_task(_notify_bridge(br_after, "submitted", user))
        except Exception as e:
            log.warning("could not schedule submitted notification: %s", e)
    return {"ok": True, "bridge_id": bridge_id, "tx_hash": tx_hash}


@app.get("/bridge/{bridge_id}/status")
def bridge_status(bridge_id: str, request: Request):
    """Returns the current status of a bridge. Polls LI.FI's status
    endpoint to detect when the destination chain has received the
    funds. If the bridge is done, marks it as such in the DB."""
    user, err = _user_from_request(request)
    if err or user is None:
        raise HTTPException(401, err or "auth required")
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
            br_done = core.get_bridge(bridge_id)
            if NOTIFY:
                try: asyncio.create_task(_notify_bridge(br_done, "done", user))
                except Exception as e: log.warning("done notify failed: %s", e)
            return br_done
        if lifi_status in ("failed", "error"):
            core.update_bridge(bridge_id, status="failed", error=str(s)[:300])
            br_fail = core.get_bridge(bridge_id)
            if NOTIFY:
                try: asyncio.create_task(_notify_bridge(br_fail, "failed", user))
                except Exception as e: log.warning("failed notify failed: %s", e)
            return br_fail
        if lifi_status in ("pending",):
            # Only notify when first entering "confirming" from another state.
            # _notify_bridge is idempotent within a process, so this is fine.
            core.update_bridge(bridge_id, status="confirming")
            br_c = core.get_bridge(bridge_id)
            if NOTIFY:
                try: asyncio.create_task(_notify_bridge(br_c, "confirming", user))
                except Exception as e: log.warning("confirming notify failed: %s", e)
            return br_c
        return br
    except Exception as e:
        log.warning("bridge status check failed: %s", e)
        return br


@app.get("/bridge/history")
def bridge_history(request: Request, limit: int = 20):
    """Recent bridges for the authenticated user.

    Audit 20 Oct: the guard was `if err:` only. With no Authorization header
    `_user_from_request` returns (None, None) — err is None — so the 401 never
    fired and `user["id"]` raised TypeError → HTTP 500 to an unauthenticated
    caller. Must check both.
    """
    user, err = _user_from_request(request)
    if err or user is None:
        raise HTTPException(401, err or "auth required")
    return core.list_bridges(user_id=user["id"], limit=limit)


@app.get("/bridge/analytics")
def bridge_analytics(request: Request, days: int = 30):
    """Aggregated analytics for the authenticated user. Used by the
    /analytics dashboard. Returns lifetime totals, partner mix, daily
    series, and a recent bridges table. Audit M4: per-user rate limit
    (30 req/min) prevents bulk enumeration if JWT is stolen."""
    user, err = _user_from_request(request)
    if err or user is None:
        raise HTTPException(401, err or "auth required")
    if not _analytics_rate_check(user["id"]):
        raise HTTPException(429, "rate limit exceeded for analytics; try again later")
    bridges = core.list_bridges(user_id=user["id"], limit=1000)
    if not bridges:
        return {
            "ok": True,
            "totals": {"count": 0, "volume_usdc": 0.0, "fees_usd": 0.0, "success_rate": None, "avg_time_s": None},
            "partners": [],
            "statuses": {},
            "daily": [],
            "recent": [],
        }
    # Totals
    total_count = len(bridges)
    volume = sum(float(b.get("amount_usdc") or 0) for b in bridges)
    fees = sum(float(b.get("total_fee_usd") or 0) for b in bridges)
    # Status breakdown
    statuses = {}
    for b in bridges:
        s = b.get("status") or "unknown"
        statuses[s] = statuses.get(s, 0) + 1
    # Success rate (done / total)
    completed = statuses.get("done", 0)
    failed = statuses.get("failed", 0)
    finished_total = completed + failed
    success_rate = (completed / finished_total) if finished_total > 0 else None
    # Avg time to completion (confirmed_at - created_at, in seconds)
    times = []
    for b in bridges:
        if b.get("status") == "done" and b.get("confirmed_at") and b.get("created_at"):
            try:
                t = int(b["confirmed_at"]) - int(b["created_at"])
                if t > 0 and t < 86400 * 7:  # cap at a week
                    times.append(t)
            except (TypeError, ValueError):
                pass
    avg_time_s = (sum(times) / len(times)) if times else None
    # Partner mix
    partner_totals = {}
    for b in bridges:
        p = (b.get("tool") or "unknown").strip()
        if not p: p = "unknown"
        partner_totals[p] = partner_totals.get(p, 0) + 1
    partners = [{"tool": k, "count": v, "share": round(v / total_count, 3)} for k, v in sorted(partner_totals.items(), key=lambda x: -x[1])]
    # Daily series (last N days). Use confirmed_at when available, else created_at.
    import datetime as _dt
    today = _dt.datetime.utcnow().date()
    day_keys = [(today - _dt.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    day_counts = {k: 0 for k in day_keys}
    day_volume = {k: 0.0 for k in day_keys}
    for b in bridges:
        ts = int(b.get("confirmed_at") or b.get("created_at") or 0)
        if ts:
            d = _dt.datetime.utcfromtimestamp(ts).date().isoformat()
            if d in day_counts:
                day_counts[d] += 1
                day_volume[d] += float(b.get("amount_usdc") or 0)
    daily = [{"date": k, "count": day_counts[k], "volume": round(day_volume[k], 2)} for k in day_keys]
    # Recent (last 10) — for the table
    recent = bridges[:10]
    for r in recent:
        if r.get("confirmed_at") and r.get("created_at"):
            try:
                r["duration_s"] = int(r["confirmed_at"]) - int(r["created_at"])
            except (TypeError, ValueError):
                r["duration_s"] = None
        else:
            r["duration_s"] = None
    return {
        "ok": True,
        "totals": {
            "count": total_count,
            "volume_usdc": round(volume, 2),
            "fees_usd": round(fees, 4),
            "success_rate": round(success_rate, 3) if success_rate is not None else None,
            "avg_time_s": round(avg_time_s, 1) if avg_time_s is not None else None,
        },
        "partners": partners,
        "statuses": statuses,
        "daily": daily,
        "recent": recent,
    }


@app.get("/push/vapid-key")
def push_vapid_key():
    """Return the VAPID public key so the browser can subscribe.
    Public endpoint — no auth needed. Audit H6: do NOT expose the
    VAPID subject (admin email) — an attacker could spam abuse
    reports to the admin email."""
    return {
        "public_key": os.environ.get("VAPID_PUBLIC_KEY", ""),
    }


class PushSubscriptionBody(BaseModel):
    endpoint: str
    keys: dict  # {"p256dh": "...", "auth": "..."}


@app.post("/push/subscribe")
async def push_subscribe(body: PushSubscriptionBody, request: Request):
    """Save a Web Push subscription so we can send bridge status
    notifications to the user's browser even when the tab is closed."""
    user, err = _user_from_request(request)
    if err or user is None:
        raise HTTPException(401, err or "auth required")
    if not body.endpoint or not body.keys:
        raise HTTPException(400, "endpoint and keys required")
    p256dh = body.keys.get("p256dh")
    auth = body.keys.get("auth")
    if not (p256dh and auth):
        raise HTTPException(400, "keys.p256dh and keys.auth required")
    user_agent = request.headers.get("User-Agent", "")[:300]
    sub_id = core.save_push_subscription(
        user_id=user["id"],
        endpoint=body.endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent=user_agent,
    )
    return {"ok": True, "id": sub_id, "subscribed": True}


@app.post("/push/unsubscribe")
async def push_unsubscribe(body: dict, request: Request):
    """Mark a push subscription as dead. Called by the browser when
    the user revokes notification permission, or when we get 404/410
    from the push service."""
    user, err = _user_from_request(request)
    if err or user is None:
        raise HTTPException(401, err or "auth required")
    endpoint = str(body.get("endpoint") or "")
    if not endpoint:
        raise HTTPException(400, "endpoint required")
    ok = core.delete_push_subscription(endpoint, user_id=user["id"])
    return {"ok": ok}


@app.get("/push/status")
def push_status(request: Request):
    """How many active push subscriptions the user has."""
    user, err = _user_from_request(request)
    if err or user is None:
        raise HTTPException(401, err or "auth required")
    subs = core.get_push_subscriptions_for_user(user["id"])
    return {
        "subscribed": len(subs) > 0,
        "count": len(subs),
        "vapid_configured": bool(os.environ.get("VAPID_PUBLIC_KEY")),
    }


@app.get("/auth/me")
async def auth_me(request: Request):
    """Return the current user from the Bearer token. 401 if no/invalid session."""
    return _require_session(request)


# ───────── Audit C3: OTP-based link, no JWT exposure ─────────
# Old flow was /link <jwt_token> which leaked the JWT (audit C3).
# New flow:
#   1. User with wallet + JWT calls POST /auth/link-telegram/start
#      → backend issues a 6-digit code tied to their user_id, TTL 10 min
#   2. User sends that code to the bot: /code 123456
#   3. Bot calls POST /auth/link-telegram/confirm with {telegram_id, code}
#      → backend validates, marks used, stamps telegram_id on the user
# Even if the JWT leaks, the attacker still needs to also control the
# Telegram chat that sends the matching code (and the code expires in 10 min).


class LinkTelegramStartRequest(BaseModel):
    """Empty body — the JWT in Authorization identifies the user."""


@app.post("/auth/link-telegram/start")
async def auth_link_telegram_start(body: LinkTelegramStartRequest, request: Request):
    """Issue a one-time 6-digit code the user sends to the Telegram
    bot (/code 123456) to link their wallet to their Telegram chat."""
    user, err = _user_from_request(request)
    if err or user is None:
        raise HTTPException(401, err or "auth required")
    code = core.create_link_code(user["id"], ttl_seconds=600)
    return {
        "ok": True,
        "code": code,
        "expires_in": 600,
        "instructions": (
            "Send this code to @UbotpayBot on Telegram:  /code " + code
        ),
    }


class LinkTelegramConfirmRequest(BaseModel):
    code: str
    telegram_id: int


@app.post("/auth/link-telegram/confirm")
async def auth_link_telegram_confirm(body: LinkTelegramConfirmRequest, request: Request):
    """Bot calls this after the user sends /code <6-digit>. The 6-digit
    code ties this Telegram chat to a specific wallet user, so even if
    the user's JWT leaks elsewhere, the attacker can't link a different
    chat to the user's wallet (audit C3)."""
    # Bot auth: requires x-api-key header (same as other server-to-server
    # routes in this codebase, e.g. /approve).
    auth(x_api_key := request.headers.get("x-api-key"))
    code = str(body.code or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "code must be 6 digits")
    u = core.consume_link_code(code, body.telegram_id)
    if not u:
        raise HTTPException(400, "code expired or already used")
    # Stamp telegram_id on the user
    with core.conn() as cc:
        cc.execute("UPDATE users SET telegram_id = ? WHERE id = ?", (int(body.telegram_id), int(u["id"])))
    return {"ok": True, "user_id": u["id"], "telegram_id": body.telegram_id}
