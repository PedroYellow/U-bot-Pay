# Sonic: the audit of the deployment that was actually running

The Sonic deployment (`/opt/usonicpay`, port 8094) was a fork of the
Solana deployment with the Solana engine removed and the EVM engine
put in. The code changed; the things *around* the code did not, and
they were the bugs.

None of them were visible from the outside. The service answered 200,
the dashboard rendered, the Telegram bot replied. The failures were in
the data, in the test suite, and in what the user actually read.

Every fix below was applied to the running deployment and verified.

---

## The hardcoded chain ids

The Solana deployment's chain id, `1151111081099710`, was hardcoded in
eight places in `core.py` and more in `api.py`. Every deployment
inherited them.

The worst were the **schema defaults**:

```sql
CREATE TABLE contacts(
  ...
  chain_id INTEGER DEFAULT 1151111081099710, ...)
```

and `add_contact` did not write the column at all - it relied on that
default. So every contact created in the Sonic deployment was on
Solana's chain.

It does not look wrong. The contact is found, the balance works, the
payment flows. But the row is on the wrong chain, and the unique index
is on `(user_id, alias, chain_id)` - so the same alias on the right
chain is a *different row*.

The same class of bug: the SQL statements that backfill the chain id
had the literal text `chain.CHAIN_ID` inside the SQL string, where a
number belongs:

```python
c.execute("UPDATE users SET address = ?, chain_id = chain.CHAIN_ID WHERE id = ?", ...)
```

Python never sees inside a string literal, so the env-prefix rewrite
did this textually and the SQL shipped broken. SQLite read
`chain.CHAIN_ID` as a column reference and answered "no such column",
and because the statements are wrapped in try/except, **the service
carried on and the rows were quietly wrong**.

Fixed: the schema's defaults come from `chain.CHAIN_ID`, `add_contact`
writes the column, the backfill statements take it as a parameter,
and the session JWT's chain fallback is the deployment's own.

### The rows that were already wrong

`upsert_user` only sets the chain when the row has none:

```python
if not r["chain_id"]:
    c.execute("UPDATE users SET chain_id=? WHERE id=?", ...)
```

That is correct - a user who already has a chain keeps it, so
re-resolving from the demo wallet cannot move them to another chain.
But it means a row written with the WRONG chain id can never be
corrected. `deploy/fix_wrong_chain_rows.py` reports and fixes them.

---

## The order-dependent config

```python
PREFIX = os.environ.get("DEPLOY_PREFIX", "USOL").strip().upper()
```

A module constant, computed at import time. If anything imported
`config` before the deployment's `.env` was loaded, the prefix froze
as the default and every lookup went to the wrong variable name. The
service then asked for `USOL_API_KEY` in a deployment that reads
`USONIC_API_KEY`.

It was not only the service. A test that read its own profile before
loading the env file imported `config` on the way, froze the prefix,
and the failure looked like a missing secret.

Fixed: the prefix is read on each use. `PREFIX` is still importable -
other modules do `from config import PREFIX` - as a string subclass
that carries the current value.

### The profile name

With the prefix read late, the fallback profile name was derived by
arithmetic: `env_prefix[4:]`. For `USONIC` that is `IC`, and the
deployment went looking for `ic.yaml`.

The profile name is not derivable by arithmetic: `USONIC` ->
`usonicpay`, `USTART` -> `ustartkpay`. It comes from the deployment's
own `profiles/` directory.

---

## The registry claimed a chain it does not have

`chains/registry.py` is shared code with a branch per chain kind. The
Sonic deployment has no `chains/solana` and no `chains/solana_signer`
- they were removed with the Solana engine - but the branch stayed:

```
ModuleNotFoundError: No module named 'chains.solana'
```

which reads like a broken deployment rather than "this deployment does
not support Solana".

Fixed: the branch asks whether the engine exists and says so plainly
- *"this deployment has no Solana engine; it runs evm"*.

---

## The bot read the wrong balance shape

`tg.py` read the balance in the Solana shape and nothing else:

```python
toks = d.get("tokens") or {}
amt = tinfo.get("settled") or 0
native_sym = d.get("native_symbol") or "SOL"
```

The EVM engine returns a flat `usdc` figure and a `native` figure. So
on Sonic the tokens map was empty, the code fell through to
`d.get("settled")` - a key that does not exist on EVM - and the user
got:

```
💰 None USDC · 0.0 SOL
```

"None" because there is no "settled" key, and "SOL" because the
fallback symbol was Solana's. On a Sonic deployment.

Fixed: the reply reads whichever shape the engine returned.

The EVM keys are easy to get wrong, and the first fix got them wrong
too:

```python
{"address": ..., "native": "S", "native_balance": 0.0,
 "chain": "Sonic", "chain_id": 146,
 "usdc": 0.0, "stable_balance": 0.0}
```

`native` is the **symbol**; `native_balance` is the amount. Taking
`native` as the amount printed the symbol twice - "S S".

`chain.py` also did not export `NATIVE_SYMBOL` at all, only
`STABLE_SYMBOL`, so anything that wanted the chain's own currency
fell back to a hardcoded "AVAX".

Fixed: `NATIVE_SYMBOL` is exported, and it resolves to `S`.

---

## The tests were testing the wrong deployment

Nine of the fifteen test files in the Sonic deployment could not run.
They imported `solders`, `base58` or `chains.solana_signer` - all
gone with the Solana engine - and failed with `ModuleNotFoundError`,
which reads like a broken deployment rather than a stale test.

A test that cannot apply to this deployment is not a test. It is noise
that makes a green suite look red and hides a real failure. They are
moved to `tests-solana-not-applicable/`.

The rest had the same problem one level down:

