"""
U-bot Pay — Telegram channel. Voice note or text → /turn → reply (text + voice via ElevenLabs TTS if key present).
Also delivers dual-control approvals (/approve, /reject).
Runs inside the same process as the API (see main.py).
"""
import os, io, logging, asyncio, tempfile
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import core, api

log = logging.getLogger("ubot.tg")
ELEVEN = os.environ.get("ELEVENLABS_API_KEY")
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "XrExE9yKIg1WjnnlVkGX")  # Matilda (professional) default; swap for official ubotpay voice
STT_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v1")
API_URL = os.environ.get("UBOT_API_URL", "http://127.0.0.1:8090")
API_KEY = os.environ.get("UBOT_API_KEY", "")


async def stt(audio_bytes: bytes, filename="voice.ogg") -> str | None:
    if not ELEVEN:
        return None
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": ELEVEN},
                         files={"file": (filename, audio_bytes, "audio/ogg")}, data={"model_id": STT_MODEL})
        if r.status_code != 200:
            log.warning("stt %s %s", r.status_code, r.text[:200]); return None
        return r.json().get("text")


async def tts(text: str, lang: str = "en") -> bytes | None:
    if not ELEVEN:
        return None
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}?output_format=opus_48000_64",
                         headers={"xi-api-key": ELEVEN, "Content-Type": "application/json"},
                         json={"text": text, "model_id": os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_v4_turbo"),
                               "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}})
        if r.status_code != 200:
            log.warning("tts %s %s", r.status_code, r.text[:200]); return None
        return r.content


async def call_turn(user_key: str, text: str, channel="telegram") -> dict:
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(f"{API_URL}/turn", json={"user_key": user_key, "text": text, "channel": channel},
                         headers={"x-api-key": API_KEY})
        r.raise_for_status(); return r.json()


async def reply_voice_and_text(update: Update, say: str, lang: str, extra: str | None = None):
    audio = await tts(say, lang)
    if audio:
        await update.message.reply_voice(io.BytesIO(audio), caption=say[:900])
    else:
        await update.message.reply_text(say)
    if extra:
        await update.message.reply_text(extra, disable_web_page_preview=True)


def _extra(res: dict) -> str | None:
    d = res.get("data") or {}
    if res.get("action") == "settled":
        return f"✅ {d.get('amount_usdc')} USDC → {d.get('contact_alias') or ''}\n⚡ {d.get('latency_s')}s on Avalanche\n🔗 {d.get('explorer')}"
    if res.get("action") == "confirm_needed":
        q = d.get("quote", {})
        return f"💱 {q.get('amount_local')} {q.get('local_ccy')} = {q.get('amount_usdc')} USDC (FX {q.get('fx_rate')}, fee 0)\n👉 Say *yes* to send, *no* to cancel." + ("\n🔐 Will require co-signer approval." if d.get("needs_approval") else "")
    if res.get("action") == "balance":
        return f"💰 {d.get('usdc')} USDC · {d.get('avax')} AVAX\n`{d.get('address')}`"
    if res.get("action") == "invoices" and isinstance(d, list) and d:
        return "\n".join(f"• {i['from_name']} — {i['amount_usdc']} USDC — {i['description']}" for i in d)
    return None


async def on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = core.upsert_user(u.id, u.full_name, (u.language_code or "en")[:2], os.environ.get("UBOT_WALLET_ADDRESS"))
    api._seed_for(user["id"])
    await update.message.reply_text(
        "👋 I'm ubotpay. Talk to me — send a voice note or type.\n\n"
        "Try:\n• \"How much do I have?\"\n• \"Send 2000 baht to mom\"\n• \"What invoices do I have?\"\n• \"Pay the VietSupply one\"\n\n"
        "Payments settle on Avalanche in under a second. Big amounts ask your co-signer first.")


async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    f = await (update.message.voice or update.message.audio).get_file()
    buf = io.BytesIO(); await f.download_to_memory(buf)
    text = await stt(buf.getvalue())
    if not text:
        await update.message.reply_text("(voice transcription needs ELEVENLABS_API_KEY — type it for now)"); return
    await update.message.reply_text(f"🎙 _{text}_", parse_mode="Markdown")
    await handle_text(update, text)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await handle_text(update, update.message.text)


async def handle_text(update: Update, text: str):
    try:
        res = await call_turn(str(update.effective_user.id), text)
    except Exception as e:
        await update.message.reply_text(f"⚠️ {str(e)[:120]}"); return
    lang = (update.effective_user.language_code or "en")[:2]
    await reply_voice_and_text(update, res["say"], lang, _extra(res))


async def on_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE, approve=True):
    parts = (update.message.text or "").split()
    if len(parts) < 2:
        pend = core.pending_approvals(update.effective_user.id)
        await update.message.reply_text("\n".join(f"{p['id']}: {p['amount_usdc']} USDC → {p['contact_alias']}" for p in pend) or "Nothing pending."); return
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(f"{API_URL}/approve", json={"payment_id": parts[1], "approver_key": str(update.effective_user.id), "approve": approve},
                         headers={"x-api-key": API_KEY})
    if r.status_code != 200:
        await update.message.reply_text(f"⚠️ {r.text[:200]}"); return
    d = r.json()
    await update.message.reply_text(("✅ Approved & settled in %ss\n%s" % (d.get("latency_s"), d.get("explorer"))) if approve else "❌ Rejected.", disable_web_page_preview=True)


async def on_reject(update, ctx):
    await on_approve(update, ctx, approve=False)


async def on_cosigner(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/cosigner <telegram_id>  — set who approves big payments."""
    parts = (update.message.text or "").split()
    user = core.get_user(telegram_id=update.effective_user.id)
    if len(parts) < 2 or not user:
        await update.message.reply_text("Usage: /cosigner <telegram_user_id>"); return
    core.set_policy(user["id"], approver_telegram_id=int(parts[1]))
    await update.message.reply_text(f"🔐 Co-signer set: {parts[1]}")


async def notify(telegram_id: int, text: str):
    try:
        await _app.bot.send_message(telegram_id, text, disable_web_page_preview=True)
    except Exception:
        log.exception("notify")


_app: Application | None = None


def build() -> Application:
    global _app
    _app = Application.builder().token(os.environ["UBOT_BOT_TOKEN"]).build()
    _app.add_handler(CommandHandler("start", on_start))
    _app.add_handler(CommandHandler("approve", on_approve))
    _app.add_handler(CommandHandler("reject", on_reject))
    _app.add_handler(CommandHandler("cosigner", on_cosigner))
    _app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    api.NOTIFY = notify
    return _app
