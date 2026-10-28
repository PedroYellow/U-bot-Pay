# The other two whitelabels: usolpay and utronpay

The Sonic deployment's audit turned up a pattern - the whitelabels were
forks of one codebase, and the code changed while everything *around*
it did not. The same turned out to be true of the other two
deployments.

Both are now verified. Neither had the data-corruption bug that Sonic
had: they never went through the env-prefix rewrite, so their chain ids
are their own and correct. What they had was a test suite that had
drifted off its own deployment.

---

## usolpay - the Solana deployment

**13/13 suites pass.** It had 4 failures.

### Three tests pointed at a local development path

```python
sys.path.insert(0, os.environ.get("ENGINE_ROOT", "/data/workspace/ubotpay_tron"))
PROFILE_PATH = os.environ.get("PROFILE_PATH", "/data/workspace/ubotpay_tron/profiles/usolpay.yaml")
```

That path exists on a developer's machine and nowhere else. On the
server the tests failed with:

```
FileNotFoundError: No such file or directory:
'/data/workspace/ubotpay_tron/profiles/usolpay.yaml'
```

which reads like a missing profile rather than a path pointing at the
wrong machine.

Each default is now the directory the test file lives in.

### One test never loaded the environment

`api.py` refuses to import without its secrets:

```
RuntimeError: USOL_API_KEY must be set: server-to-server auth
```

`test_bot_flow.py` already loaded the deployment's `.env` before the
imports. `test_usolpay_e2e.py` did not, so it died at the import with
a message about a missing API key - which reads like a configuration
problem rather than a test that forgot to load the environment.

### A settled transaction read as pending - for ever

This one was a real bug, and it was in the service, not the test.

The engine read a transaction's status with `getTransaction`:

```python
r = await c.get_transaction(signature, max_supported_transaction_version=0)
if getattr(r, "value", None) is None:
    return {"status": "pending", ...}
```

The comment said why: *"the public endpoint answers null for a
signature that IS on chain (it only keeps recent statuses in memory),
which would make every settled payment read as pending forever."*

But that is only half true, and the two methods fail in **opposite**
directions:

| method | drops |
|---|---|
| `getTransaction` | OLD transactions - the node aged them out of its cache |
| `getSignatureStatuses` | RECENT ones the node has not finalised yet |

So a payment from a while ago read as pending forever, which is the
opposite failure from the one the comment described.

`getSignatureStatuses` with `searchTransactionHistory=True` finds the
old one - it asks the node to look through its history rather than
only its recent cache. Verified against the public mainnet RPC:

```
getSignatureStatuses -> confirmationStatus "finalized", err null, slot 460603110
```

**And then the fallback timed out.** The history scan is slow, and the
default httpx timeout was not enough for it:

```
SolanaRpcException <class 'httpx2.ReadTimeout'> raised in
"GetSignatureStatuses" endpoint request
```

so the fallback returned "pending" - the same answer the bug had. The
fallback now builds its own client with a 45 second timeout.

The endpoint now answers what the chain actually says:

```json
{"status": "confirmed", "error": null, "slot": 460603110}
```

### The balance endpoint test asserted the behaviour an audit removed

The endpoint is authenticated:

```python
@ app.get("/balance")
def balance(x_api_key: str | None = Header(default=None), request: Request = None):
    """Balance for the demo user OR for the JWT-authenticated user."""
```

A balance is an inventory of a spendable asset, and the audit made it
so. The test called it publicly and expected 200. It also used a path
parameter the route does not have - the route is `/balance`, not
`/balance/<address>`.

The test now calls the real route with the deployment's key, and adds a
check that the endpoint refuses an unauthenticated caller - which is
the property worth keeping.

---

## utronpay - the Tron deployment

**40 suites pass, 3 moved aside.** It had 5 failures.

### The env shim, and why the variable name looked wrong

`api.py` reads:

```python
API_KEY = os.environ.get("Utron_API_KEY")
```

The deployment's `.env` has `UTRON_API_KEY`. **On Linux, environment
variable names are case-sensitive**, so that lookup finds nothing -
except that each whitelabel has an `_env_shim` that maps the names
onto each other:

```python
("UTRON_API_KEY", "UBOT_API_KEY"),
("UTRON_API_KEY", "Utron_API_KEY"),
```

Three spellings exist in the inherited code - `UTRON_*`, `UBOT_*` and
`Utron_*` - and all three have to resolve, or a fresh boot fails on a
missing secret while a long-running process keeps working from its old
environment.

The shim runs once, before any module-level code reads `os.environ`.
The service imports it first, in `main_utron.py`:

```python
import _env_shim     # noqa: F401  must run first
import api, tg
```

A test that imports `api` directly does not get the shim. So the
module asked for `Utron_API_KEY` when the environment only had
`UTRON_API_KEY`, and the import failed with:

```
RuntimeError: Utron_API_KEY must be set
```

which reads like a deployment whose secrets were never configured.

