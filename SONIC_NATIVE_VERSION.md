# Native Sonic version — plan for the Stargate Foundation / Sonic Labs request

_2026-10-27._

## The ask

Stargate Foundation and Sonic Labs want their own native version of
U-bot Pay. A native version means the product runs on Sonic as its home
chain, not that it merely bridges to it.

**This is not a fork.** The chain-engine architecture already in the repo
(`chains/engine.py`, `chains/evm.py`, `chains/registry.py` + a YAML profile)
exists precisely so that a new chain is a config file. Sonic is EVM, so it
reuses `evm.py` unchanged.

---

## What is already done

| Piece | State |
|-------|-------|
| Sonic in the chain registry | `chain.py` line 338 — chain 146, rpc `https://rpc.soniclabs.com`, native `S` |
| Sonic USDC address | `0x29219dd400f2Bf60E5a23d13Be72B486D4038894` — verified on-chain, not copied from a website |
| Sonic in the wallet connector | `web/index.html` — `chainId: "0x92"`, `sonicscan.org` |
| Sonic as a bridge destination | LI.FI quotes Avalanche → Sonic via Polymer |
| EVM engine | `chains/evm.py` — explicitly written for "Avalanche, Base, **Sonic**, Arbitrum, Polygon…" |
| Bridge to Sonic | `/bridge/quote` + `/bridge/start` already route to it |
| Payment chain validation | `/auth/wallet/nonce` now rejects unknown chains (F-8 fix) — Sonic 146 returns 200 |

**Verified on-chain today** (not from a website):

```
decimals() -> 0x06
symbol()   -> "USDC"  (0x45555344)
name()     -> "USDC"
bytecode   -> 1798 bytes (a real contract, not an empty account)
chainId    -> 0x92 (146)
```

LI.FI's token API independently returns the same address for chain 146.
Note: the USDC address that circulates on most websites for Sonic is
**wrong** — the one in `chain.py` is the verified one.

---

## What a native Sonic deployment needs

### 1. A domain

The naming pattern already in use:

| Deployment | Domain | Registered |
|------------|--------|-----------|
| Avalanche (original) | `ubotpay.com` | 2026-10-21 |
| Tron | `utronpay.com` | 2026-10-21 |
| Solana | `usolpay.com` | 2026-10-22 |
| Sonic | **see below** | — |

**`usonicpay.com` was taken — 40 minutes before the first check.**

The RDAP record shows:

```
ldhName:   USONICPAY.COM
status:    client transfer prohibited
registered: 2026-10-27T09:59:15Z
ns:        ARTEMIS.DNS-PARKING.COM, HERMES.DNS-PARKING.COM
```

A fresh registration, transfer locked, pointing at DNS-parking. That is the
signature of someone who registered the obvious name and is waiting to see
whether anyone wants it. **The first RDAP check in this session returned a
false "available"** because Verisign's endpoint answers a query for a
registered-but-recently-created domain with an empty body rather than a 404.
Anything checked once, without a second read of the full record, is not
trustworthy — that is why the table below was re-verified with the full
record, including the registration date.

Verified available right now (full RDAP records, 404 from Verisign):

| Domain | Status |
|--------|--------|
| `usonicpay.com` | **TAKEN** (2026-10-27, locked, parked) |
| `ubotsonic.com` | available |
| `ubotsonicpay.com` | available |
| `sonicspay.com` | available |
| `usonic-pay.com` | available |
| `usonicpay.io` | available |
| `usonic.io` | available |
| `usonicpay.org` | available |
| `usonicpay.net` | available |
| `usonicpay.app` / `.dev` / `.xyz` / `.link` / `.tech` / `.finance` / `.one` | available |
| `usonic.com` | taken (NS: DNS105/106.REGISTER.COM) |
| `sonicpay.com` | taken (NS: AFTERNIC — for sale) |

**Recommendation — register the whole defensible set, today:**

- **Primary: `ubotsonicpay.com`.** It is the closest parallel to the
  existing family (`ubotpay` / `utronpay` / `usolpay`) and it is
  unambiguous. Unlike `usonicpay.com` it carries the "ubot" brand, which
  is the asset — the chain is a deployment target, the product is U-bot Pay.
- **Defensive, same day:** `ubotsonic.com`, `ubotsonicpay.com`,
  `usonicpay.com` (if the parked owner will sell — it is days old, so the
  price is likely low), `usonicpay.io`, `usonicpay.org`, `usonicpay.net`.
- **Optional redirect:** `usonic.com` (taken, parked) — if the owner will
  sell, it is the best short form.

> **Timing matters more here than in any other step of this plan.** Someone
> else registered the obvious domain minutes ago. The same thing will happen
> to the second-best name within days. Register everything defensible in one
> transaction — it is ~$60 for the lot — and point them all at the
> deployment once it is live.

### 2. A YAML profile — this is the whole "native" work

`profiles/usonicpay.yaml`, roughly 40 lines. The USolPay Solana profile is
the template; the Sonic one is the **EVM** form, which is much shorter:

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
  native: S
  key_path: /opt/usonicpay/secrets/treasury.key
  # private_key / revenue_address come from the environment

