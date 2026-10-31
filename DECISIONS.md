# The six decisions — with the numbers, as of 2026-10-29

Everything here was measured, not estimated. Each item says what it
is, what it costs, and what happens if it is not done.

Nothing here has been executed. No funds have moved.

**1 and 6 cost nothing.** The other four move money and need the
user's explicit go-ahead on how much to put in each. That is why the
numbers are given here rather than acted on.

---

## 1. The orphaned Chromium on the VPS — DONE, 31 Oct

**It was executed. 191 orphan processes killed; the CDP browser was
kept.**

```
before   the chrome processes: 201     load 154
after    the chrome processes: 10      load 240  (the 1-minute average
                                               still counts the processes
                                               that were running)
+2 min   the chrome processes: 12      load 4.64
```

The load is a 1, 5 and 15 minute average, so it reads high for a
while after the kill and then falls.

| | before | after |
|---|---|---|
| the chrome processes | 201 | 12 |
| the load average | 142-157 | **4.64** |
| `import api` | 51 s | 21 s |
| `import web3` | 43 s | 14 s |
| `test_cors.py` | slow | **1 s** |

**What it did NOT fix.** `import api` is still 21 s, and it is not I/O
and not saturation:

```
User time:           15.48 s
System time:          3.40 s
Percent of CPU:        99 %
File system inputs:    0
```

99 % CPU, zero file reads. The disk has 87 G free and 2 % of its
inodes; 6.6 G of RAM is available. So the import costs real CPU on
this machine, and the Chrome processes were stealing part of it - now
they are not, and the rest is the code being genuinely expensive to
import on two slow vCPUs.

That is a separate question from the orphans, and it is not a
security matter.

**The history, for the record:**

**209 processes, 23 browsers, across 24 CDP ports (9333-9370).**
Load average 59.54 on 2 CPUs.

They are leftovers from the browser testing: the driver exited without
cleaning them up, they were reparented to init, and they have been
burning ~218% of CPU for three and a half days.

**The evidence they are orphans** — checked before proposing anything:

| check | result |
|---|---|
| a driver process | none — no node or python is controlling a browser |
| ESTABLISHED sockets to their ports | none |
| their ages | 89h, 101h, 102h, 103h, 114h, 118h |
| the youngest | **89 hours** |
| their pages | all `about:blank` |

**What it costs now.** Every NEW python process pays for the CPU
starvation:

```
import web3        -> 43 s
import api         -> 46 s
import dataagent   -> 78 s
```

The running services are **not** affected — they imported web3 once,
at boot, and their health endpoints answer in 8-516 ms. But every test
and every deploy crawls, and any measurement describes the load rather
than the code. It is why `test_nonce_db_atomic.py` takes 478 seconds.

**The command** (a dry run by default — it reports and changes
nothing):

    ssh hvps 'sh /opt/ubotpay/cleanup_orphaned_chrome.sh'          # report
    ssh hvps 'sh /opt/ubotpay/cleanup_orphaned_chrome.sh --go'    # do it

It keeps the ~10 processes on the `chrome-cdp-prof` profile — the
browser the DOM-inspection skill connects to. Everything else goes.

**Reversible**: the browser is not a service. The next session starts
its own.

**If not done**: everything stays working, and everything stays slow.

---

## 2. The Avalanche treasury has no gas

```
the USDC: 18.32
the AVAX: 0.0000
```

**Zero AVAX means zero gas**, so the treasury cannot send. And 18.32
USDC at the observed payment sizes is about six transfers before it
is empty.

Volume so far: 10 payments, 2,730 USDC, 8 settled and verified on
chain.

**What it needs**: a small amount of AVAX for gas. A few dollars is
enough — the observed transactions used 45,059-62,159 gas.

**If not done**: the service can read balances but not move money.

---

## 3. The Sonic treasury is empty

The Sonic treasury has **0 S**. Every transaction on Sonic pays its
fee in S, so it cannot send either.

**The quote** — measured, not assumed:

