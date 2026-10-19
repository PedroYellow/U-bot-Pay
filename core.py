"""
U-bot Pay — core: users, contacts, FX, policy (limits + dual control), ledger.
SQLite. Demo-grade but structured like the real thing.
"""
import os, json, time, sqlite3, hashlib, secrets
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
  raw_quote TEXT,                  -- JSON blob of the full LI.FI quote for replay/debug
  created_at INTEGER,
  confirmed_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_bridges_status ON bridges(status);
CREATE INDEX IF NOT EXISTS idx_bridges_user ON bridges(user_id);
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
        ]:
            try: c.execute(ddl)
            except Exception: pass
        # Backfill users with NULL address to the operator treasury (Avalanche).
        # Avoids (NULL,NULL) collisions on the unique index below.
        if treasury:
            try: c.execute("UPDATE users SET address = ?, chain_id = 43114 WHERE address IS NULL", (treasury,))
            except Exception as e: log.warning("backfill users.address: %s", e)
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
        except Exception as e: print(f"[core.init] dedupe users: {e}")
        try: c.execute("UPDATE contacts SET chain_id = 43114 WHERE chain_id IS NULL")
        except Exception: pass
        try: c.execute("UPDATE payments SET chain_id = 43114 WHERE chain_id IS NULL")
        except Exception: pass
        try: c.execute("UPDATE invoices SET chain_id = 43114 WHERE chain_id IS NULL")
        except Exception: pass
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_wallet ON users(chain_id, lower(address)) WHERE address IS NOT NULL")


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
    if not fields: return False
    sets = ", ".join(f"{k} = ?" for k in fields.keys())
    vals = list(fields.values()) + [bridge_id]
    with conn() as cc:
        cc.execute(f"UPDATE bridges SET {sets} WHERE id = ?", tuple(vals))
    return cc.rowcount > 0

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
def spent_today(user_id) -> float:
    day0 = int(time.time()) - (int(time.time()) % 86400)
    with conn() as c:
        r = c.execute("SELECT COALESCE(SUM(amount_usdc),0) s FROM payments WHERE user_id=? AND status='settled' AND created_at>=?", (user_id, day0)).fetchone()
        return float(r["s"])


def check_policy(user: dict, amount_usdc: float) -> dict:
    out = {"allowed": True, "needs_approval": False, "reason": ""}
    if spent_today(user["id"]) + amount_usdc > (user.get("daily_limit") or 1e12):
        out.update(allowed=False, reason=f"daily limit {user['daily_limit']} USDC exceeded")
    elif amount_usdc >= (user.get("dual_threshold") or 1e12):
        out.update(needs_approval=True, reason=f"amount ≥ {user['dual_threshold']} USDC requires a second approval")
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
