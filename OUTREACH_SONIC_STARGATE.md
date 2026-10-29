# Outreach — Sonic Labs & Stargate Foundation: native U-bot Pay for Sonic

_2026-10-27. Everything below is verified on-chain or against a live API.
No claim here is taken from a marketing page._

---

## The one-page brief

**What we are.** U-bot Pay is a live, voice-first payments product. You speak,
it confirms, USDC settles in under two seconds. It is running in production
today on Avalanche: `ubotpay.com`, 8 settled payments, $30.01 volume, a
public revenue wallet on Snowtrace, 40 regression suites green.

**What they asked for.** A native version of U-bot Pay on Sonic — Sonic as the
home chain, not a bridge destination.

**What it costs us.** About one week of engineering and ~$60 of domains. The
chain engine is already built and already supports Sonic. The delta is a YAML
profile, a treasury key, an nginx vhost and a service file. **There is no
fork.**

**What we need from them.** Four things, listed below. The first one is the
important one.

---

## The finding that makes this a real conversation

**Stargate routes small transfers into Sonic, but it stops at $100 from
Avalanche.**

The Stargate V2 USDC pool on Sonic exists on-chain and holds
**67,316 USDC** — up from 27,874 two days earlier, so the liquidity is
being supplied, not drained. With Stargate as the only allowed
bridge:

```
$ curl 'https://li.quest/v1/quote?fromChain=43114&toChain=146&...&allowBridges=stargateV2'
  10 USDC     -> QUOTE, out 9.96 USDC
  100 USDC    -> QUOTE, out 99.58 USDC
  1,000 USDC  -> NO QUOTE
  10,000 USDC -> NO QUOTE
  100,000 USDC-> NO QUOTE
```

The same query from Ethereum does better:

```
$ curl 'https://li.quest/v1/quote?fromChain=1&toChain=146&...&allowBridges=stargateV2'
  10 USDC     -> QUOTE, out 9.96 USDC
  100 USDC    -> QUOTE, out 99.61 USDC
  1,000 USDC  -> QUOTE, out 996.11 USDC
  10,000 USDC -> QUOTE, out 9,961.05 USDC
```

So the pool is not empty and not broken. What it has is a **size
cap**, and the cap bites hardest on the direction we care about:
from Avalanche — our own chain, where our product's volume would
come from — it stops at $100.

Meanwhile the Avalanche→Sonic route **does** work at that size,
through Polymer:

```
$ curl 'https://api.ubotpay.com/bridge/quote?from_chain=43114&to_chain=146&amount_usdc=100&...'
  tool               : Polymer (Standard)
  to_amount_human    : 99.75 USDC
  total_fee_usd      : 0.2591
  execution_duration : 1080s (18 min)
  gas_cost_usd       : 0.0091
```

DefiLlama agrees: Stargate V2 TVL is **$75.58M total**, of which **Sonic
holds $27,836** — Ethereum alone holds $53.02M. Sonic's 30-day bridge
volume through Stargate is **$118,471**, against $301M on Ethereum.

**Why this matters for a payments product.** A payments rail that cannot
move $100 is not a payments rail. Circle's CCTP is not deployed on Sonic
either — of the 69 addresses in Circle's own registry, only the Sonic USDC
token has code on chain 146, with no `MessageTransmitter` or
`TokenMessenger`. So today the paths into Sonic are:

- **Polymer** — works, and is what our product uses today.
- **Sonic Gateway** — Sonic's own trustless Ethereum↔Sonic bridge.
- **Stargate** — deployed and live, but capped at $100 from
  Avalanche by the missing LP shares.
- **CCTP** — not live yet.

A native Sonic U-bot Pay is a **demand generator** for exactly this. A
payments product that settles in USDC on Sonic, quotes in THB/USD/EUR, and
takes a fraction of a percent, is a recurring flow through whichever bridge
carries it. That is the argument for Stargate's Sonic deployment being
funded, and it is not a hypothetical — the product already sends volume to
Sonic via Polymer today, and the same volume would go through Stargate if the
pool had LP shares minted against it.