```
50 USDC on Avalanche  ->  ~1,068.86 S on Sonic
total cost            ->  ~$0.235
route                 ->  LayerSwap
```

**What it needs**: your OK to spend ~$0.24 of your own USDC to get
the S. The USDC comes from somewhere — either the Avalanche treasury
(which has 18,32) or a separate source.

**If not done**: the Sonic deployment stays read-only.

---

## 4. Starknet mainnet needs funding

The account is **prepared**:

```
address    0x5378d0a08c1447c5d76095b47ae4b741afb27a7a0f54ee54b3188e516d5c2d4
key        saved at 0600, before anything touched the chain
class      OpenZeppelin v1.0.0, confirmed deployed on mainnet
contract   NOT deployed
STRK       0
```

The bootstrap stops at the funding gate on purpose — there is no
mainnet faucet, and the deployment has to be paid for in STRK.

**What it needs**:

- STRK for the gas, and USDC for the treasury, sent to that address
  via StarkGate or a Starknet Foundation grant
- then the contract is deployed and the service can sign

**Why it cannot be automated**: StarkGate is a bridge with its own
steps, and the Foundation grant is a request to a person. Both need
you.

**If not done**: ustartkpay serves balances and quotes correctly
(`chain: Starknet`, `SN_MAIN`) and cannot send.

---

## 5. The Solana treasury has no USDC

Found on 29 Oct, while running the whitelabel suites again.

```
the treasury: BX6sfCwA9CkVqfpppco3HgXi3gCMhxQgBFoH4yLpJk55
the SOL:     0.099626      <- gas, and that is all there is
the USDC:    0             <- it does not even have the ATA
```

The chain's own log says it:

```
Program log: Error: insufficient funds
Program TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA failed:
custom program error: 0x1
```

**0.0996 SOL is the gas, not the token.** On Solana the fee is paid in
SOL and the token is transferred separately, so a treasury can hold
enough SOL to sign a transaction and still have nothing to send.

It shows up in the tests as two failures that look like software
faults and are not:

  - the concurrent-confirm test expected "settled" and got "failed"
  - the transaction simulation expected the chain to accept a USDC
    transfer and got "insufficient funds"

Both are the chain and the guard working correctly. The tests now say
what they can actually prove - that exactly one of four racing
confirms takes the pending payment - and the settle/fail split is
printed as a note.

**What it needs**: USDC in the treasury. The SOL it already has
covers the fee.

**If not done**: the Solana deployment can read balances and build
transactions, but cannot send.

---

## 6. The Stargate Foundation email

Every number in it was measured today:

| | |
|---|---|
| the pool's USDC | **67,335.87**, read straight from the contract |
| the LP tokens outstanding | **0.00** |
| the largest transfer that routes from Avalanche | **$882** (returns 878.54 USDC) |
| $1,000 from Avalanche | no quote |
| from Ethereum | routes past $10,000 |

The ask is narrow and specific: **mint LP against the USDC already in
the contract**. Not "fund the pool" — the USDC is there. It is
collateral with nothing minted against it.

**Before sending, re-run**:

    cd /data/workspace/ubotpay
    python3 deploy/verify_stargate_pool.py     # the balance
    python3 deploy/find_stargate_threshold.py   # the threshold

Both numbers move. The pool went 27,874 -> 27,339 -> 67,315.78 ->
67,335.87 in two days.

---

## What is already done and verified

| | |
|---|---|
| the main suite | 43/43 |
| utronpay | 39 suites |
| usolpay | 13 suites |
| usonicpay | 6 suites |
| Starknet | 31 + 12 + 5 + 8 |
| the public endpoints | 45/45, 0 failures, across all five API domains |
| the cron jobs | 6/6 |
| the services | 6/6 active |
| the sites | 6/6 at 200 |
| the venvs | 5/5 clean under pip check |
| the drift check | 0 failures, including the deploy scripts |
| the balance guard | 5/5 deployments OK |
| the Starknet deploy | EXIT=0, five times in a row |
| the settled payments | 8/8 verified on chain |
| the blocklist sync | EXIT=0, verified against production |
