"""
U-bot Pay — intent parsing with Ubot (U-topia's LLM). Turns "mándale 2000 baht a mi madre" into a structured action.
Ubot is the brain. Until the Ubot endpoint is live, a generic LLM fallback keeps the demo running.
Works for text transcripts from any channel (ElevenLabs, Telegram voice→STT, web).
"""
import os, json
import httpx

# Ubot — U-topia's own model. OpenAI-compatible chat endpoint.
UBOT_LLM_URL = os.environ.get("UBOT_LLM_URL", "")          # e.g. https://llm.u-topia.com/v1/chat/completions
UBOT_LLM_KEY = os.environ.get("UBOT_LLM_KEY", "")
UBOT_MODEL = os.environ.get("UBOT_MODEL", "ubot-1")
# Fallback while Ubot is being wired up (any provider; kept out of the product surface)
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "claude-sonnet-4-6")

SYSTEM = """You are the intent parser for ubotpay, the voice payments assistant built for the U-topia ecosystem.
Return ONLY a JSON object. No prose. Detect the user's language (ISO 639-1).

Schema:
{
 "intent": "balance" | "pay" | "invoices" | "pay_invoice" | "history" | "contacts" | "confirm" | "cancel" | "agent_query" | "help" | "smalltalk" | "unknown",
 "lang": "en|es|th|my|vi|km|lo|id|fr|...",
 "amount": number|null,
 "currency": "THB|USD|EUR|VND|MMK|PHP|KHR|LAK|IDR|INR|USDC"|null,
 "recipient": string|null,          // name/alias as said by the user, e.g. "mi madre", "VietSupply", "the Vietnam supplier"
 "memo": string|null,               // short purpose if stated
 "invoice_ref": string|null,        // which invoice ("the Vietnam one", "the first", "inv_1a2b")
 "query": string|null,              // for agent_query: the company/topic to research, e.g. "Sequoia", "Binance Labs"
 "reply": string                    // ONE short natural sentence in the user's language to say back while acting (max 20 words). Never mention JSON.
}

Rules:
- "baht/bath/บาท" → THB. If no currency is said and user is in Thailand context, default THB for small amounts; use USDC if they say usdc/dollars.
- "confirm" = yes/sí/ใช่/ok/confirmo/go ahead. "cancel" = no/cancel/stop/para.
- Money words: "2k" = 2000, "mil" = 1000, "two hundred" = 200.
- Never invent recipients. If unclear, intent "pay" with recipient null and ask in reply.
- "agent_query" = user asks ubotpay to research / run due diligence / ask DataAgent about a company or investor ("research Sequoia", "ask DataAgent about X", "what do you know about Binance Labs", "haz due diligence de X").
"""