```python
sys.path.insert(0, os.environ.get("ENGINE_ROOT", "/opt/usolpay"))
PROFILE = os.environ.get("PROFILE_PATH", "/opt/usolpay/profiles/usolpay.yaml")
ENV_FILE = os.environ.get("USOLPAY_ENV_FILE", "/opt/usolpay/.env")
```

Every default pointed at the **Solana** deployment. The tests loaded
usolpay's registry, usolpay's profile and usolpay's secrets - and
then failed on a Solana engine Sonic does not have. The same test would
have PASSED against usolpay while proving nothing about Sonic.

Fixed: each default is the directory the test file lives in.

### The assertions that described Solana

With the right deployment loaded, the assertions still described
Solana:

| assertion | Solana | Sonic |
|---|---|---|
| "the user records the Solana chain id" | `1151111081099710` | `146` |
| "the balance reports 'settled', not 'usdc'" | `settled` | `usdc` |
| "engine validates it as a Solana address" | base58 | `0x...` |
| "the balance reply does not say 'None AVAX'" | AVAX | S |

Fixed: they ask the deployment what it is. The revenue address in
particular was a hardcoded Solana base58 address, which the EVM engine
correctly rejects - the assertion was checking Solana on Sonic.

### The contact

`core.find_contact` filters on the **users table's id** - the internal
id - and the API does `uid = user["id"]`. The test created the
contact for one telegram id and sent the message as another, so the
lookup found nothing and the bot answered "I don't have mom in your
contacts."

Fixed: the contact goes on the row of the user the bot sends as.

### The assertions that needed a funded treasury

Two of them could not pass on an unfunded treasury:

- "the token with a balance is named first" - with everything at zero
  no ordering can satisfy it
- "four concurrent confirms settle exactly once" - "settled" means
  the payment went on chain, and the treasury cannot pay

The concurrent-confirm one is worth being precise about, because the
guard it protects is real. `_pending.pop(uid, None)` is atomic, so
exactly one thread takes the pending payment and the other three find
nothing. That is the double-settlement guard holding. The assertion
used to require the action to be "settled", so it reported the guard
as broken when the guard had held and the send had failed.

Both now check what they can prove, and report the treasury state
instead of calling it a defect.

---

## The result

```
test_audit_findings.py       PASS
test_balance_guard.py        PASS
test_bot_flow.py             PASS
test_deploy_env.py           PASS
test_profile_loader.py       PASS
test_voice_webhook.py        PASS
```

Six suites, all passing. The service runs on chain 146, every row is
on chain 146, and the balance reply reads:

```
You have 0.0 USDC, about 0.0 USD.
💰 0.0 S · 0.0 USDC
```

## What is still missing

**The treasury holds nothing.** It has 0 S and 0 USDC. The quotes,
the confirmations and the payment flow all work; a real payment cannot
be sent until the treasury is funded.

**The other deployments were not touched.** `/opt/ubotpay` (the main
Avalanche deployment), `/opt/usolpay` (Solana) and `/opt/utronpay`
(Tron) do not have these bugs - none of them went through the
env-prefix rewrite. The main deployment's `core.py` has no
`chain.CHAIN_ID` in its SQL.

**The fixes live in the deployment, not yet in the repo's main tree.**
The repository's `core.py`, `api.py` and `tg.py` are the main
deployment's own copies. The Sonic versions are in
`deploy/usonicpay_patches/` as the reference for what the whitelabel
needs.

## The scripts

Each fix is a script in `deploy/`, so it can be re-run and audited:

| script | what it fixes |
|---|---|
| `clean_stale_tests.py` | the Solana tests that cannot run here |
| `fix_sql_chain_id.py` | `chain.CHAIN_ID` inside SQL string literals |
| `fix_schema_chain_defaults.py` | the schema's hardcoded chain defaults |
| `fix_contacts_chain_id.py` | `add_contact` not writing the chain id |
| `fix_user_chain_fallback.py` | the JWT's chain fallback |
| `fix_lazy_config_prefix.py` | the order-dependent config prefix |
| `fix_profile_name_derivation.py` | `USONIC` -> `ic.yaml` |
| `fix_default_profile_name.py` | the literal `"usolpay"` fallback |
| `fix_registry_solana_branch.py` | the branch for a chain it does not have |
| `fix_native_symbol_export.py` | `chain.NATIVE_SYMBOL` missing |
| `fix_tg_balance_shape.py` | the balance reply reading one shape |
| `fix_tg_balance_amounts.py` | `native` is the symbol, not the amount |
| `fix_engine_root.py` | the tests pointing at another deployment |
| `fix_profile_path.py` | the same, for the profile |
| `fix_revenue_address_test.py` | the Solana revenue address |
| `make_tests_deployment_aware.py` | the Solana-shaped assertions |
| `fix_bot_flow_balance_assertions.py` | the balance field and symbol |
| `fix_bot_flow_final_assertions.py` | the chain id passed to upsert_user |
| `fix_bot_flow_evm_remaining.py` | the sorting and the contact |
| `fix_bot_flow_contact_fake_user.py` | the contact on the right user |
| `fix_bot_flow_contact_target.py` | the same |
| `fix_bot_flow_contact_user.py` | the contact on the internal id |
| `fix_bot_flow_contact.py` | the malformed contact rows |
| `fix_balance_first_assertion.py` | the string check on a number |
| `fix_balance_first_assertion_2.py` | the empty treasury |
| `fix_concurrent_confirm_assertion.py` | the double-settlement guard |
| `fix_audit_findings_balance_assertion.py` | the engine's balance field |
| `inspect_user_rows.py` | the users table |
| `fix_wrong_chain_rows.py` | every table's wrong-chain rows |
| `fix_default_profile_name.py` | the profile fallback |
