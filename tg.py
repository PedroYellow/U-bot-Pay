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


async def _require_private(update: Update) -> bool:
    """Audit H1: reject all bot commands in groups. The bot's payment
    flow leaks balances and personal info if used in a group chat.
    Returns True if the message should be processed, False if it was
    already replied to with an error."""
    if update.effective_chat and update.effective_chat.type != "private":
        # Silent in groups — don't spam the group with bot errors.
        return False
    return True


async def on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_private(update): return
    u = update.effective_user
    # Round 23: pass address=None. The previous form was upsert_user(...,
    # os.environ.get("UBOT_WALLET_ADDRESS")), which on every /start forced the
    # user's row.address back to the demo wallet, even after the user had
    # linked their real wallet via /code. /start now only registers the
    # user and seeds demo contacts; the wallet address comes from /code,
    # not from /start. Address=None means core.upsert_user leaves an
    # existing address untouched and leaves it None for brand-new users
    # until they link one.
    user = core.upsert_user(u.id, u.full_name, (u.language_code or "en")[:2], None)
    api._seed_for(user["id"])
    await update.message.reply_text(
        "👋 I'm ubotpay. Talk to me — send a voice note or type.\n\n"
        "Try:\n• \"How much do I have?\"\n• \"Send 2000 baht to mom\"\n• \"What invoices do I have?\"\n• \"Pay the VietSupply one\"\n\n"
        "Payments settle on Avalanche in under a second. Big amounts ask your co-signer first.")


async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_private(update): return
    f = await (update.message.voice or update.message.audio).get_file()
    buf = io.BytesIO(); await f.download_to_memory(buf)
    text = await stt(buf.getvalue())
    if not text:
        await update.message.reply_text("(voice transcription needs ELEVENLABS_API_KEY — type it for now)"); return
    await update.message.reply_text(f"🎙 _{text}_", parse_mode="Markdown")
    await handle_text(update, text)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_private(update): return
    await handle_text(update, update.message.text)


async def handle_text(update: Update, text: str):
    try:
        res = await call_turn(str(update.effective_user.id), text)
    except Exception as e:
        # The exception can carry the full request URL, which embeds API_URL and
        # would put an internal hostname in a chat log. Log it, tell the user only
        # that it failed. This is the main text path, so it is the last place any
        # of this was still leaking.
        log.warning("turn call failed for tg %s: %s: %s",
                    update.effective_user.id, type(e).__name__, str(e)[:200])
        await update.message.reply_text(
            "⚠️ Something went wrong on my side. Try again in a moment.")
        return
    lang = (update.effective_user.language_code or "en")[:2]
    await reply_voice_and_text(update, res["say"], lang, _extra(res))


async def on_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE, approve=True):
    if not await _require_private(update): return
    parts = (update.message.text or "").split()
    if len(parts) < 2:
        pend = core.pending_approvals(update.effective_user.id)
        await update.message.reply_text("\n".join(f"{p['id']}: {p['amount_usdc']} USDC → {p['contact_alias']}" for p in pend) or "Nothing pending."); return
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(f"{API_URL}/approve", json={"payment_id": parts[1], "approver_key": str(update.effective_user.id), "approve": approve},
                         headers={"x-api-key": API_KEY})
    if r.status_code != 200:
        log.warning("telegram /approve -> %s: %s", r.status_code, r.text[:200])
        await update.message.reply_text(
            "⚠️ That didn't work — the payment may already have been approved, "
            "or you may not be its co-signer."); return
    d = r.json()
    await update.message.reply_text(("✅ Approved & settled in %ss\n%s" % (d.get("latency_s"), d.get("explorer"))) if approve else "❌ Rejected.", disable_web_page_preview=True)


async def on_reject(update, ctx):
    if not await _require_private(update): return
    await on_approve(update, ctx, approve=False)


