"""
U-bot Pay — intent parsing with Ubot (U-topia's LLM). Turns "mándale 2000 baht a mi madre" into a structured action.
Ubot is the brain. Until the Ubot endpoint is live, a generic LLM fallback keeps the demo running.
Works for text transcripts from any channel (ElevenLabs, Telegram voice→STT, web).
"""
import os, json, re, logging

# httpx is only needed for the Ubot LLM call. Keep the import optional so the
# local parser and the regression tests run without it (audit 20 Oct: the
# product must not depend on an external LLM being reachable).
try:
    import httpx
except ImportError:      # pragma: no cover - only in stripped environments
    httpx = None

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
    if httpx is None:
        raise RuntimeError("httpx not installed; cannot reach Ubot")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(UBOT_LLM_URL,
                         headers={"Authorization": f"Bearer {UBOT_LLM_KEY}", "Content-Type": "application/json"},
                         json={"model": UBOT_MODEL, "temperature": 0, "max_tokens": 300,
                               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def _fallback(system: str, user: str) -> str:
    """Anthropic fallback. Raises on missing key or provider failure so the
    caller can drop to the local parser instead of 500-ing the turn."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=key)
    r = await client.messages.create(model=FALLBACK_MODEL, max_tokens=300, system=system,
                                     messages=[{"role": "user", "content": user}])
    return r.content[0].text


# ── Local deterministic parser ──────────────────────────────────────────────
# Last line of defence. The whole product must not 500 because an external LLM
# host is unreachable (audit 20 Oct: llm.u-topia.com did not resolve and took
# /turn down at 100%). This covers the intents that matter for a payment demo
# without any network call.

_LANG_HINTS = [
    # Thai
    (("บาท", "ยอด", "ส่ง", "เท่าไหร่", "ขอบคุณ"), "th"),
    # Spanish
    (("cuánto", "cuanto", "tienes", "envía", "envia", "saldo", "factura", "gracias", "pagar"), "es"),
]

_CCY_WORDS = {
    "thb": ("baht", "bath", "บาท", "thb"),
    "usd": ("dollars", "dollar", "usd", "dólares", "dolares"),
    "eur": ("euro", "euros", "eur", "€"),
    "usdc": ("usdc", "usd coin"),
    "vnd": ("vnd", "dong", "đồng"),
    "mmk": ("mmk", "kyat"),
    "php": ("php", "peso filipino"),
    "khr": ("khr", "riel"),
    "lak": ("lak", "kip"),
    "idr": ("idr", "rupiah"),
    "inr": ("inr", "rupees", "rupee"),
}
_CCY_LOCAL = {"THB": "THB", "USD": "USD", "EUR": "EUR", "USDC": "USDC", "VND": "VND",
              "MMK": "MMK", "PHP": "PHP", "KHR": "KHR", "LAK": "LAK", "IDR": "IDR", "INR": "INR"}

_MULT = {"k": 1_000, "m": 1_000_000, "mil": 1_000, "khao": 1_000, "พัน": 1_000}
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "once": 1, "twelve": 12, "twenty": 20,
             "cien": 100, "hundred": 100, "mil": 1000, "thousand": 1000}

_NUM_RE = re.compile(r"(\d[\d,._]*)\s*(k|m|mil)?", re.I)

# Recipient extraction: find a pay/send verb, then take the text after it and
# strip amount / currency / filler tokens. Token-based rather than one big
# regex so it behaves the same in Thai (no word boundaries between words).
_PAY_VERBS = ("send", "pay", "transfer", "envía", "envia", "pagar", "manda",
              "mandar", "transferir", "transferirle", "transferencia", "pagar", "paga",
               "abonar", "abona", "enviar", "ส่ง", "จ่าย", "โอน")
_NOISE_TOKENS = {"to", "a", "la", "el", "los", "las", "para", "por", "favor",
                 "please", "now", "ahora", "de", "the", "please", "usdc", "dollars",
                 "dollar", "usd", "euros", "euro", "baht", "bath", "thb", "บาท",
                 "mil", "k", "ด้วย", "ให้", "ครับ", "ค่ะ", "คะ"}
_NOISE_MULT = {"mil", "khao", "พัน"}


def _detect_lang(text: str) -> str:
    low = text.lower()
    if re.search(r"[\u0e00-\u0e7f]", text):
        return "th"
    for words, lang in _LANG_HINTS:
        if any(w in low for w in words):
            return lang
    return "en"


def _detect_currency(text: str) -> str | None:
    low = text.lower()
    # Longest-first: "usdc" must win over "usd", "baht" over "bat".
    matches: list[tuple[int, str]] = []
    for code, words in _CCY_WORDS.items():
        for w in words:
            i = low.find(w)
            if i != -1:
                matches.append((len(w), _CCY_LOCAL[code.upper()]))
    if not matches:
        return None
    return max(matches)[1]


def _detect_amount(text: str) -> float | None:
    m = _NUM_RE.search(text)
    if m:
        raw = m.group(1).replace(",", "").replace("_", "")
        if "." in raw and raw.count(".") > 1:          # 2.000.000 style
            raw = raw.replace(".", "")
        try:
            n = float(raw)
        except ValueError:
            n = None
        if n is not None:
            suffix = (m.group(2) or "").lower()
            return n * _MULT.get(suffix, 1) if suffix else n
    words = re.findall(r"[a-z]+", text.lower())
    for i, w in enumerate(words):
        if w in _WORD_NUM and i + 1 < len(words) and words[i + 1] in _WORD_NUM:
            return _WORD_NUM[w] * _WORD_NUM[words[i + 1]]
    for w in words:
        if w in _WORD_NUM:
            return float(_WORD_NUM[w])
    return None


def _extract_recipient(text: str) -> str | None:
    """Take the text after a pay/send verb and strip amount/currency/filler.
    Works for Latin and Thai scripts (Thai has no \\b word boundaries, so this
    avoids \\b-delimited regexes)."""
    low = text.lower()
    # Find earliest pay verb position, and at that position the longest verb.
    # Audit 20 Oct round 14: _PAY_VERBS holds both "transfer" and "transferir".
    # They start at the same index, and the loop only kept the first match, so
    # "transferir" was cut after "transfer" and the payee alias became
    # "ir mom". On a longer tie the longer verb wins.
    pos, verb_len = None, 0
    for v in _PAY_VERBS:
        i = low.find(v)
        if i == -1:
            continue
        if pos is None or i < pos or (i == pos and len(v) > verb_len):
            pos, verb_len = i, len(v)
    if pos is None:
        return None
    tail = text[pos + verb_len:]
    # Strip leading/trailing filler and keep the meaningful words.
    words = re.findall(r"[A-Za-z0-9À-ÿ฀-๿'’\-]+", tail)
    kept: list[str] = []
    for w in words:
        wl = w.lower()
        if re.fullmatch(r"[\d,._]+(?:k|m)?", wl):     # a number
            continue
        if wl in _NOISE_MULT:
            continue
        if wl in _NOISE_TOKENS:
            continue
        kept.append(w)
    # Reject single generic words like "money" / "it" that aren't a person/alias.
    generic = {"money", "it", "them", "cash", "transfer", "payment", "funds",
               "เงิน", "it", "all"}
    kept = [w for w in kept if w.lower() not in generic]
    if not kept:
        return None
    return " ".join(kept[:4])          # cap at 4 words for a name/alias


def _local_parse(text: str, context: dict | None = None) -> dict:
    """Deterministic intent parsing — no network, no LLM. Covers the demo
    intents so a dead LLM host degrades to a working product, not a 500."""
    raw = (text or "").strip()
    low = raw.lower()
    lang = _detect_lang(raw)
    ctx = context or {}

    base = {"intent": "unknown", "lang": lang, "amount": None, "currency": None,
            "recipient": None, "memo": None, "invoice_ref": None, "query": None,
            "reply": ""}

    # ── confirm / cancel (checked before pay so "yes, send it" doesn't re-quote)
    # Audit 20 Oct round 14: \b is an ASCII word boundary, so the Thai
    # alternatives could never match on their own — "ใช่" and "ได้" were listed here
    # and silently unreachable, so a Thai user saying yes got nothing. Thai has no
    # word boundaries, which the pay pattern below already accounted for. The Latin
    # alternatives keep \b; the Thai ones are matched on their own.
    if (re.search(r"\b(confirm|confirma|confirmo|confirmar|yes|yeah|yep|y|"
                  r"s[ií]|ok|okay|go ahead|do it|proceed)\b", low)
            or re.search(r"(ใช่|ได้|ส่งเลย)", low)):
        base["intent"] = "confirm"
        base["reply"] = {"en": "On it.", "es": "Voy.", "th": "ได้เลย"}[lang]
        return base
    # Cancellation. Audit 20 Oct round 11: this used to be a bare word list
    # including "no" and "nada", so "qwerty zzz nada" was classified as cancel —
    # a nonsense utterance silently cancelled whatever the user was doing. A
    # bare "no"/"nada" is far too weak a signal on its own: it only counts when
    # it is the whole message, or when it follows the money words we just quoted.
    if (re.search(r"\b(cancel|stop|nope|cancela|cancelar)\b", low)
            or re.search(r"(หยุด|ยกเลิก)", low)) \
       or re.fullmatch(r"(no|nada|nah|nope|ไม่|ไม่เอ็น)\s*[.!]?", low) \
       or re.search(r"\b(no|nada)\b[^.!?]{0,24}\b(send|send it|env[ií]a|envia|do it|go ahead|it|pagar|paga)\b", low):
        base["intent"] = "cancel"
        base["reply"] = {"en": "Cancelled.", "es": "Cancelado.", "th": "ยกเลิกแล้ว"}[lang]
        return base

    # ── balance (no \b around Thai — Thai script has no word boundaries)
    if re.search(r"balance|how much (do i|have|left)|saldo|cu[aá]nto|ยอด|เท่าไหร่|เงินเหลือ|มีเงิน", low):
        base["intent"] = "balance"
        base["reply"] = "…"
        return base

    # ── pay_invoice — must run BEFORE the plain invoices check
    if re.search(r"\b(pay|pagar|paga)\b", low) and re.search(r"invoice|factura|inv_", low):
        base["intent"] = "pay_invoice"
        base["amount"] = _detect_amount(raw)
        base["reply"] = "…"
        return base

    # ── invoices
    if re.search(r"invoice|factura| bills|ใบแจ้งหนี้|ใบเสร็จ", low):
        base["intent"] = "invoices"
        base["reply"] = "…"
        return base

    # ── history
    if (re.search(r"\b(history|transactions?|payments?|mis pagos|historial)\b", low)
            or re.search(r"รายการ", low)):
        base["intent"] = "history"
        base["reply"] = "…"
        return base

    # ── contacts
    if (re.search(r"\b(contacts?|contactos?)\b", low)
            or re.search(r"(รายชื่อ|ผู้ติดต่อ)", low)):
        base["intent"] = "contacts"
        base["reply"] = "…"
        return base

    # ── agent_query
    if re.search(r"\b(research|due diligence|ask (dataagent|about)|what do you know about|"
                 r"investiga|sobre .+|busca info)\b", low):
        base["intent"] = "agent_query"
        q = re.sub(r"^(research|investiga|busca info (about|de)?)\s*", "", low).strip()
        base["query"] = q or None
        base["reply"] = "…"
        return base

    # ── help
    # \b cannot sit against Thai script, so "ช่วยด้วย" never matched on its own.
    if re.search(r"^\s*(help|ayuda)\b", low) or re.search(r"^\s*ช่วยด้วย", low):
        base["intent"] = "help"
        base["reply"] = "…"
        return base

    # ── pay / send (Thai verbs have no \b boundaries)
    # "pagar"/"paga"/"pagarle" were absent while "envia"/"manda" were present:
    # the most obvious Spanish word for this product did not route to pay at all.
    # Latin verbs are word-bounded so "paypal" / "payment" do not read as a
    # payment request; Thai has no word boundaries and is matched on its own.
    # ("send" appeared twice in the alternation before this.)
    # Words are listed explicitly rather than with a \w* tail. "pay\w*" would
    # match "paypal", and because the alternation is ordered, "transfer\w*"
    # would match the "transfer" inside "transferir" first — which not only
    # mis-fires on "paypal" but leaves _extract_recipient() with the leftover
    # "ir mom" as the payee alias.
    # "pay" is guarded with (?!pal) rather than a \w* tail: \b cannot help here
    # because the character after "pay" in "paypal" is itself a word character, so
    # the boundary sits cleanly and "pay" matches inside it.
    if (re.search(r"\b(send|sends|sending|sent|pay(?!pal)|pays|paid|"
                  r"transfer|transfers|transferring|"
                  r"env[ií]a|env[ií]ar|env[ií]ale|envia|enviar|"
                  r"manda|mandar|mándale|"
                  r"transferir|transferirle|transferencia|"
                  r"paga|pagar|pagarle|pago|pagos|abona|abonar)\b", low)
            or re.search(r"(ส่ง|จ่าย|โอน)", low)):
        base["recipient"] = _extract_recipient(raw)
        base["intent"] = "pay"
        base["amount"] = _detect_amount(raw)
        base["currency"] = _detect_currency(raw)
        base["reply"] = "…"
        return base

    # ── smalltalk identity
    if re.search(r"who are you|what are you|your name|quien eres|eres tu|"
                 r"คุณเป็นใคร|ชื่ออะไร", low):
        base["intent"] = "smalltalk"
        base["reply"] = "I'm ubotpay — I move your USDC by voice."
        return base

    return base


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
        except Exception as e:
            # Audit 20 Oct: this used to swallow the error silently, which made
            # an unresolvable LLM host look like a mystery 500. Log it instead.
            logging.getLogger("ubotpay.brain").warning("ubot LLM failed (%s: %s); falling back",
                                                       type(e).__name__, str(e)[:120])
            raw = None
    if raw is None:
        try:
            raw = await _fallback(SYSTEM, text + ctx)
        except Exception as e:
            # No LLM available at all — degrade to the local parser rather than
            # failing the turn. Demo-critical intents still work offline.
            logging.getLogger("ubotpay.brain").warning("anthropic fallback failed (%s: %s); using local parser",
                                                       type(e).__name__, str(e)[:120])
            return _local_parse(text, context)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[1] if "\n" in raw else raw.strip("`")
        raw = raw.replace("json\n", "", 1)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("not a JSON object")
        # Guard: an LLM returning an unknown intent must not blank out the reply.
        parsed.setdefault("intent", "unknown")
        parsed.setdefault("lang", _detect_lang(text))
        if not parsed.get("reply"):
            parsed["reply"] = "…"
        return parsed
    except Exception:
        return _local_parse(text, context)


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