async def _ubot(system: str, user: str) -> str:
    """One call to Ubot. Raises on any failure so the caller can fall back."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(UBOT_LLM_URL,
                         headers={"Authorization": f"Bearer {UBOT_LLM_KEY}", "Content-Type": "application/json"},
                         json={"model": UBOT_MODEL, "temperature": 0, "max_tokens": 300,
                               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def _fallback(system: str, user: str) -> str:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    r = await client.messages.create(model=FALLBACK_MODEL, max_tokens=300, system=system,
                                     messages=[{"role": "user", "content": user}])
    return r.content[0].text


async def parse(text: str, context: dict | None = None) -> dict:
    ctx = ""
    if context:
        ctx = "\nContext (contacts known: " + ", ".join(context.get("contacts", [])) + \
              "; open invoices: " + "; ".join(context.get("invoices", [])) + \
              "; awaiting_confirmation: " + str(context.get("awaiting", False)) + ")"
    raw = None
    if UBOT_LLM_URL:
        try:
            raw = await _ubot(SYSTEM, text + ctx)
        except Exception:
            raw = None
    if raw is None:
        raw = await _fallback(SYSTEM, text + ctx)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[1] if "\n" in raw else raw.strip("`")
        raw = raw.replace("json\n", "", 1)
    try:
        return json.loads(raw)
    except Exception:
        return {"intent": "unknown", "lang": "en", "reply": "Sorry, I didn't catch that. Could you repeat?"}


# Spoken replies in the user's language (short, for TTS)
SAY = {
    "en": {"balance": "You have {usdc} USDC, about {local} {ccy}.",
           "quote": "That's {usdc} USDC to {name}, FX {rate}, {fee}. Shall I send it?",
           "sent": "Done. Sent in {lat} seconds.",
           "needs_approval": "That amount needs a second approval. I've asked {approver}. I'll tell you when it's cleared.",
           "approved_sent": "Approved and sent in {lat} seconds.",
           "limit": "That goes over your daily limit of {limit} USDC. Want me to send a smaller amount?",
           "no_contact": "I don't have {name} in your contacts. Who should I send it to?",
           "invoices_none": "You have no open invoices.",
           "invoices": "You have {n} open invoices: {list}",
           "cancelled": "Cancelled.", "nothing": "There's nothing to confirm.",
           "history": "Your last payments: {list}", "agent_paid": "I asked DataAgent and paid it {price} USDC via x402 — settled in {lat} seconds. {summary}",
           "agent_fail": "DataAgent didn't answer: {err}",
           "help": "I can check your balance, send money, read your invoices, and pay them. Just tell me."},
    "es": {"balance": "Tienes {usdc} USDC, unos {local} {ccy}.",
           "quote": "Son {usdc} USDC para {name}, cambio {rate}, {fee}. ¿Lo envío?",
           "sent": "Hecho. Enviado en {lat} segundos.",
           "needs_approval": "Ese importe necesita una segunda aprobación. Se lo he pedido a {approver}. Te aviso cuando esté.",
           "approved_sent": "Aprobado y enviado en {lat} segundos.",
           "limit": "Eso supera tu límite diario de {limit} USDC. ¿Envío una cantidad menor?",
           "no_contact": "No tengo a {name} en tus contactos. ¿A quién se lo envío?",
           "invoices_none": "No tienes facturas pendientes.",
           "invoices": "Tienes {n} facturas pendientes: {list}",
           "cancelled": "Cancelado.", "nothing": "No hay nada que confirmar.",
           "history": "Tus últimos pagos: {list}", "agent_paid": "Le pregunté a DataAgent y le pagué {price} USDC por x402, liquidado en {lat} segundos. {summary}",
           "agent_fail": "DataAgent no respondió: {err}",
           "help": "Puedo ver tu saldo, enviar dinero, leer tus facturas y pagarlas. Dime."},
    "th": {"balance": "คุณมี {usdc} USDC ประมาณ {local} {ccy}",
           "quote": "เท่ากับ {usdc} USDC ให้ {name} อัตรา {rate} {fee} ส่งเลยไหม",
           "sent": "เรียบร้อย ส่งแล้วใน {lat} วินาที",
           "needs_approval": "จำนวนนี้ต้องมีการอนุมัติครั้งที่สอง ฉันขอ {approver} แล้ว จะแจ้งเมื่อผ่าน",
           "approved_sent": "อนุมัติและส่งแล้วใน {lat} วินาที",
           "limit": "เกินวงเงินรายวัน {limit} USDC ส่งจำนวนน้อยกว่านี้ไหม",
           "no_contact": "ไม่มี {name} ในรายชื่อ ส่งให้ใครดี",
           "invoices_none": "ไม่มีใบแจ้งหนี้ค้าง",
           "invoices": "คุณมี {n} ใบแจ้งหนี้ค้าง: {list}",
           "cancelled": "ยกเลิกแล้ว", "nothing": "ไม่มีอะไรให้ยืนยัน",
           "history": "รายการล่าสุด: {list}", "agent_paid": "ฉันถาม DataAgent และจ่าย {price} USDC ผ่าน x402 เสร็จใน {lat} วินาที {summary}",
           "agent_fail": "DataAgent ไม่ตอบ: {err}",
           "help": "ฉันดูยอดเงิน ส่งเงิน อ่านใบแจ้งหนี้ และจ่ายให้ได้ บอกมาได้เลย"},
}


def say(lang: str, key: str, **kw) -> str:
    table = SAY.get(lang, SAY["en"])
    return table.get(key, SAY["en"][key]).format(**kw)