async def on_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Link this Telegram chat to a wallet using the OTP code shown
    in the ubotpay.com "Link Telegram" banner. Audit C3: never paste
    JWT tokens in chat. The code expires in 10 minutes.

    Usage: /code <6-digit>
    """
    # Audit 20 Oct round 13: this was the only handler without the private-chat
    # guard. Not directly exploitable — the endpoint still requires the 6-digit
    # OTP and the bot's API key — but in a group the user types the code in
    # public, it is echoed into the group transcript, and the bot replies with
    # linking instructions to everyone there. A secret typed in the wrong place is
    # the problem to prevent, not just the one this guard would stop.
    if not await _require_private(update): return
    parts = (update.message.text or "").split()
    # Round 21 bumped create_link_code to 7 digits; the bot has to accept both
    # so a fresh code from the UI is usable, while an older 6-digit one in
    # flight is not refused.
    if len(parts) < 2 or not parts[1].isdigit() or len(parts[1]) not in (6, 7):
        await update.message.reply_text(
            "🔗 *Link this chat to your ubotpay wallet*\n\n"
            "1. Open https://ubotpay.com and connect your wallet\n"
            "2. Click the 'Link Telegram' button — it gives you a 6 or 7-digit code\n"
            "3. Come back here and send:  `/code <digits>`\n\n"
            "Codes expire in 10 minutes.",
            parse_mode="Markdown")
        return
    code = parts[1]
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(f"{API_URL}/auth/link-telegram/confirm",
                               headers={"x-api-key": API_KEY},
                               json={"code": code, "telegram_id": update.effective_user.id})
        if r.status_code != 200:
            # The body can carry internal detail (hostnames, upstream payloads).
            # Log it here, tell the user only what they can act on.
            log.warning("link-telegram confirm failed %s: %s", r.status_code, r.text[:200])
            await update.message.reply_text(
                "⚠️ That code didn't work. It may have expired (they last 10 "
                "minutes) or already been used — request a new one on "
                "ubotpay.com and try again."); return
        d = r.json()
        await update.message.reply_text(
            f"✅ Linked! Your wallet is now connected to this chat.\n"
            f"You'll get a Telegram message every time you do a cross-chain bridge.",
            parse_mode="Markdown")
    except Exception as e:
        log.warning("telegram /code failed: %s: %s", type(e).__name__, e)
        await update.message.reply_text(
            "⚠️ Couldn't reach the server. Try again in a moment.")


# Backward-compat: old /link command returns a friendly deprecation
async def on_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Deprecated. Use /code <6-digit> instead."""
    if not await _require_private(update): return
    await update.message.reply_text(
        "⚠️ The `/link <token>` command has been retired for security.\n\n"
        "New flow:\n"
        "1. Open https://ubotpay.com → connect wallet\n"
        "2. Click the 'Link Telegram' button\n"
        "3. Copy the 6-digit code and send  `/code <digits>`",
        parse_mode="Markdown")


async def on_cosigner(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/cosigner <telegram_id>  — set who approves big payments.

    Audit 20 Oct round 6: this accepted any integer with no validation, which
    let a user appoint *themselves* as their own co-signer and then approve
    their own high-value payments — defeating the one control the product is
    sold on. Now it refuses self-appointment, rejects garbage, and verifies the
    nominee actually exists as a user here.
    """
    if not await _require_private(update): return
    parts = (update.message.text or "").split()
    me = update.effective_user.id
    user = core.get_user(telegram_id=me)
    if len(parts) < 2 or not user:
        await update.message.reply_text("Usage: /cosigner <telegram_user_id>"); return

    raw = parts[1].lstrip("@").strip()
    if not raw.isdigit():
        await update.message.reply_text("❌ The co-signer id must be numeric.\n"
                                       "Get yours from @userinfobot in Telegram."); return
    try:
        approver_id = int(raw)
    except ValueError:
        await update.message.reply_text("❌ That is not a valid Telegram id."); return

    # The whole point of a second signature is that it comes from someone else.
    if approver_id == me:
        await update.message.reply_text(
            "❌ You can't be your own co-signer.\n"
            "A second approval has to come from a different person — that's the "
            "whole point of the check.\n"
            "Ask whoever should authorise your payments to message @Ubotpaybot "
            "first, then pass their Telegram id here."); return

    nominee = core.get_user(telegram_id=approver_id)
    if not nominee:
        await update.message.reply_text(
            f"⚠️ {approver_id} has never used this bot, so it can't be set as your "
            "co-signer yet.\n"
            "Ask them to send /start to @Ubotpaybot first, then try again."); return
    if nominee["id"] == user["id"]:
        await update.message.reply_text("❌ That account is you."); return

    core.set_policy(user["id"], approver_telegram_id=approver_id)
    name = (nominee.get("name") or "this person").strip()
    await update.message.reply_text(
        f"🔐 Co-signer set to {name} (id {approver_id}).\n"
        f"They can approve with:  /approve <payment_id>")


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
    _app.add_handler(CommandHandler("code", on_code))
    _app.add_handler(CommandHandler("link", on_link))  # deprecated
    _app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    api.NOTIFY = notify
    return _app