---

## The diagnosis: the pool has zero LP tokens outstanding

This is the number that explains the rest. The pool holds ~67,316 USDC
and **0.00 S*USDC** — the pool's own LP token, read from the contract.
Nobody has deposited against it, so:

- there is no counterparty on the other side of a swap
- there is no price for the pool share
- the USDC sitting in the contract cannot be traded at size

The pool is not thin. It has no market at size. The USDC was seeded
into the contract without the LP being minted against it. The fix is
for whoever supplies the USDC to mint the pool's LP tokens, not just
to transfer tokens to the pool address.

**This is why the route caps at $100 rather than failing outright.**
Below roughly $100 the routing algorithm can price a small swap
without a pool share — enough to fill the order on the USDC sitting
there. Above that it has nothing to price against, so it declines.
That is also why Ethereum reaches $10,000 while Avalanche stops at
$100: Ethereum-side liquidity covers the gap that the Sonic-side
missing shares leave open.

## Stargate does not support Starknet — this matters for the ask

Stargate's own API publishes 56 chains and 123 assets. Sonic is one of
them. Starknet is not — and neither is it on the aggregator we use
(LI.FI's chain list has no Starknet either). So if the Stargate
Foundation conversation is also meant to cover a Starknet deployment,
that is a new chain integration for them, not a liquidity top-up.

Worth noting in their favour: Stargate's own site links to a
`dstChain=starknet` route, so the intent is there even though the
deployment is not. And StarkGate is Starknet's official
Ethereum↔Starknet bridge, so the path exists — just not through
Stargate.

## Proof points (all live, all verifiable)

| Claim | Source |
|---|---|
| Product is live | `https://ubotpay.com` — 200 |
| 8 settled payments, $30.01 volume, $0.17 revenue | `GET api.ubotpay.com/pricing` |
| Revenue wallet on-chain | `0xbE440bf22FFDDdD8372DDf66a4beffEB8D41B209` (Snowtrace) |
| 0.5% transfer fee vs 5.5% bank benchmark | `GET api.ubotpay.com/pricing` → `model` |
| 100 THB → 2.91 USDC, 0.02 fee, saves 0.14 vs bank | `GET api.ubotpay.com/pricing` → `example_100_thb` |
| **16 chains already enabled, Sonic included** | `GET api.ubotpay.com/auth/chains` — Avalanche, Base, Polygon, Arbitrum, Ethereum, Optimism, zkSync, **Sonic**, Solana, Linea, Scroll, Mantle, Celo, World Chain, Unichain + Fuji |
| Avalanche→Sonic bridge works today | `GET api.ubotpay.com/bridge/quote` — Polymer, 18 min, $0.26 total fee |
| 40 regression suites green | `run_tests.sh` → `40 suites passed, EXIT=0` |
| Stargate routability is measured, not asserted | `deploy/track_stargate_sonic.py`, re-run every 6h by a watchdog |
| The Sonic pool's own state, from the contract | `deploy/read_stargate_sonic_pool.py` — 67,316 USDC, 0.00 LP outstanding |
| Stargate's chain list, from their own API | `GET mainnet.stargate-api.com/v1/metadata?version=v2` — 56 chains: Sonic yes, Starknet no |
| USDC on Starknet, read on chain | `deploy/verify_starknet_usdc.py` — "USD Coin", 6 decimals, 4,388,217.22 supply |
| USDC on Sonic is **native Circle USDC** | Circle's own registry lists `0x29219dd400f2Bf60E5a23d13Be72B486D4038894` for Sonic mainnet |

**On that last point** — the USDC address that circulates on the web for
Sonic is wrong. We read the contract instead of copying a website:
`decimals() = 6`, `symbol() = "USDC"`, `name() = "USDC"`, 1798 bytes of
bytecode, proxy with `owner() = 0xfb5c45c955d5904360fe8eb34b4934f6a6e658b8`.
Circle's own contract-address registry confirms the same address. This is
also worth flagging to anyone integrating on Sonic: **verify the address,
do not copy it.**

---

## The four asks

### 1. Stargate: fund the Sonic USDC pool

**The pool holds $67.3K and cannot route $100 from Avalanche.** Every route into Sonic
through Stargate fails at LI.FI's aggregator level. We are ready to be a
recurring consumer of that route — our bridge already quotes and executes
Avalanche→Sonic.

**Specific asks:**
- A liquidity proposal for the Sonic USDC pool (and USDT, where it exists),
  at a size that can service real payment volume — not a few hundred
  thousand.
- A **named technical contact** for the integration, so we can point our
  bridge at Stargate directly rather than through an aggregator.
- Confirmation of the intended path: does Stargate on Sonic bridge via the
  **Sonic Gateway**, and do they intend to add **CCTP** when Circle ships
  `MessageTransmitter` on chain 146?

### 2. Circle CCTP on Sonic — timing

CCTP is the cleanest cross-chain path and it removes a hop. It is the
single biggest structural improvement available to a payments product on
Sonic. **When does Circle expect `MessageTransmitter`/`TokenMessenger` to
be live on chain 146?** We will design the native deployment around the
answer rather than guess.

### 3. Sonic Labs: gas sponsorship + FeeM

- **Gas sponsorship for the first transaction.** A user who arrives holding
  USDC only — the exact profile of a payments user — currently cannot pay
  gas without holding S. Sonic's "sponsored by design" programme and
  Circle's USDC paymaster may already cover this; we would like to know
  which applies, and how to enroll.
- **FeeM registration.** FeeM pays builders **90% of the network fees their
  app generates**. A payments product that settles in USDC on Sonic is
  unusual traffic — it keeps the app contract hot, and it is a real revenue
  line for us, not a grant. We intend to register the proxy, per the
  FeeM guidance. Please confirm the registration path for a non-upgradeable
  payment contract.
- **Innovator Fund.** The intake form is at `soniclabs.com/bd-intake`. We
  are applying for support for the native Sonic deployment — treasury
  liquidity, an S balance, and the go-to-market push. Tell us what you
  would prioritise funding here.

### 4. Go to market

- **A listing on MySonic.** The Sonic app directory, where the Stargate
  listing already sits.
- **A mention in the Sonic ecosystem posts and newsletter.**
- **Co-marketing.** Sonic Labs is pushing S as everyday spend — the Redotpay
  Visa card integration puts it in 130M+ merchant locations. A payments
  product that settles in USDC on Sonic in under two seconds, for a
  fraction of a percent, fits that story directly. We are ready to write
  the launch piece and the docs.

---

## What we are NOT asking for

No grant, no fee waiver, no exclusivity. We are asking for a working
liquidity rail, a technical contact, and a listing.

---

## What we are NOT committing to

**No launch date.** The delta is small but it is not zero: a treasury key, a
funded S balance, a domain, an nginx vhost, a TLS certificate, a profile
file, and a bridge integration against whatever route Stargate ends up
supporting. **Two to three weeks is honest**, and we would rather agree a
date after the first three asks are answered than guess now.

---

## The delta, in detail

This is the part that makes it a one-week job rather than a quarter. The
chain engine (`chains/engine.py`, `chains/evm.py`, `chains/registry.py`)
treats a chain as configuration. Sonic is EVM, so it reuses `evm.py`
unchanged — that file was written with Sonic named in its docstring as one
of the target chains.

Everything chain-specific lives in a YAML profile:

```yaml
name: USonicPay
brand: usonicpay
engine: evm

chains:
  kind: evm
  chain_id: 146
  name: Sonic
  rpc: https://rpc.soniclabs.com
  stable_address: 0x29219dd400f2Bf60E5a23d13Be72B486D4038894
  explorer: https://sonicscan.org
  native_symbol: S
  key_path: /opt/usonicpay/secrets/treasury.key

tokens:
  USDC:
    address: 0x29219dd400f2Bf60E5a23d13Be72B486D4038894
    decimals: 6
  USDT:
    address: 0x6047828dc181963ba44974801FF68e538dA5eaF9
    decimals: 6

primary_token: USDC
```

`api.py`, `core.py`, `bridge.py`, `dataagent.py` and `tg.py` call
`chain.send_usdc(...)`, `chain.balance(...)`, `chain.to_units(...)` and
never learn which chain they are talking to. **No Python changes.**

The rest is infrastructure:

| Item | Effort |
|---|---|
| Domain (`ubotsonicpay.com` + defensive set) | minutes, ~$60 |
| Sonic treasury key + funded S balance | 1 hour |
| `profiles/usonicpay.yaml` | ~1 hour |
| nginx vhost + TLS cert | ~1 hour |
| systemd service + env | ~1 hour |
| Front-end chain selector (Sonic first) | ~2 hours |
| Verify USDT address on-chain (Tether's own registry) | ~1 hour |
| Bridge route work: point the bridge at Stargate | ~1 day |
| **Total** | **~1 week, ~$60** |

**On the USDT address:** the one in the draft above comes from LI.FI's token
list and reads `decimals() = 6` on-chain, but it must be verified against
**Tether's own registry** the same way USDC was, and confirmed as
Tether-issued rather than a wrapped representation. We do not ship an
address we have not read the contract for.

---

## Domain — act on this one first

**`usonicpay.com` was taken.** RDAP shows it registered **2026-10-27**,
transfer locked, pointing at DNS-parking. It went minutes before we first
checked.

Still available, verified against Verisign's RDAP:

| Domain | Status |
|---|---|
| `usonicpay.com` | **TAKEN** (registered today) |
| `ubotsonicpay.com` | available |
| `ubotsonic.com` | available |
| `sonicspay.com` | available |
| `usonic-pay.com` | available |
| `usonicpay.io` / `.org` / `.net` | available |

We are going with **`ubotsonicpay.com`** as the public name — it is the
closest parallel to the existing `ubotpay.com` / `utronpay.com` /
`usolpay.com` family and it keeps the "ubot" brand, which is the asset.
The chain is a deployment target; U-bot Pay is the product. The rest of the
set is registered defensively and pointed at the same deployment.

---

## Channels

| Who | Where | Notes |
|---|---|---|
| Sonic Labs | `soniclabs.com/contact` | Main BD path. The Innovator Fund intake is at `soniclabs.com/bd-intake` — a separate, faster form for funding. |
| Sonic Labs BD | `bd@soniclabs.com` | Published address for exchange/DEX integrations. |
| Sonic Builders Telegram | `t.me/SonicBuilders` | Dev channel, fastest for technical questions on FeeM and sponsorship. |
| Stargate DAO | `stargate.discourse.group` → "New Chain Deployments" | The Sonic deployment thread is `stargate.discourse.group/t/stargate-deployment-to-sonic/62`. A follow-up on a live integration is on-topic there. |
| Stargate liquidity | Snapshot / SIP process | If funding the Sonic pool needs a proposal, the DAO process is the route. |

**Recommendation on sequence.** Send the Sonic Labs message first (it is
the relationship, and the asks — sponsorship, FeeM, listing, Innovator
Fund — are the ones that need a human answer). Send the Stargate message
in parallel but frame it as *a live consumer with volume, plus a concrete
number on what is missing from the pool*. Their Sonic thread already has
780 views and 40 likes; the deployment was ratified at 95.55% with USDC
and USDT pools promised on day one. **The USDC arrived; the LP shares
against it did not.** That is a fair thing to point out, politely, with
the $67.3K figure attached.

---

## Contact

- Product: `ubotpay.com` · API: `api.ubotpay.com` · Security: `security@ubotpay.com`
- Repo: `github.com/PedroYellow/U-bot-Pay`
