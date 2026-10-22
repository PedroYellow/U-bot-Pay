"""
treasury_watch.py — poll the treasury USDC balance and notify on arrivals.

Why polling rather than a webhook provider: the operator asked for a
"no external service, check every 5 minutes" alert, and a payment
arriving in the same 5-minute window as another one still registers
as a delta — which is fine for a treasury that mostly receives one
payment at a time.

What counts as an arrival: the treasury USDC balance increases by more
than UBOT_TREASURY_WATCH_MIN_DELTA (default 0.01 USDC). Decreases are
ignored entirely — those are our own settlements and fees.

Baseline handling:
  - On first run the current balance is written to the state file and no
    notification fires. A restart does not re-announce yesterday's
    money.
  - A decrease larger than UBOT_TREASURY_WATCH_DROP_TOLERANCE
    (default 5% of the last-seen balance) is logged as a warning.
    A large drop that is not one of our own payments means something
    else moved funds.

State file: /opt/ubotpay/treasury_watch.json, written with mode 0600.
It holds only a number (the last-seen balance) and a timestamp —
no keys, no addresses.

Notifications: direct HTTP POST to the Telegram Bot API. Cheap,
idempotent, no dependency on the python-telegram-bot Application
instance. No personal data, no API keys in the message body.
"""
import json
import logging
import os
import threading
import time

import requests

log = logging.getLogger("ubotpay.treasury_watch")

STATE_PATH = os.environ.get(
    "UBOT_TREASURY_WATCH_STATE", "/opt/ubotpay/treasury_watch.json")
TREASURY = os.environ.get("UBOT_WALLET_ADDRESS", "")
POLL_SECONDS = int(os.environ.get("UBOT_TREASURY_WATCH_INTERVAL", "300"))
MIN_DELTA = float(os.environ.get("UBOT_TREASURY_WATCH_MIN_DELTA", "0.01"))
DROP_TOLERANCE = float(
    os.environ.get("UBOT_TREASURY_WATCH_DROP_TOLERANCE", "0.05"))
# Operator's chat_id. Kept as a single value rather than per-user
# because the operator explicitly said "to me only".
OPERATOR_TG_ID = int(os.environ.get("UBOT_OPERATOR_TG_ID", "0"))
# The bot token. Direct HTTP — no dependency on the python-telegram-bot
# Application instance.
TG_BOT_TOKEN = os.environ.get("UBOT_BOT_TOKEN", "")

# Sentinel meaning "no Telegram id configured". We still poll and log,
# but every check is silent on Telegram.
_ENABLED = OPERATOR_TG_ID != 0 and bool(TG_BOT_TOKEN)


def _telegram_send(text):
    """Direct send. Cheap and idempotent. Returns True on success."""
    if not _ENABLED:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": OPERATOR_TG_ID, "text": text,
                  "disable_web_page_preview": True},
            timeout=10)
    except Exception:
        # A failed Telegram call must not break the watcher.
        log.exception("treasury watch: telegram send failed")
        return False
    if r.status_code != 200 or not r.json().get("ok"):
        log.warning("treasury watch: telegram send rejected: %s", r.text[:200])
        return False
    return True


def _notify_default(text):
    """Background-thread notify: just hit Telegram directly."""
    _telegram_send(text)


def _read_state():
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return None


def _write_state(balance):
    """Atomic write: tmp + rename. A crash mid-write leaves the previous
    state in place rather than a half-formed file."""
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"last_balance": balance, "ts": int(time.time())}, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_PATH)


def _fetch_balance():
    import chain
    b = chain.balance(TREASURY)
    return float(b.get("usdc") or 0.0)


def check_once(notify=None):
    """One poll. Returns (delta, current_balance) or (None, current) on
    no change.

    `notify` is an optional callable(str) so the test suite can drive
    notifications through a list without standing up Telegram. The
    background thread passes None and gets the default direct-send.
    """
    now = _fetch_balance()
    st = _read_state()

    if st is None or "last_balance" not in st:
        # First run (or state lost): record and stay quiet. Announcing
        # the existing balance on a fresh deploy would page the operator
        # for money that predates the watch.
        _write_state(now)
        log.info("treasury watch baseline set: %.2f USDC", now)
        return None, now

    last = float(st["last_balance"])
    delta = round(now - last, 6)

    send = notify if notify is not None else _notify_default

    if delta >= MIN_DELTA:
        _write_state(now)
        # f-string, not %-formatting and not .format(): the message uses
        # %.2f-style precision which only %-formatting understands, and
        # str.format() silently ignores those specifiers — which shipped
        # once and produced an alert reading literally
        # "💰 Received %.2f USDC / Treasury: %s / ..." with no values
        # in it.
        msg = (
            f"\U0001F4B0 Received {delta:.2f} USDC\n"
            f"Treasury: {TREASURY}\n"
            f"New balance: {now:.2f} USDC\n"
            f"https://snowtrace.io/address/{TREASURY}"
        )
        log.info("treasury +%.2f USDC (%.2f -> %.2f)", delta, last, now)
        send(msg)
        return delta, now

    if delta <= 0:
        # Outgoing: our own settlements, or the operator's take-rate
        # split.
        if last > 0 and (last - now) / last > DROP_TOLERANCE:
            log.warning("treasury dropped %.2f (%.2f -> %.2f), larger than the "
                        "%.0f%% tolerance — if this was not a scheduled "
                        "settlement, investigate",
                        now - last, last, now, DROP_TOLERANCE * 100)
        _write_state(now)
        return None, now

    # Delta between 0 and MIN_DELTA: dust or rounding. Record so it
    # does not accumulate into a false alert on the next poll.
    _write_state(now)
    return None, now


# ── background thread ─────────────────────────────────────────────────
_thread = None
_thread_stop = threading.Event()


def start_background():
    """Spawn the polling thread. Idempotent — second call is a no-op.

    Designed to be called once at process startup, from main_utron.py /
    main.py. The thread is a daemon: it will die with the process and
    the in-flight tick will not be checkpointed.
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    if not _ENABLED:
        log.warning("treasury watch disabled (UBOT_OPERATOR_TG_ID or "
                    "UBOT_BOT_TOKEN missing); polling runs but Telegram "
                    "alerts are off")
        return
    log.info("treasury watch starting: every %ss, min delta %.4f USDC",
             POLL_SECONDS, MIN_DELTA)
    _thread_stop.clear()
    _thread = threading.Thread(
        target=_run_forever, name="treasury-watch", daemon=True)
    _thread.start()


def stop_background():
    _thread_stop.set()


def _run_forever():
    while not _thread_stop.is_set():
        try:
            check_once()
        except Exception:
            # A failed poll must never kill the loop; the next tick
            # retries.
            log.exception("treasury watch poll failed")
        _thread_stop.wait(POLL_SECONDS)