tokens:
  USDC:
    address: 0x29219dd400f2Bf60E5a23d13Be72B486D4038894
    decimals: 6
  USDT:
    address: 0x6047828dc181963ba44974801FF68e538dA5eaF9
    decimals: 6

primary_token: USDC
```

The USDT address above is from LI.FI's chain-146 token list and **must be
verified on-chain before it goes in the file** — the same way the USDC one
was. The rule the codebase already follows is "read it from the chain, do
not copy it from a website", and the Sonic USDC address is a live example of
why: the widely-published one is wrong.

**No Python changes.** `api.py`, `core.py`, `bridge.py`, `dataagent.py` and
`tg.py` call `chain.send_usdc(...)`, `chain.balance(...)`,
`chain.to_units(...)` and never learn which chain they are talking to.

### 3. A treasury key and a revenue address

- A fresh Sonic keypair, generated on the VPS, stored at
  `/opt/usonicpay/secrets/treasury.key` (0600, outside the webroot).
- A revenue address, separate from the treasury.
- Gas is paid in **S**, not ETH. The treasury needs an S balance. Sonic's
  "sponsored by design" programme covers the first transaction for users
  bridging in without S — worth raising with Sonic Labs, because it
  removes the single biggest onboarding friction.

### 4. An nginx vhost

`deploy/nginx_usonicpay.conf`, modelled on `deploy/nginx_usolpay.conf`:
the chosen public domain + `www` + `api.` + `agent.` subdomains, e.g.
`ubotsonicpay.com` + `www` + `api.ubotsonicpay.com` +
`agent.ubotsonicpay.com`,
with the same security-header set that was just cleaned up on
`ubotpay.com` — **copy the current one, do not copy the old one.** The
`ubotpay.com` vhost went through three failed dedup attempts before it was
got right; `deploy/nginx.conf` in the repo is now the corrected version and
carries the backup-file deny rule.

### 5. A systemd service

`deploy/usonicpay.service`, same shape as `usolpay.service`, running from
`/opt/usonicpay/`.

### 6. The front end

`web/index.html` needs the Sonic chain in the wallet connector, which
`ubotpay.com` already has. For a *native* Sonic deployment the coin
selector should show Sonic first and the explorer links should point at
`sonicscan.org`.

### 7. A bridge route

For a native deployment, Sonic is the home chain, so the useful bridge
direction is **Sonic → everywhere else**, and inbound from Avalanche
(where the current liquidity and traffic live). The LI.FI integration
already handles both directions; the destination list needs to change.

---

## Two things to verify before promising anything

### A. Is Sonic USDC Circle-native? — **ANSWERED: yes, native Circle USDC**

Verified from **Circle's own contract-address registry**
(`developers.circle.com/stablecoins/usdc-contract-addresses`):

| Chain | Address |
|-------|---------|
| **Sonic (mainnet)** | `0x29219dd400f2Bf60E5a23d13Be72B486D4038894` |
| Sonic Blaze Testnet | `0xA4879Fed32Ecbef99399e5cbC247E533421C4eC6` |

That is **exactly** the address already in `chain.py` — so the code is
right, and the pitch can honestly say "native Circle USDC on Sonic".

Cross-checks, all independent and all agreeing:

- On-chain `decimals()` → `0x06`, `symbol()` → `0x45555344` ("USDC"),
  `name()` → "USDC", 1798 bytes of bytecode (a real contract).
- LI.FI's chain-146 token list returns the same address.
- Sonic's own docs have a "Native USDC" page and state the **Sonic Gateway
  uses Circle's CCTP V2** for USDC transfers.
- The USDC is a **proxy**: `owner()` → `0xfb5c45c955d5904360fe8eb34b4934f6a6e658b8`
  (an EOA/multisig signer, no code) and the implementation lives at
  storage slot 8 → `0xca40611d7e52dc26e9fd52bfc83b27f232c87679`
  (5346 bytes, itself with an `owner()`). It uses a custom storage layout,
  not EIP-1967 — which is why the standard implementation slot reads empty.

> Note the widely-published "Sonic USDC" address that circulates on the web
> is **wrong**. This codebase already got that right by reading the contract
> instead of copying a website — and it is the single most common way a
> payments deployment ends up on the wrong token.

**Important caveat found while checking:** of the 69 addresses in Circle's
registry, **only the Sonic USDC token has code on Sonic** — there is no
Circle **CCTP `MessageTransmitter` or `TokenMessenger`** deployed on Sonic
yet. So:

- USDC on Sonic is native and Circle-issued. Good.
- But the canonical Circle cross-chain rail is **not yet live on Sonic**.
  Liquidity and bridging into Sonic therefore route through the **Sonic
  Gateway** (Sonic's own trustless Ethereum↔Sonic bridge) and **Stargate**,
  not CCTP.

That is exactly why Stargate matters here, and it is a concrete, useful
thing to ask them for: **Stargate's Sonic deployment is the liquidity rail
that makes a native Sonic U-bot Pay credible at volume.**

### B. Gas model — verify with a live transfer

Gas is paid in **S**, not ETH. Sonic publishes ~$0.001 per transfer, and
the treasury needs an S balance. Sonic's "sponsored by design" programme
covers the first transaction for users who bridge in with USDC only —
that is the single biggest onboarding friction for a payments product, and
it is worth raising with Sonic Labs directly. Circle also provides a
**paymaster for popular stablecoins like USDC**
(`developers.circle.com/stablecoins/paymaster-overview`), so the
USDC-only user may not need S at all.

**Do not quote a number from marketing.** Run one real transfer on a
throwaway key and measure.

---

## Talking to Stargate Foundation and Sonic Labs

What they are actually buying is a **payments rail on their chain** — voice
in, USDC out, sub-second, with the same product they can see live at
ubotpay.com. The pitch is:

- **It is already live on Avalanche.** This is not a deck; it is a running
  product with real settled payments (`/pricing` shows `settled_payments: 8`
  and the live revenue wallet on Snowtrace).
- **Sonic is the cheapest chain to run this on.** $0.001 transfers.
- **Native Circle USDC.** Sonic USDC is Circle-issued (Circle's own address
  registry confirms `0x29219dd4…`), so this is real USDC, not a bridged
  representation. Decimals 6, verified on-chain.
- **The native version is a config file, not a rewrite.** The chain engine
  is already built and already supports Sonic; the Sonic profile, the
  treasury key and the domain are the entire delta.
- **Stargate gives the liquidity.** Circle's CCTP is not deployed on Sonic
  yet, so the inbound rail into Sonic is the Sonic Gateway and Stargate.
  A native Sonic U-bot Pay makes Stargate's Sonic deployment useful to
  consumers, not just to LPs — that is the argument.
- **The moat is not the chain.** It is the voice-first product and the
  policy engine (the dual-control gate, the daily limits, the audit trail
  — 39 test suites). Any chain can be added; few teams will do it as
  fast.

**What to ask them for:**

1. **Stargate route + liquidity for USDC and USDT on Sonic, in both
   directions**, and a named technical contact for the integration. This is
   the one that matters most: with CCTP not yet on Sonic, Stargate *is* the
   rail. (Confirmed: Stargate's Sonic deployment was ratified by the DAO at
   95.55% with USDC and USDT pools on day 1.)
2. **CCTP on Sonic.** Ask Circle and Sonic Labs directly when Circle's
   `MessageTransmitter`/`TokenMessenger` go live on chain 146 — that is the
   cleanest cross-chain path and it removes a hop.
3. **Native USDT confirmation.** The address in the profile draft
   (`0x6047828dc181963ba44974801FF68e538dA5eaF9`) is from LI.FI's token
   list and reads `decimals() = 6` on-chain, but it must be verified
   against Tether's own registry the same way USDC was — and confirmed as
   Tether-issued rather than a wrapped representation.
4. **Gas sponsorship for the first transaction** (their "sponsored by
   design" programme) so a user arriving with USDC only can pay without S.
   Circle's USDC paymaster may already cover it — confirm which applies.
5. **A listing on MySonic** (the Sonic app directory) and a mention in
   their ecosystem posts.
6. **Co-marketing.** Sonic Labs has a large, active community and a paid
   Visa card integration with Redotpay — the S token is being pushed as
   everyday spend. A payments product that settles in S on Sonic fits that
   story directly.

**What NOT to commit to:** a launch date. The delta is small but it is not
zero — a treasury key, a funded S balance, a domain, an nginx vhost, a
cert, and a profile file. Two to three weeks is honest.

---

## Cost and effort

| Item | Cost | Effort |
|------|------|--------|
| `usonicpay.com` + defensive domains | ~$12–40/yr | minutes |
| Sonic treasury key + first S | gas only | 1 hour |
| `profiles/usonicpay.yaml` | free | ~1 hour |
| nginx vhost + cert | free | ~1 hour |
| systemd service + env | free | ~1 hour |
| Front-end chain selector | free | ~2 hours |
| Verify USDC/USDT on-chain | free | ~1 hour |
| Bridge route audit (Sonic → AVAX/EVM) | free | ~1 day |
| **Total** | **~$40** | **~1 week of work** |

Compare: the original Avalanche build was a fork. This is a profile.

---

## Immediate next steps

1. **Register the domains** (primary `ubotsonicpay.com` + the defensive
   set) — this is the only step that gets more expensive by waiting,
   because someone else already took `usonicpay.com` today.
2. **Verify the USDC type on-chain** (section A) — this determines whether
   the "native USDC" claim in the pitch is true. It is one `eth_call`.
3. **Verify the USDT address on-chain** before it goes in the profile.
4. **Write `profiles/usonicpay.yaml`** against the USolPay profile.
5. **Contact Sonic Labs and Stargate** with the pitch in section
   "Talking to…" — the Sonic Labs contact form and the Stargate DAO
   discourse are the two real channels.
