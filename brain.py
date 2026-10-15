"""
U-bot Pay — intent parsing with Claude. Turns "mándale 2000 baht a mi madre" into a structured action.
Works for text transcripts from any channel (ElevenLabs, Telegram voice→STT, web).
"""
import os, json
from anthropic import AsyncAnthropic

MODEL = os.environ.get("UBOT_MODEL", "claude-sonnet-4-6")

SYSTEM = """You are the intent parser for U-bot, the voice finance assistant of U-topia.
Return ONLY a JSON object. No prose. Detect the user's language (ISO 639-1).

Schema:
{
 "intent": "balance" | "pay" | "invoices" | "pay_invoice" | "history" | "contacts" | "confirm" | "cancel" | "help" | "smalltalk" | "unknown",
 "lang": "en|es|th|my|vi|km|lo|id|fr|...",
 "amount": number|null,
 "currency": "THB|USD|EUR|VND|MMK|PHP|KHR|LAK|IDR|INR|USDC"|null,
 "recipient": string|null,          // name/alias as said by the user, e.g. "mi madre", "VietSupply", "the Vietnam supplier"
 "memo": string|null,               // short purpose if stated
 "invoice_ref": string|null,        // which invoice ("the Vietnam one", "the first", "inv_1a2b")
 "reply": string                    // ONE short natural sentence in the user's language to say back while acting (max 20 words). Never mention JSON.
}

Rules:
- "baht/bath/บาท" → THB. If no currency is said and user is in Thailand context, default THB for small amounts; use USDC if they say usdc/dollars.
- "confirm" = yes/sí/ใช่/ok/confirmo/go ahead. "cancel" = no/cancel/stop/para.
- Money words: "2k" = 2000, "mil" = 1000, "two hundred" = 200.
- Never invent recipients. If unclear, intent "pay" with recipient null and ask in reply.
"""


async def parse(text: str, context: dict | None = None) -> dict:
    client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    ctx = ""
    if context:
        ctx = "\nContext (contacts known: " + ", ".join(context.get("contacts", [])) + \
              "; open invoices: " + "; ".join(context.get("invoices", [])) + \
              "; awaiting_confirmation: " + str(context.get("awaiting", False)) + ")"
    r = await client.messages.create(model=MODEL, max_tokens=300, system=SYSTEM,
                                     messages=[{"role": "user", "content": text + ctx}])
    raw = r.content[0].text.strip()
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
           "quote": "That's {usdc} USDC to {name}, FX {rate}, zero fees. Shall I send it?",
           "sent": "Done. Sent in {lat} seconds.",
           "needs_approval": "That amount needs a second approval. I've asked {approver}. I'll tell you when it's cleared.",
           "approved_sent": "Approved and sent in {lat} seconds.",
           "limit": "That goes over your daily limit of {limit} USDC. Want me to send a smaller amount?",
           "no_contact": "I don't have {name} in your contacts. Who should I send it to?",
           "invoices_none": "You have no open invoices.",
           "invoices": "You have {n} open invoices: {list}",
           "cancelled": "Cancelled.", "nothing": "There's nothing to confirm.",
           "history": "Your last payments: {list}", "help": "I can check your balance, send money, read your invoices, and pay them. Just tell me."},
    "es": {"balance": "Tienes {usdc} USDC, unos {local} {ccy}.",
           "quote": "Son {usdc} USDC para {name}, cambio {rate}, sin comisión. ¿Lo envío?",
           "sent": "Hecho. Enviado en {lat} segundos.",
           "needs_approval": "Ese importe necesita una segunda aprobación. Se lo he pedido a {approver}. Te aviso cuando esté.",
           "approved_sent": "Aprobado y enviado en {lat} segundos.",
           "limit": "Eso supera tu límite diario de {limit} USDC. ¿Envío una cantidad menor?",
           "no_contact": "No tengo a {name} en tus contactos. ¿A quién se lo envío?",
           "invoices_none": "No tienes facturas pendientes.",
           "invoices": "Tienes {n} facturas pendientes: {list}",
           "cancelled": "Cancelado.", "nothing": "No hay nada que confirmar.",
           "history": "Tus últimos pagos: {list}", "help": "Puedo ver tu saldo, enviar dinero, leer tus facturas y pagarlas. Dime."},
    "th": {"balance": "คุณมี {usdc} USDC ประมาณ {local} {ccy}",
           "quote": "เท่ากับ {usdc} USDC ให้ {name} อัตรา {rate} ไม่มีค่าธรรมเนียม ส่งเลยไหม",
           "sent": "เรียบร้อย ส่งแล้วใน {lat} วินาที",
           "needs_approval": "จำนวนนี้ต้องมีการอนุมัติครั้งที่สอง ฉันขอ {approver} แล้ว จะแจ้งเมื่อผ่าน",
           "approved_sent": "อนุมัติและส่งแล้วใน {lat} วินาที",
           "limit": "เกินวงเงินรายวัน {limit} USDC ส่งจำนวนน้อยกว่านี้ไหม",
           "no_contact": "ไม่มี {name} ในรายชื่อ ส่งให้ใครดี",
           "invoices_none": "ไม่มีใบแจ้งหนี้ค้าง",
           "invoices": "คุณมี {n} ใบแจ้งหนี้ค้าง: {list}",
           "cancelled": "ยกเลิกแล้ว", "nothing": "ไม่มีอะไรให้ยืนยัน",
           "history": "รายการล่าสุด: {list}", "help": "ฉันดูยอดเงิน ส่งเงิน อ่านใบแจ้งหนี้ และจ่ายให้ได้ บอกมาได้เลย"},
}


def say(lang: str, key: str, **kw) -> str:
    table = SAY.get(lang, SAY["en"])
    return table.get(key, SAY["en"][key]).format(**kw)