The tests now import the shim first, and their subprocesses import it
too - a `python -c` subprocess imports nothing.

### The subprocess ran the MAIN deployment's interpreter

```python
["/opt/ubotpay/venv/bin/python", "-c", code]
```

The utronpay deployment has its own venv - `/opt/utronpay/venv` - and
its own dependencies. The two pin `eth-abi` differently and cannot
share a venv, which is why the deployment carries a `web3_stub`: the
Tron venv has tronpy, not web3, and the stub provides the two Web3
helpers the inherited code uses.

Running the main deployment's interpreter against the utronpay tree was
running one deployment's code under another deployment's libraries.

### Three tests belong to the main deployment

| test | why it cannot run on Tron | on the main deployment |
|---|---|---|
| `test_dataagent_paid_boundary.py` | imports `dataagent`, which imports web3 | 35/35 |
| `test_chain_registry.py` | reads chain.py's literal `Chain(...)` registry - the Tron chain.py is profile-driven | 21/21 |
| `test_bridge_destinations.py` | the same shape, on bridge.py | 37/37 |

All three pass on the main deployment, which is where they belong. The
dataagent service itself runs from `/opt/ubotpay`; the copy of
`dataagent.py` in the Tron deployment is unused dead code.

### The decimals regression pin looked for a function that is not there

The pin was:

```python
i = src.index("def _balance_usdc(")
```

It is a regression pin for the main repository's `chain.py`, which
keeps a module-level `_DEC` and used to reassign it inside the balance
function. The Tron `chain.py` is profile-driven and delegates to the
engine, so there is no such function - and the pin raised
`ValueError: substring not found` and the file died before asserting
anything.

The pin now scans the whole file for a `global _DEC` outside
`decimals()`. That is a **stronger** check - it catches the assignment
anywhere, not only in one function - and it works on every
architecture.

---

## The result

| deployment | chain | suites |
|---|---|---|
| `/opt/ubotpay` | Avalanche 43114 | **42 passed** |
| `/opt/usonicpay` | Sonic 146 | **6 passed** |
| `/opt/usolpay` | Solana | **13 passed** |
| `/opt/utronpay` | Tron | **40 passed** |

The three tests moved aside from utronpay are in
`tests-main-deployment-only/` there, and the nine Solana tests moved
aside from Sonic are in `tests-solana-not-applicable/` there.

## The pattern, and the rule

A whitelabel is a fork. The code changes when the chain changes. The
things around the code - the paths, the variable names, the shape of
the balance, the constructor - do not change unless someone changes
them, and nothing fails when they do not. The deployment keeps
answering 200.

So the rule for a whitelabel: **every default in a test points at the
deployment the test file lives in**, and every assertion about a
chain asks the deployment what it is rather than assuming.

## The scripts

| script | what it fixes |
|---|---|
| `fix_deployment_paths.py` | the local dev path in a test |
| `fix_env_loader.py` | the .env not loaded before the imports |
| `fix_env_shim_import.py` | the env shim not imported first |
| `fix_subprocess_deployment.py` | the subprocess using another deployment's venv |
| `fix_test_env_prefix.py` | the subprocess setting another deployment's variable names |
| `fix_solana_tx_status.py` | `getTransaction` dropping old transactions |
| `fix_tx_status_timeout.py` | the history scan timing out |
| `fix_balance_endpoint_assertion.py` | the endpoint test and its auth |
| `fix_decimals_pin.py` | the pin looking for a function that is not there |
| `clean_stale_tests_tron.py` | the tests that cannot run on Tron |
| `clean_stale_tests.py` | the Solana tests that cannot run on a non-Solana deployment |

---

## Two more that needed the same treatment

### test_nonce_db_atomic.py - also a main-deployment test

It pins `dataagent._remember_nonce` - the round 27 fix for a nonce
pinned in memory when SQLite raises. The dataagent service runs from
/opt/ubotpay, and the main deployment's dataagent.py imports web3,
which the Tron venv does not have.

In the utronpay deployment the test HANGS rather than failing: it runs
`import dataagent` in a subprocess with the MAIN deployment's
interpreter but utronpay's tree at the front of sys.path, so it
imports a module that needs web3, in an interpreter that has it, and
the import resolves to the wrong file.

It passes on the main deployment.

### test_pending_gc_and_cache.py - the same subprocess problem

It imports `api` in a subprocess, with no env shim, so the module
asked for Utron_API_KEY when the environment only had UTRON_API_KEY.

The subprocess now runs this deployment's interpreter, imports the shim
first, and gets this deployment's variable names.

### One mistake worth recording

The `chk` helper in the Tron suite is `chk(name, got, want)` and it
compares `got == want`. The first version of the rewritten pin
passed `not offenders` as the value and `offenders[:3]` as the
expectation - so it compared `True == []` and failed, printing
`got=True want=[]`.

The value goes first. A helper's argument order is not a detail.
