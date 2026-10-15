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
  telegram_id INTEGER UNIQUE, voice_pin TEXT, address TEXT, daily_limit REAL DEFAULT 500,
  dual_threshold REAL DEFAULT 1000, approver_telegram_id INTEGER, created_at INTEGER);
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, alias TEXT, name TEXT, address TEXT,
  country TEXT, currency TEXT DEFAULT 'USDC', is_agent INTEGER DEFAULT 0, UNIQUE(user_id, alias));
CREATE TABLE IF NOT EXISTS payments(
  id TEXT PRIMARY KEY, user_id INTEGER, contact_alias TEXT, to_address TEXT,
  amount_usdc REAL, amount_local REAL, local_ccy TEXT, fx_rate REAL, fee_usdc REAL DEFAULT 0,
  memo TEXT, memo_hash TEXT, status TEXT, tx TEXT, latency_s REAL, channel TEXT,
  needs_approval INTEGER DEFAULT 0, approved_by INTEGER, created_at INTEGER, settled_at INTEGER);
CREATE TABLE IF NOT EXISTS invoices(
  id TEXT PRIMARY KEY, user_id INTEGER, from_name TEXT, from_address TEXT, amount_usdc REAL,
  description TEXT, due INTEGER, status TEXT DEFAULT 'open', paid_tx TEXT, created_at INTEGER);
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
    with conn() as c:
        c.executescript(SCHEMA)


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
