# Test isolation: a test must not write to a deployment's database

28 Oct 2026.

## The bug

Two deployments had the same line in their wallet-ratelimit test:

    env.setdefault("UBOT_DB", "/tmp/test_wallet_rl.db")

Two things are wrong with it.

**The path was fixed.** Every run used the same database file, so two
concurrent runs raced on it. One of them failed with an error that
looked like a product defect:

    RuntimeError: subprocess failed: ...

**`setdefault` does not set a variable that is already set.** And
`UBOT_DB` WAS already set - in the deployment's `.env`, and in the
service's own environment. So whenever the suite ran with that env
loaded, the test pointed at the **live** database.

The value is now assigned, not defaulted, and it is a fresh temporary
directory:

    _db_dir = tempfile.mkdtemp(prefix="wallet_rl_")
    env["UBOT_DB"] = os.path.join(_db_dir, "test.db")

## How bad was it?

Less than it looks, and it is worth being precise about that rather
than overstating it.

The test only calls the in-memory rate-limit helpers:

    A._wallet_nonce_ip_allow('1.2.3.4')
    A._wallet_nonce_addr_allow('0xabc')

It creates no user and no payment. And the rate-limit buckets are
**in memory**, not in a table - the live database has no `nonces`
table at all.

So the live database was **not** written to by this test. The
observable effect was the concurrency race: one run failing with an
error that read like a defect.

The `setdefault` was still a real hazard - it would have pointed the
test at the live database the moment the code grew to touch it, and
the whole point of a test's own database is that it cannot.

## The rows that ARE in the live database

26 user rows, and they are NOT from this test:

    tg=                 wallet:0x0000...dEaD       chain=43114
    tg=                 wallet:0x6b39...6Ed2       chain=43114
    ... 21 wallet rows, one per bound wallet
    tg=                 Demo User                 chain=43114
    tg=4242             otro                      chain=43114
    tg=4343             otro                      chain=43114
    tg=648129483        Pedro Miranda             chain=43114
    tg=999999001        audit test                chain=43114

The `wallet:0x...` rows have **no creation timestamp** - they come
from `upsert_user_by_wallet`, the legitimate wallet-binding path.

`otro` and `audit test` are from earlier test work, and they are
harmless: no address, no balance, no activity.

The one real row is `Pedro Miranda` (tg 648129483) - that is you.

## The rule

A test that runs in a deployment gets its own database, in a
temporary directory, assigned - never defaulted. `setdefault` on a
path that the deployment's own environment already sets is a test
that points at production.
