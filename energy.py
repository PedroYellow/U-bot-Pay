"""
TronRental energy rental — client for tronrental.com.

Design notes (TRONRENTAL_INTEGRATION.md):

  - TronRental bills in TRX, not TRC-20. Nothing here moves funds on Tron; this
    module only reads prices and creates invoices. That is what keeps the
    integration bridge-free: U-bot stays entirely on Avalanche.
  - Two of the quote endpoints are public. GET /v1/prices and
    GET /v1/energy/quote need no API key; sending one returns the account's
    negotiated rate instead of the public market price. Every method that can
    take a key does.
  - Everything fails closed. If TronRental does not answer, the caller gets an
    exception rather than a stale or invented price — the fee is charged against
    a quote, so a wrong quote means charging the wrong amount.
"""
import asyncio
import hashlib
import logging
import os
import time

import httpx

log = logging.getLogger("ubot.energy")

TRONRENTAL_BASE = os.environ.get(
    "TRONRENTAL_BASE", "https://api.tronrental.com/v1").rstrip("/")
TRONRENTAL_API_KEY = os.environ.get("TRONRENTAL_API_KEY", "")
TIMEOUT = float(os.environ.get("TRONRENTAL_TIMEOUT", "15"))

# Documented bounds. Quoting outside them would be rejected by the API, and
# validating here means the user gets a clear message instead of a 400.
MIN_ENERGY = 60_000
MAX_ENERGY = 5_000_000
# Their docs say energy supports 1h only. Note that create_invoice() echoes
# duration back as null even when one is sent, so the value we hold is the one we
# asked for, not a confirmation of it.
SUPPORTED_DURATIONS = ("1h",)

# Energy needed for a single USDT (TRC-20) transfer, from TronRental's own docs.
ENERGY_RECIPIENT_HAS_USDT = 65_000
ENERGY_RECIPIENT_NO_USDT = 131_000


# ── TRON address validation ────────────────────────────────────────────────
# TRON addresses are base58check: 25 bytes — a 0x41 version byte, a 21-byte
# account id, and 4 checksum bytes.
#
# This was validated against TronGrid before being trusted: two addresses
# quoted from memory as "real" turned out not to exist on-chain and correctly
# fail the checksum, while the Tether treasury and the Tron Foundation — which
# do exist — pass.
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes | None:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            return None
        n = n * 58 + i
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * pad + body


def validate_tron_address(addr) -> str:
    """Return the address if it is a valid TRON address, else raise ValueError.

    Deliberately strict. An EVM 0x address is the most likely mistake and it
    would otherwise be silently passed to TronRental as a target that can never
    receive a delegation.
    """
    if not isinstance(addr, str) or not addr:
        raise ValueError("TRON address required")
    addr = addr.strip()
    if addr.startswith("0x"):
        raise ValueError("that is an EVM address, not a TRON address")
    if len(addr) != 34:
        raise ValueError("a TRON address is 34 characters")
    if not addr.startswith("T"):
        raise ValueError("a TRON address starts with T")
    raw = _b58decode(addr)
    if raw is None or len(raw) != 25:
        raise ValueError("not valid base58")
    if raw[0] != 0x41:
        raise ValueError("wrong version byte for TRON")
    body, checksum = raw[:21], raw[21:]
    if hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4] != checksum:
        raise ValueError("checksum does not match — check for a typo")
    return addr


def validate_volume(volume) -> int:
    try:
        v = int(volume)
    except (TypeError, ValueError):
        raise ValueError("energy volume must be a whole number")
    if not (MIN_ENERGY <= v <= MAX_ENERGY):
        raise ValueError(
            f"energy must be between {MIN_ENERGY:,} and {MAX_ENERGY:,}")
    return v


def validate_duration(duration) -> str:
    d = str(duration or "1h")
    if d not in SUPPORTED_DURATIONS:
        raise ValueError(f"duration must be one of {', '.join(SUPPORTED_DURATIONS)}")
    return d


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if TRONRENTAL_API_KEY:
        h["X-API-Key"] = TRONRENTAL_API_KEY
    return h


async def _get(path: str, params: dict | None = None) -> dict:
    url = f"{TRONRENTAL_BASE}{path}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(url, params=params, headers=_headers())
    if r.status_code != 200:
        # Do not surface their body: it can contain account detail when a key is
        # rejected. The reason is enough to tell the caller it failed.
        log.warning("tronrental %s -> %s", path, r.status_code)
        raise RuntimeError(f"TronRental unavailable ({r.status_code})")
    return r.json()


