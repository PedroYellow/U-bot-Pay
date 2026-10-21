"""
U-bot Pay — core: users, contacts, FX, policy (limits + dual control), ledger.
SQLite. Demo-grade but structured like the real thing.
"""
import os, threading, json, time, sqlite3, hashlib, secrets, logging

# Audit 21 Oct: this module used log.warning in three places but never created a
# logger, so any of those paths raised NameError. init() hit it on the first
# backfill that failed, which took the whole service down — it surfaced only
# because the TronRental work exercised that path. Line 171 had been latent
# since the multi-chain change.
log = logging.getLogger("ubot.core")
from contextlib import contextmanager

DB = os.environ.get("UBOT_DB", "/opt/ubotpay/ubotpay.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, lang TEXT DEFAULT 'en',
  telegram_id INTEGER UNIQUE, voice_pin TEXT, address TEXT, chain_id INTEGER DEFAULT 43114,
  daily_limit REAL DEFAULT 500, dual_threshold REAL DEFAULT 1000, approver_telegram_id INTEGER,
  jwt_nonce TEXT, jwt_exp INTEGER, created_at INTEGER);
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, alias TEXT, name TEXT, address TEXT,
  chain_id INTEGER DEFAULT 43114, country TEXT, currency TEXT DEFAULT 'USDC', is_agent INTEGER DEFAULT 0,
  UNIQUE(user_id, alias, chain_id));
CREATE TABLE IF NOT EXISTS invoices(
  id TEXT PRIMARY KEY, user_id INTEGER, from_name TEXT, from_address TEXT, amount_usdc REAL,
  description TEXT, due INTEGER, status TEXT DEFAULT 'open', paid_tx TEXT, created_at INTEGER);