class TronRentalBusy(RuntimeError):
    """Their account is at its pending-invoice limit.

    TronRental allows at most 3 pending invoices at a time and answers 429 past
    that. Invoices expire on their own after 30 minutes and there is no endpoint
    to cancel one early, so this is a wait condition rather than an error — the
    caller should tell the user to retry shortly, not to give up.
    """


async def _post(path: str, payload: dict) -> dict:
    url = f"{TRONRENTAL_BASE}{path}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(url, json=payload, headers=_headers())
    if r.status_code == 429:
        log.warning("tronrental POST %s -> 429 (pending invoice limit)", path)
        raise TronRentalBusy(
            "too many energy invoices in progress — they expire after 30 "
            "minutes, so try again shortly")
    if r.status_code >= 400:
        log.warning("tronrental POST %s -> %s: %s", path, r.status_code,
                    r.text[:160])
        raise RuntimeError(f"TronRental rejected the request ({r.status_code})")
    return r.json()


# ── public API ─────────────────────────────────────────────────────────────

async def market_prices() -> dict:
    """Current market prices. Public, no key needed."""
    return await _get("/prices")


async def quote(volume: int = ENERGY_RECIPIENT_HAS_USDT,
                duration: str = "1h") -> dict:
    """Exact price for a volume, including the fixed fee.

    Public endpoint: without a key this is the market price, with one it is the
    account's negotiated rate. Returns the raw response plus a normalised view.
    """
    volume = validate_volume(volume)
    duration = validate_duration(duration)
    raw = await _get("/energy/quote",
                     {"volume": volume, "duration": duration})
    try:
        total_trx = float(raw["total_trx"])
        rental = float(raw.get("rental_trx", 0))
        fixed = float(raw.get("fixed_fee_trx", 0))
        usd = float(raw["total_usd"]) if raw.get("total_usd") is not None else None
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError("TronRental returned an unexpected quote") from e
    if total_trx <= 0:
        raise RuntimeError("TronRental returned a zero price")
    return {
        "volume": volume,
        "duration": duration,
        "total_trx": total_trx,
        "rental_trx": rental,
        "fixed_fee_trx": fixed,
        "total_usd": usd,
        "negotiated": bool(raw.get("personal_price")),
        "raw": raw,
    }


async def create_invoice(target_address: str, volume: int,
                         duration: str = "1h", transfer_count: int | None = None,
                         bandwidth_amount: int | None = None) -> dict:
    """Create a payment invoice. The user pays TronRental directly.

    This is the step that keeps U-bot out of the payment path entirely: there is
    no way to attach a commission to this invoice, which is why the fee is
    billed separately against the user's U-bot balance.
    """
    target_address = validate_tron_address(target_address)
    volume = validate_volume(volume)
    duration = validate_duration(duration)
    payload = {
        "target_address": target_address,
        "energy_amount": volume,
        "duration": duration,
    }
    if transfer_count is not None:
        payload["transfer_count"] = int(transfer_count)
    if bandwidth_amount is not None:
        payload["bandwidth_amount"] = int(bandwidth_amount)
    return await _post("/invoices", payload)


async def get_invoice(invoice_id) -> dict:
    """Invoice status from TronRental.

    Note the path. /orders/{id} only accepts integer order ids and rejects a UUID
    with 422 — it is for buy_energy() purchases, not for invoices created through
    /invoices. Verified against their API: GET /invoices/<uuid> returns 200.

    Used to tell the user whether the energy actually landed, and by the webhook
    handler to react without polling.
    """
    return await _get(f"/invoices/{invoice_id}")


async def get_order(order_id) -> dict:
    """Order status for a direct buy_energy() purchase. Integer id only."""
    return await _get(f"/orders/{int(order_id)}")


# ── savings ────────────────────────────────────────────────────────────────

def savings(volume: int = ENERGY_RECIPIENT_HAS_USDT,
            recipient_has_usdt: bool = True) -> dict:
    """Compare renting against burning TRX, from their published figures.

    Used only for the headline the user reads. The price they are charged comes
    from quote(), never from here.
    """
    need = ENERGY_RECIPIENT_HAS_USDT if recipient_has_usdt else ENERGY_RECIPIENT_NO_USDT
    return {
        "volume": volume,
        "energy_needed": need,
        "note": (
            f"A USDT transfer from this address needs about {need:,} energy. "
            + ("It already holds USDT, so the cheaper amount applies."
               if recipient_has_usdt else
               "It holds no USDT, so a transfer activates the token and costs more energy.")),
    }