CREATE TABLE IF NOT EXISTS bridges(
  id TEXT PRIMARY KEY,
  user_id INTEGER,
  from_chain INTEGER NOT NULL,
  to_chain INTEGER NOT NULL,
  from_address TEXT NOT NULL,
  to_address TEXT NOT NULL,
  amount_usdc REAL NOT NULL,
  to_amount_min TEXT,
  tool TEXT,
  total_fee_usd REAL,
  tx_hash TEXT,
  status TEXT DEFAULT 'pending',  -- pending | submitted | confirming | done | failed
  error TEXT,
  -- Amount actually credited on the destination chain, as reported by the
  -- bridge partner. Distinct from amount_usdc (sent) and to_amount_min
  -- (guaranteed floor) — audit 20 Oct round 4, the "done" notification used to
  -- print to_amount_min as if it were what arrived.
  delivered_usdc REAL,
  raw_quote TEXT,                  -- JSON blob of the full LI.FI quote for replay/debug
  created_at INTEGER,
  confirmed_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_bridges_status ON bridges(status);
CREATE INDEX IF NOT EXISTS idx_bridges_user ON bridges(user_id);
CREATE TABLE IF NOT EXISTS push_subscriptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  endpoint TEXT NOT NULL,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  user_agent TEXT,
  is_alive INTEGER DEFAULT 1,
  created_at INTEGER,
  last_used_at INTEGER,
  UNIQUE(endpoint)
);
CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_push_alive ON push_subscriptions(is_alive);
CREATE TABLE IF NOT EXISTS link_codes(
  code TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER,
  created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_link_codes_user ON link_codes(user_id);

-- payments
-- This table was previously absent from SCHEMA: it only ever existed in the
-- production DB, created by hand. A fresh deployment would fail with
-- "no such table: payments" on the first quote. Audit 20 Oct surfaced this
-- while testing the policy limits. Columns mirror what core.py and api.py read
-- and write, including the ones the ALTER migrations below add.
CREATE TABLE IF NOT EXISTS payments(
  id TEXT PRIMARY KEY,
  user_id INTEGER,
  contact_alias TEXT,
  to_address TEXT,
  amount_usdc REAL,
  amount_local REAL,
  local_ccy TEXT,
  fx_rate REAL,
  fee_usdc REAL DEFAULT 0,
  memo TEXT,
  memo_hash TEXT,
  status TEXT DEFAULT 'pending',   -- pending | pending_approval | settled | failed | rejected | expired_policy
  channel TEXT,
  needs_approval INTEGER DEFAULT 0,
  chain_id INTEGER DEFAULT 43114,
  tx TEXT,
  latency_s REAL,
  approved_by INTEGER,
  created_at INTEGER,
  settled_at INTEGER,
  claimed_at INTEGER   -- when it entered 'settling'; lets a stale claim be found
);

-- TronRental energy orders (integration 21 Oct 2026).
-- The fee is billed separately from TronRental's own payment, because their
-- invoice API has no commission field and the user pays them directly. This
-- table tracks that side; the fee itself flows through `payments` so it
-- inherits check_policy, the daily limit and the atomic settlement claim.
CREATE TABLE IF NOT EXISTS energy_orders(
  id TEXT PRIMARY KEY,
  user_id INTEGER,
  target_address TEXT,        -- TRON address the energy is delegated to
  volume INTEGER,
  duration TEXT DEFAULT '1h',
  quote_trx REAL,             -- what TronRental quoted
  quote_usd REAL,
  fee_usdc REAL,              -- 5% of quote_usd, floored at ENERGY_FEE_MIN
  status TEXT DEFAULT 'quoted', -- quoted|fee_paid|invoice_created|activated|failed|expired
  tronrental_invoice_id TEXT,
  tronrental_order_id TEXT,
  payment_id TEXT,            -- the U-bot payment that collected the fee
  created_at INTEGER,
  settled_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_energy_user ON energy_orders(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_payments_user   ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_daily  ON payments(user_id, status, created_at);
"""

# demo FX table (USDC per 1 unit of local). Real build → oracle / Circle FX.
FX = {"THB": 1/34.4, "USD": 1.0, "EUR": 1.08, "VND": 1/25400, "MMK": 1/2100, "PHP": 1/56.2, "KHR": 1/4100, "LAK": 1/21800, "IDR": 1/15700, "INR": 1/83.5, "MXN": 1/17.1}
CCY_WORDS = {"baht": "THB", "bath": "THB", "บาท": "THB", "dollar": "USD", "dollars": "USD", "usd": "USD", "euro": "EUR", "euros": "EUR", "dong": "VND", "kyat": "MMK",
             "peso": "PHP", "pesos": "PHP", "riel": "KHR", "kip": "LAK", "rupiah": "IDR", "rupee": "INR", "rupees": "INR", "usdc": "USDC"}


@contextmanager
def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    try:
        yield c; c.commit()
    finally:
        c.close()


def init():
    """Create tables if missing, then idempotently add new columns for multi-chain support.
    SQLite has no ADD COLUMN IF NOT EXISTS, so we swallow the 'duplicate column' error.

    Migration order matters: ALTERs first to add the new columns with defaults,
    then backfill NULL rows, then create the partial unique index. Otherwise the
    unique index explodes on the (NULL, NULL) collisions from legacy users."""
    treasury = os.environ.get("UBOT_WALLET_ADDRESS", "")
    with conn() as c:
        c.executescript(SCHEMA)
        for ddl in [
            "ALTER TABLE users    ADD COLUMN chain_id  INTEGER DEFAULT 43114",
            "ALTER TABLE users    ADD COLUMN jwt_nonce TEXT",
            "ALTER TABLE users    ADD COLUMN jwt_exp   INTEGER",
            "ALTER TABLE contacts ADD COLUMN chain_id  INTEGER DEFAULT 43114",
            "ALTER TABLE payments ADD COLUMN chain_id  INTEGER DEFAULT 43114",
            "ALTER TABLE invoices ADD COLUMN chain_id  INTEGER DEFAULT 43114",
            # Audit 20 Oct round 4: real delivered amount per bridge.
            "ALTER TABLE bridges  ADD COLUMN delivered_usdc REAL",
            # Audit 20 Oct round 12: when a payment entered 'settling', so a claim
            # stranded by a killed process can be identified and released.
            "ALTER TABLE payments ADD COLUMN claimed_at INTEGER",
        ]:
            try: c.execute(ddl)
            except Exception: pass
        # Backfill users with NULL address to the operator treasury (Avalanche).
        # Avoids (NULL,NULL) collisions on the unique index below.
        #
        # Audit 21 Oct: this assigned the treasury address to *every* NULL-address
        # row. idx_users_wallet is UNIQUE on (chain_id, lower(address)), so the
        # second such row made the whole UPDATE raise IntegrityError — which
        # aborted init() and, because the handler raised NameError (this module
        # had no logger), took the service down on every restart. One row per
        # startup got the address; the rest kept theirs and were left alone.
        if treasury:
            assigned = 0
            for row in c.execute(
                "SELECT id FROM users WHERE address IS NULL ORDER BY id"
            ).fetchall():
                try:
                    c.execute("UPDATE users SET address = ?, chain_id = 43114 WHERE id = ?",
                              (treasury, row["id"]))
                    assigned += 1
                except Exception as e:
                    # Another user already holds this wallet, so the unique index
                    # rejects it. That is the normal case from the second row on,
                    # and it is not an error worth failing startup over.
                    log.info("skipped treasury backfill for user %s: %s", row["id"], e)
            if assigned:
                log.info("backfilled treasury address onto %d user(s)", assigned)
        # Dedupe users by (chain_id, lower(address)) — merge payments/contacts/invoices
        # into the lowest id and delete the rest. Required before the unique index.
        # If a collision happens on contacts (same alias in two users), the canonical
        # row wins; the secondary is dropped. Same for invoices.
        try:
            dups = list(c.execute(
                "SELECT chain_id, lower(address) AS a, COUNT(*) c FROM users "
                "WHERE address IS NOT NULL GROUP BY chain_id, a HAVING c > 1"))
            for chain_id, addr_lc, _ in dups:
                canonical = c.execute("SELECT id FROM users WHERE chain_id = ? AND lower(address) = ? ORDER BY id LIMIT 1", (chain_id, addr_lc)).fetchone()
                if not canonical: continue
                cid = canonical[0]
                for (uid,) in c.execute("SELECT id FROM users WHERE chain_id = ? AND lower(address) = ? AND id != ?", (chain_id, addr_lc, cid)).fetchall():
                    # contacts: drop the secondary row if alias collides with canonical
                    c.execute("DELETE FROM contacts WHERE user_id = ? AND alias IN (SELECT alias FROM contacts WHERE user_id = ?)", (uid, cid))
                    c.execute("UPDATE contacts SET user_id = ? WHERE user_id = ?", (cid, uid))
                    # invoices: no UNIQUE on (user_id, ...) in this DB, so safe to update
                    try: c.execute("UPDATE invoices SET user_id = ? WHERE user_id = ?", (cid, uid))
                    except Exception: pass
                    # payments: same
                    try: c.execute("UPDATE payments SET user_id = ? WHERE user_id = ?", (cid, uid))
                    except Exception: pass
                    c.execute("DELETE FROM users WHERE id = ?", (uid,))
        except Exception as e:
            log.warning("dedupe users on startup: %s", e)
        try: c.execute("UPDATE contacts SET chain_id = 43114 WHERE chain_id IS NULL")
        except Exception: pass
        try: c.execute("UPDATE payments SET chain_id = 43114 WHERE chain_id IS NULL")
        except Exception: pass
        try: c.execute("UPDATE invoices SET chain_id = 43114 WHERE chain_id IS NULL")
        except Exception: pass
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_wallet ON users(chain_id, lower(address)) WHERE address IS NOT NULL")
    # Start the reaper outside the transaction block: it opens its own connection
    # on its first tick, and a live connection here would just hold the write lock.
    start_settling_reaper(
        interval_s=int(os.environ.get("UBOT_REAP_INTERVAL_S", "60")),
        max_age_s=int(os.environ.get("UBOT_SETTLING_MAX_AGE_S", "300")),
    )


# ───────── TronRental energy orders ─────────
# Integration 21 Oct 2026. See TRONRENTAL_INTEGRATION.md.
#
# U-bot never touches TRX or TRC-20. It quotes TronRental's public price, takes
# its own fee against the user's U-bot balance, and hands back an invoice the
# user settles with TronRental directly. That is what keeps it bridge-free.

ENERGY_FEE_PCT = float(os.environ.get("UBOT_ENERGY_FEE_PCT", "5.0")) / 100
# Below roughly a cent the fee does not cover its own gas, so it is floored.
# At the real prices ($0.51 and $0.95) 5% is $0.026 / $0.048, well clear.
ENERGY_FEE_MIN = float(os.environ.get("UBOT_ENERGY_FEE_MIN_USDC", "0.005"))


def energy_fee_for(quote_usd: float) -> float:
    """U-bot's cut of an energy order.

    Floored at ENERGY_FEE_MIN and rounded to 6 decimals, which is the smallest
    unit USDC has on chain. Rounding up would overcharge by a fraction of a
    cent; the floor case is handled by the minimum instead.
    """
    if quote_usd <= 0:
        raise ValueError("quote must be positive")
    fee = max(quote_usd * ENERGY_FEE_PCT, ENERGY_FEE_MIN)
    return round(fee, 6)


def create_energy_order(user_id: int, target_address: str, volume: int,
                        quote_trx: float, quote_usd: float,
                        duration: str = "1h") -> dict:
    """Record a quote. No money moves here — the fee is collected in
    collect_energy_fee(), which is a separate, explicit step."""
    fee = energy_fee_for(quote_usd)
    oid = "en_" + secrets.token_hex(6)
    now = int(time.time())
    with conn() as c:
        c.execute(
            "INSERT INTO energy_orders(id,user_id,target_address,volume,duration,"
            "quote_trx,quote_usd,fee_usdc,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,'quoted',?)",
            (oid, user_id, target_address, int(volume), duration,
             float(quote_trx), float(quote_usd), fee, now))
    return get_energy_order(oid)


def get_energy_order(order_id: str, user_id: int | None = None) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM energy_orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    # Enumeration guard: an order id is a bearer-ish token, so a mismatch is a
    # 404 rather than a 403 — the caller learns nothing about other users' orders.
    if user_id is not None and int(d["user_id"]) != int(user_id):
        return None
    return d


def list_energy_orders(user_id: int, limit: int = 20) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM energy_orders WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def claim_energy_order_for_payment(order_id: str) -> bool:
    """Move an order out of 'quoted' so only one caller charges its fee.

    The same compare-and-set discipline as claim_payment_for_settlement: without
    it two concurrent POST /energy/order calls (or a retry after a timeout) could
    both pass the status check and both charge the fee.

    Returns True only for the caller that won.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE energy_orders SET status='fee_claiming' "
            "WHERE id=? AND status='quoted'",
            (order_id,),
        )
        return cur.rowcount == 1


def release_energy_order(order_id: str) -> None:
    """Undo the claim so a failed charge can be retried."""
    with conn() as c:
        c.execute(
            "UPDATE energy_orders SET status='quoted' "
            "WHERE id=? AND status='fee_claiming'",
            (order_id,),
        )


def find_energy_order_by_payment(payment_id: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM energy_orders WHERE payment_id=? LIMIT 1",
            (payment_id,)).fetchone()
    return dict(row) if row else None


def set_energy_status(order_id: str, status: str, **fields) -> dict | None:
    """Move an order forward. Only the columns named in `fields` are written."""
    allowed = {"tronrental_invoice_id", "tronrental_order_id", "payment_id"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown energy order fields: {sorted(unknown)}")
    sets = ["status=?"]
    vals: list = [status]
    for k, v in fields.items():
        sets.append(f"{k}=?")
        vals.append(v)
    if status in ("activated", "failed", "expired"):
        sets.append("settled_at=?")
        vals.append(int(time.time()))
    vals.append(order_id)
    with conn() as c:
        c.execute(f"UPDATE energy_orders SET {','.join(sets)} WHERE id=?", tuple(vals))
    return get_energy_order(order_id)


# ───────── pricing (business model) ─────────
# Recipient always receives `amount_usdc` in full. The sender pays `total_usdc` = amount + fee.
#   fee_usdc  = FEE_PCT of amount (min FEE_MIN) + FX spread (SPREAD_PCT, only on non-USDC currencies)
# Fees go to REVENUE_ADDRESS (a second USDC transfer in the same settlement).
FEE_PCT    = float(os.environ.get("UBOT_FEE_PCT", "0.5")) / 100     # 0.5 % per transfer
FEE_MIN    = float(os.environ.get("UBOT_FEE_MIN_USDC", "0.01"))     # never below 1 cent
SPREAD_PCT = float(os.environ.get("UBOT_FX_SPREAD_PCT", "0.3")) / 100  # 0.3 % FX spread
AGENT_TAKE_PCT = float(os.environ.get("UBOT_AGENT_TAKE_PCT", "1.0")) / 100  # 1 % on x402 agent payments
BANK_PCT   = 0.055   # what a bank / remittance shop charges (for the "you saved" line)


def quote(amount: float, ccy: str) -> dict:
    ccy = ccy.upper()
    if ccy == "USDC":
        amt = round(amount, 2); spread = 0.0; rate_shown = 1.0
    else:
        mid = FX[ccy]; amt = round(amount * mid, 2)
        spread = round(amt * SPREAD_PCT, 4); rate_shown = round(1 / mid, 4)
    fee_base = round(max(amt * FEE_PCT, FEE_MIN), 4)
    fee = round(fee_base + spread, 2) if (fee_base + spread) >= 0.01 else 0.01
    return {"amount_usdc": amt, "amount_local": amount, "local_ccy": ccy, "fx_rate": rate_shown,
            "fee_usdc": fee, "fee_pct": round(100 * fee / amt, 2) if amt else 0, "total_usdc": round(amt + fee, 2),
            "bank_fee_usdc": round(amt * BANK_PCT, 2), "saved_usdc": round(amt * BANK_PCT - fee, 2),
            "pricing": {"fee_pct": FEE_PCT * 100, "fee_min": FEE_MIN, "fx_spread_pct": SPREAD_PCT * 100}}


def revenue_stats() -> dict:
    with conn() as c:
        r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(amount_usdc),0) vol, COALESCE(SUM(fee_usdc),0) fees FROM payments WHERE status='settled'").fetchone()
        return {"settled_payments": r["n"], "volume_usdc": round(r["vol"], 2), "revenue_usdc": round(r["fees"], 4)}


# ───────── users / contacts ─────────
def upsert_user(telegram_id: int, name: str, lang="en", address=None) -> dict:
    with conn() as c:
        r = c.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        if r:
            if address: c.execute("UPDATE users SET address=? WHERE id=?", (address, r["id"]))
            return dict(c.execute("SELECT * FROM users WHERE id=?", (r["id"],)).fetchone())
        c.execute("INSERT INTO users(name,lang,telegram_id,address,created_at) VALUES(?,?,?,?,?)",
                  (name, lang, telegram_id, address, int(time.time())))
        return dict(c.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone())


# ───────── bridges (LI.FI cross-chain) ─────────
import secrets as _secrets_bridge
def create_bridge(user_id: int, from_chain: int, to_chain: int, from_address: str, to_address: str, amount_usdc: float, tool: str, total_fee_usd: float, to_amount_min: str, raw_quote: str) -> dict:
    """Create a new bridge record. The tx_hash is filled in later
    when the user submits the bridge transaction on the source chain."""
    bid = "br_" + _secrets_bridge.token_urlsafe(12)
    with conn() as cc:
        cc.execute(
            "INSERT INTO bridges(id, user_id, from_chain, to_chain, from_address, to_address, amount_usdc, to_amount_min, tool, total_fee_usd, raw_quote, status, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (bid, user_id, from_chain, to_chain, from_address, to_address, amount_usdc, to_amount_min, tool, total_fee_usd, raw_quote, int(time.time())),
        )
    return get_bridge(bid)

def get_bridge(bridge_id: str) -> dict | None:
    with conn() as cc:
        row = cc.execute("SELECT * FROM bridges WHERE id = ?", (bridge_id,)).fetchone()
    return dict(row) if row else None

def list_bridges(user_id: int | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
    with conn() as cc:
        q = "SELECT * FROM bridges"
        where = []
        params = []
        if user_id is not None:
            where.append("user_id = ?")
            params.append(user_id)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = cc.execute(q, tuple(params)).fetchall()
    return [dict(r) for r in rows]

def update_bridge(bridge_id: str, **fields) -> bool:
    """Update a bridge row. Returns True if a row was actually changed.

    Audit 20 Oct (round 4): this read `cc.rowcount` off the *connection* after
    the `with` block had already closed it. sqlite3.Connection has no
    rowcount, so every call raised AttributeError — the UPDATE itself had been
    committed, but the caller never got a truthful return value and every
    `except` around it swallowed the failure. The rowcount lives on the cursor.
    """
    if not fields: return False
    sets = ", ".join(f"{k} = ?" for k in fields.keys())
    vals = list(fields.values()) + [bridge_id]
    with conn() as cc:
        cur = cc.execute(f"UPDATE bridges SET {sets} WHERE id = ?", tuple(vals))
        return cur.rowcount > 0

def get_user_by_wallet(address: str, chain_id: int = 43114) -> dict | None:
    """Look up user by (chain_id, address). Lowercased address. Returns row or None."""
    with conn() as cc:
        row = cc.execute("SELECT * FROM users WHERE lower(address) = lower(?) AND chain_id = ?",
                         (address, chain_id)).fetchone()
    return dict(row) if row else None

def upsert_user_by_wallet(address: str, chain_id: int = 43114) -> dict:
    """Create or return the user owning (chain_id, address). Idempotent."""
    u = get_user_by_wallet(address, chain_id)
    if u:
        return u
    with conn() as cc:
        cc.execute(
            "INSERT INTO users(name, lang, address, chain_id) VALUES(?, ?, ?, ?)",
            (f"wallet:{address[:6]}...{address[-4:]}", "en", address, chain_id),
        )
    return get_user_by_wallet(address, chain_id)

def get_user(user_id=None, telegram_id=None) -> dict | None:
    with conn() as c:
        q, a = ("SELECT * FROM users WHERE id=?", (user_id,)) if user_id else ("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        r = c.execute(q, a).fetchone(); return dict(r) if r else None


def set_policy(user_id, daily_limit=None, dual_threshold=None, approver_telegram_id=None, voice_pin=None):
    with conn() as c:
        for k, v in (("daily_limit", daily_limit), ("dual_threshold", dual_threshold), ("approver_telegram_id", approver_telegram_id), ("voice_pin", voice_pin)):
            if v is not None: c.execute(f"UPDATE users SET {k}=? WHERE id=?", (v, user_id))


def add_contact(user_id, alias, name, address, country="", is_agent=0):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO contacts(user_id,alias,name,address,country,is_agent) VALUES(?,?,?,?,?,?)",
                  (user_id, alias.lower(), name, address, country, is_agent))


def find_contact(user_id, text: str) -> dict | None:
    """fuzzy: alias or name contained in text, or text contained in name."""
    t = text.lower().strip()
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM contacts WHERE user_id=?", (user_id,))]
    for r in rows:
        if r["alias"] in t or t in r["alias"] or r["name"].lower() in t or t in r["name"].lower():
            return r
    for r in rows:  # token overlap
        if any(tok in t for tok in r["name"].lower().split() if len(tok) > 2):
            return r
    return None


def list_contacts(user_id) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM contacts WHERE user_id=? ORDER BY name", (user_id,))]


# ───────── policy ─────────
# Payment states that still reserve budget. A payment that is only quoted must
# not be spendable twice: audit 20 Oct found that spent_today counted only
# 'settled', so several payments could all be quoted while the total looked
# like zero and then all be approved together. Round 6 added 'settling', the
# state a payment occupies while it is actually being signed and broadcast —
# without it, a payment in flight frees its budget for the duration of the
# network call.
_RESERVED_STATUSES = ("pending", "pending_approval", "settling")


def _day_start(tz_offset_hours: int | None = None) -> int:
    """Start of the current local day, as a unix timestamp.

    The previous version used `time.time() % 86400`, i.e. UTC midnight. For a
    Bangkok deployment that resets the 'daily limit' at 07:00 local, which is
    not what a user means by 'today'. Set UBOT_DAY_TZ_OFFSET (hours from UTC,
    e.g. 7 for Bangkok) to align it with the user's day.
    """
    now = int(time.time())
    off = tz_offset_hours
    if off is None:
        try:
            off = int(os.environ.get("UBOT_DAY_TZ_OFFSET", "7"))
        except ValueError:
            off = 7
    local = now + off * 3600
    return local - (local % 86400) - off * 3600


def spent_today(user_id, include_reserved: bool = True) -> float:
    """Amount already committed today.

    Counts settled payments plus anything still in flight (pending /
    pending_approval) unless `include_reserved` is False. Reserving the
    in-flight amount is what stops N payments being quoted against a limit of
    one.
    """
    day0 = _day_start()
    statuses = ("settled",) + _RESERVED_STATUSES if include_reserved else ("settled",)
    marks = ",".join("?" * len(statuses))
    with conn() as c:
        r = c.execute(
            f"SELECT COALESCE(SUM(amount_usdc),0) s FROM payments "
            f"WHERE user_id=? AND status IN ({marks}) AND created_at>=?",
            (user_id, *statuses, day0),
        ).fetchone()
        return float(r["s"])


def _daily_limit(user: dict) -> float | None:
    """The user's daily cap, or None when no policy is set.

    Audit 20 Oct: this used to be `user.get('daily_limit') or 1e12`, which turned
    an explicit 0 — the most restrictive setting there is — into a trillion
    USDC. None (no policy configured) and 0 (nothing allowed) are different
    states and must not collapse into the same branch.
    """
    v = user.get("daily_limit")
    if v is None:
        try:
            return float(os.environ.get("UBOT_DEFAULT_DAILY_LIMIT", "40"))
        except ValueError:
            return 40.0
    return float(v)


def _dual_threshold(user: dict) -> float:
    """Amount at or above which a co-signer is required.

    `or 1e12` here would mean a configured 0 disables dual control entirely —
    the opposite of what the user asked for and the product's headline promise.
    A missing threshold falls back to the operator default.
    """
    v = user.get("dual_threshold")
    if v is None:
        try:
            return float(os.environ.get("UBOT_DEFAULT_DUAL_THRESHOLD", "10"))
        except ValueError:
            return 10.0
    return float(v)


def check_policy(user: dict, amount_usdc: float, exclude_payment_id: str | None = None) -> dict:
    """May `user` commit `amount_usdc` more right now?

    `exclude_payment_id` drops one already-created payment from the committed
    total. It exists for callers that re-check a payment they are about to
    settle, where the amount is already included in the committed sum.

    /approve deliberately does NOT use it: since in-flight payments now reserve
    budget, the pending payment is already counted, and subtracting it would
    let N pending payments each clear the cap in turn. /approve re-checks with
    exclude=None and amount 0, i.e. "does anything else already committed today
    leave room for this one?".
    """
    out = {"allowed": True, "needs_approval": False, "reason": "", "daily_limit": None}
    uid = user["id"]
    committed = spent_today(uid)
    if exclude_payment_id:
        with conn() as c:
            row = c.execute("SELECT amount_usdc FROM payments WHERE id=? AND user_id=?",
                            (exclude_payment_id, uid)).fetchone()
        if row:
            committed -= float(row["amount_usdc"] or 0.0)

    limit = _daily_limit(user)
    out["daily_limit"] = limit
    total = committed + amount_usdc
    if total > limit:
        out.update(allowed=False,
                   reason=f"daily limit {limit:g} USDC exceeded "
                          f"({committed:g} USDC already committed today)")
    elif amount_usdc >= _dual_threshold(user):
        out.update(needs_approval=True,
                   reason=f"amount ≥ {user.get('dual_threshold')} USDC requires a second approval")
    return out


# ───────── payments ─────────
def create_payment(user_id, contact, q: dict, memo: str, channel: str, needs_approval: bool) -> dict:
    pid = "pay_" + secrets.token_hex(6)
    memo_hash = hashlib.sha256(f"{pid}|{memo}".encode()).hexdigest() if memo else None
    with conn() as c:
        c.execute("""INSERT INTO payments(id,user_id,contact_alias,to_address,amount_usdc,amount_local,local_ccy,fx_rate,fee_usdc,memo,memo_hash,status,channel,needs_approval,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (pid, user_id, contact["alias"], contact["address"], q["amount_usdc"], q["amount_local"], q["local_ccy"], q["fx_rate"], q.get("fee_usdc", 0),
                   memo, memo_hash, "pending_approval" if needs_approval else "pending", channel, int(needs_approval), int(time.time())))
        return dict(c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone())


def get_payment(pid) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone(); return dict(r) if r else None


def claim_payment_for_settlement(pid: str) -> bool:
    """Atomically move a payment out of pending_approval so only one caller can
    settle it.

    Audit 20 Oct round 6: api.approve() read the payment, checked the status, and
    only wrote 'settled' *after* an `await` on the chain call. Two concurrent
    /approve requests — which is easy to produce, and unavoidable with more than
    one uvicorn worker — both passed the read-side check and both called
    send_usdc. A double spend.

    This is the compare-and-set: `UPDATE ... WHERE id=? AND status=?` is atomic in
    SQLite, and rowcount tells us whether we were the one who won. Returns True
    only for the caller that actually claimed it.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE payments SET status='settling', claimed_at=? "
            "WHERE id=? AND status='pending_approval'",
            (int(time.time()), pid),
        )
        return cur.rowcount == 1


_REAPER: threading.Thread | None = None


def _reaper_loop(interval_s: int, max_age_s: int) -> None:
    """Background thread that reaps stale settlement claims.

    A daemon thread rather than an APScheduler job: nothing in this codebase used
    APScheduler despite it being installed, and a thread started from init() has
    no dependency to add and dies with the process.
    """
    while True:
        try:
            time.sleep(interval_s)
            ids = reap_stale_settling_claims(max_age_s)
            if ids:
                log.warning(
                    "reaped %d payment(s) stranded in 'settling' and returned them "
                    "to pending_approval: %s", len(ids), ", ".join(ids[:10]))
        except Exception as e:      # never let the thread die
            log.warning("settling reaper iteration failed: %s", e)


def start_settling_reaper(interval_s: int = 60, max_age_s: int = 300) -> None:
    """Idempotent. Called from init() so every entrypoint (api, tg, tests) gets it."""
    global _REAPER
    if _REAPER is not None and _REAPER.is_alive():
        return
    t = threading.Thread(target=_reaper_loop, args=(interval_s, max_age_s),
                         name="settling-reaper", daemon=True)
    t.start()
    _REAPER = t


def reap_stale_settling_claims(max_age_s: int = 300) -> list[str]:
    """Return payments stuck in 'settling' to 'pending_approval'.

    Audit 20 Oct round 12. claim_payment_for_settlement() moves a payment to
    'settling' before the chain call, and release_payment_claim() hands it back —
    but only if the process survives to run its `finally`. A SIGKILL, an OOM kill
    or a systemd restart between the claim and the settle leaves it in 'settling'
    forever:

        spent_today() counts 'settling' as committed budget, so the user's daily
        limit stays debited by an amount that will never be spent, and
        claim_payment_for_settlement() refuses to re-claim it (its WHERE requires
        pending_approval). Reproduced: 50 USDC reserved permanently out of a 100
        limit, unrecoverable without a direct DB edit.

    Only claims older than max_age_s are reaped, so a settlement that is genuinely
    in flight — a slow RPC, a congested chain — is not interrupted. Five minutes is
    far longer than any observed settlement and still short enough that a stuck
    payment is released well within a user's day.

    Returns the ids it released, so the caller can log or notify.
    """
    cutoff = int(time.time()) - max_age_s
    with conn() as c:
        rows = c.execute(
            "SELECT id FROM payments WHERE status='settling' "
            "AND (claimed_at IS NULL OR claimed_at < ?)",
            (cutoff,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            # Same predicate as release_payment_claim, so this cannot resurrect a
            # payment that was settled between the SELECT and this UPDATE.
            c.execute(
                "UPDATE payments SET status='pending_approval', claimed_at=NULL "
                "WHERE status='settling' AND (claimed_at IS NULL OR claimed_at < ?)",
                (cutoff,),
            )
    return ids


def release_payment_claim(pid: str) -> None:
    """Undo claim_payment_for_settlement so the payment can be retried.

    Used when we claimed successfully but then failed before settling (e.g. the
    policy re-check refused). Without this the payment would be stuck in
    'settling' forever and could never be approved again.
    """
    with conn() as c:
        c.execute(
            "UPDATE payments SET status='pending_approval', claimed_at=NULL "
            "WHERE id=? AND status='settling'",
            (pid,),
        )


def mark(pid, status, tx=None, latency=None, approved_by=None):
    with conn() as c:
        c.execute("UPDATE payments SET status=?, tx=COALESCE(?,tx), latency_s=COALESCE(?,latency_s), approved_by=COALESCE(?,approved_by), settled_at=CASE WHEN ?='settled' THEN ? ELSE settled_at END WHERE id=?",
                  (status, tx, latency, approved_by, status, int(time.time()), pid))


def history(user_id, n=10) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM payments WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, n))]


def pending_approvals(approver_telegram_id) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("""SELECT p.* FROM payments p JOIN users u ON u.id=p.user_id
                 WHERE p.status='pending_approval' AND u.approver_telegram_id=? ORDER BY p.created_at""", (approver_telegram_id,))]


# ───────── invoices (what U-bot "reads" to you) ─────────
def add_invoice(user_id, from_name, from_address, amount_usdc, description, due_days=7) -> str:
    iid = "inv_" + secrets.token_hex(4)
    with conn() as c:
        c.execute("INSERT INTO invoices(id,user_id,from_name,from_address,amount_usdc,description,due,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (iid, user_id, from_name, from_address, amount_usdc, description, int(time.time()) + due_days * 86400, int(time.time())))
    return iid


def clear_invoices(user_id):
    with conn() as c:
        c.execute("DELETE FROM invoices WHERE user_id=?", (user_id,))


def open_invoices(user_id) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM invoices WHERE user_id=? AND status='open' ORDER BY due", (user_id,))]


def pay_invoice(iid, tx):
    with conn() as c:
        c.execute("UPDATE invoices SET status='paid', paid_tx=? WHERE id=?", (tx, iid))

# ───────── wallet-based session (Nivel 1) ─────────
# JWT firmado server-side. El user firma un nonce con su wallet (EIP-191 personal_sign),
# el server valida y emite un JWT de 7 días. El cliente lo guarda en localStorage
# y lo envía como `Authorization: Bearer *** en cada llamada.
# El `jwt_nonce` en users se rota al pedir uno nuevo (replay protection).

import hmac as _hmac, hashlib as _hashlib, base64 as _b64, json as _json, time as _time

def _b64u(b: bytes) -> str:
    return _b64.urlsafe_b64encode(b).rstrip(b"=").decode()

def _jwt_sign(payload: dict, secret: str) -> str:
    h = {"alg": "HS256", "typ": "JWT"}
    h_b64 = _b64u(_json.dumps(h, separators=(",",":")).encode())
    p_b64 = _b64u(_json.dumps(payload, separators=(",",":")).encode())
    sig = _hmac.new(secret.encode(), f"{h_b64}.{p_b64}".encode(), _hashlib.sha256).digest()
    return f"{h_b64}.{p_b64}.{_b64u(sig)}"

def _jwt_verify(token: str, secret: str) -> dict | None:
    try:
        h_b64, p_b64, s_b64 = token.split(".")
        sig = _b64.urlsafe_b64decode(s_b64 + "==")
        expected = _hmac.new(secret.encode(), f"{h_b64}.{p_b64}".encode(), _hashlib.sha256).digest()
        if not _hmac.compare_digest(sig, expected):
            return None
        payload = _json.loads(_b64.urlsafe_b64decode(p_b64 + "=="))
        if payload.get("exp", 0) < _time.time():
            return None
        return payload
    except Exception:
        return None

def new_wallet_nonce(address: str, chain_id: int = 43114) -> str:
    """Generate a single-use nonce for the user to sign. Rotates any previous nonce for that user."""
    import secrets as _secrets
    nonce = "UbotPay sign-in: " + _secrets.token_urlsafe(24) + " — chain " + str(chain_id) + " — " + address
    exp = int(_time.time()) + 600
    with conn() as cc:
        # Detect if a row already exists for this (chain_id, address). If yes,
        # update the nonce in place; if no, create a new row.
        existing = cc.execute("SELECT 1 FROM users WHERE lower(address) = lower(?) AND chain_id = ?",
                              (address, chain_id)).fetchone()
        if existing:
            cc.execute("UPDATE users SET jwt_nonce = ?, jwt_exp = ? WHERE lower(address) = lower(?) AND chain_id = ?",
                       (nonce, exp, address, chain_id))
        else:
            cc.execute("INSERT INTO users(name, lang, address, chain_id, jwt_nonce, jwt_exp) VALUES(?, ?, ?, ?, ?, ?)",
                       (f"wallet:{address[:6]}...{address[-4:]}", "en", address, chain_id, nonce, exp))
    return nonce

def consume_wallet_nonce(nonce: str) -> dict | None:
    """Read the user that owns this nonce. Returns the user row or None if expired/unknown."""
    with conn() as cc:
        row = cc.execute("SELECT * FROM users WHERE jwt_nonce = ?", (nonce,)).fetchone()
    if not row:
        return None
    user = dict(row)
    if not user.get("jwt_exp") or user["jwt_exp"] < _time.time():
        return None
    return user

def issue_session_jwt(user: dict, secret: str, ttl_s: int = 7 * 24 * 3600) -> str:
    """Mint a 7-day JWT bound to (chain_id, address, user_id)."""
    payload = {
        "sub": user["id"],
        "addr": user["address"],
        "chain": user.get("chain_id") or 43114,
        "iat": int(_time.time()),
        "exp": int(_time.time()) + ttl_s,
    }
    return _jwt_sign(payload, secret)

def verify_session_jwt(token: str, secret: str) -> dict | None:
    """Verify and return the JWT payload, or None."""
    return _jwt_verify(token, secret)


# ───────── push subscriptions (Web Push) ─────────
def save_push_subscription(user_id: int, endpoint: str, p256dh: str, auth: str, user_agent: str = "") -> int:
    """Insert or update a Web Push subscription. Returns the row id.

    Audit 20 Oct round 6: the UPDATE branch matched on `endpoint` alone. Since
    that column is UNIQUE, any authenticated caller could post a *known* push
    endpoint and silently take the row over — `user_id` was rewritten, so the
    previous owner stopped getting notifications. A cross-user write. Now the
    match is scoped to (endpoint, user_id): you can refresh your own
    subscription, but you cannot claim someone else's.
    """
    with conn() as c:
        now = int(time.time())
        existing = c.execute(
            "SELECT id FROM push_subscriptions WHERE endpoint = ? AND user_id = ?",
            (endpoint, user_id),
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE push_subscriptions SET p256dh = ?, auth = ?, user_agent = ?, "
                "is_alive = 1, last_used_at = ? WHERE id = ?",
                (p256dh, auth, user_agent, now, existing["id"]),
            )
            return existing["id"]
        # Endpoint already held by someone else: refuse rather than steal it.
        taken = c.execute(
            "SELECT user_id FROM push_subscriptions WHERE endpoint = ?",
            (endpoint,),
        ).fetchone()
        if taken:
            raise ValueError("push endpoint already registered to another account")
        cur = c.execute(
            "INSERT INTO push_subscriptions(user_id, endpoint, p256dh, auth, user_agent, created_at, last_used_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (user_id, endpoint, p256dh, auth, user_agent, now, now),
        )
        return cur.lastrowid


def get_push_subscriptions_for_user(user_id: int) -> list[dict]:
    """Return all live push subscriptions for a user. Format matches what
    the Web Push Protocol expects (the dict structure the browser gave us)."""
    with conn() as c:
        rows = c.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ? AND is_alive = 1",
            (user_id,),
        ).fetchall()
    return [{"endpoint": r["endpoint"], "keys": {"p256dh": r["p256dh"], "auth": r["auth"]}} for r in rows]


def delete_push_subscription(endpoint: str, user_id: int = 0) -> bool:
    """Mark a subscription as dead (soft delete) or hard delete it."""
    with conn() as c:
        if user_id:
            cur = c.execute("UPDATE push_subscriptions SET is_alive = 0 WHERE endpoint = ? AND user_id = ?", (endpoint, user_id))
        else:
            cur = c.execute("UPDATE push_subscriptions SET is_alive = 0 WHERE endpoint = ?", (endpoint,))
        return cur.rowcount > 0


def cleanup_dead_subscriptions(user_id: int) -> int:
    """Drop dead subscriptions (called when push service returns 404/410)."""
    with conn() as c:
        cur = c.execute(
            "UPDATE push_subscriptions SET is_alive = 0 WHERE user_id = ? AND is_alive = 1 AND last_used_at < ?",
            (user_id, int(time.time()) - 86400 * 30),
        )
    return cur.rowcount


# ───────── link codes (audit C3: OTP-based telegram link) ─────────
def create_link_code(user_id: int, ttl_seconds: int = 600) -> str:
    """Create a one-time code the user sends to the Telegram bot to
    prove they own this telegram_id AND this wallet. Returns the code."""
    code = "".join([str((user_id * 31 + i * 7) % 10) for i in range(6)])
    # Make it actually random, not deterministic:
    import secrets
    code = f"{secrets.randbelow(1000000):06d}"
    with conn() as c:
        c.execute(
            "INSERT INTO link_codes(code, user_id, expires_at, created_at) VALUES(?, ?, ?, ?)",
            (code, user_id, int(time.time()) + ttl_seconds, int(time.time())),
        )
    return code


def consume_link_code(code: str, telegram_id: int) -> dict | None:
    """Validate a link code, mark it used, and return the user_id it
    belongs to. Returns None if the code is unknown, expired, or already
    used. The telegram_id is recorded but not enforced here — the bot
    passes the id of the chat that sent the code, so this becomes
    the binding between wallet and chat."""
    now = int(time.time())
    with conn() as c:
        row = c.execute(
            "SELECT id, user_id, expires_at, used_at FROM link_codes WHERE code = ?",
            (code,),
        ).fetchone()
        if not row: return None
        if row["used_at"]: return None
        if row["expires_at"] < now: return None
        c.execute("UPDATE link_codes SET used_at = ? WHERE id = ?", (now, row["id"]))
    return get_user(user_id=row["user_id"])


def link_telegram_to_user(token: str, secret: str, telegram_id: int) -> dict | None:
    """Verify a JWT and stamp the user's telegram_id onto the matching
    user record. This is how a wallet-connected web user becomes
    reachable by the Telegram bot — so bridge status notifications
    can be sent to their Telegram chat.

    Returns the user dict on success, or None on invalid/expired token.
    If the user already had a telegram_id set, it's overwritten (the
    user explicitly chose to link, so this is intentional).
    """
    payload = _jwt_verify(token, secret)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    with conn() as c:
        c.execute("UPDATE users SET telegram_id = ? WHERE id = ?", (int(telegram_id), int(user_id)))
        row = c.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    return dict(row) if row else None
