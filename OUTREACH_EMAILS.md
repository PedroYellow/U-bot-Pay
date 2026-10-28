# Ready-to-send messages

_2026-10-27. Two messages: Sonic Labs and Stargate. Both are drafts for
Pedro to send from a real mailbox — do not send from a service account._

---

## 1. Sonic Labs

**To:** `soniclabs.com/contact` (or `bd@soniclabs.com` for BD)
**Subject:** Native U-bot Pay for Sonic — a live payments product, and a liquidity rail

Hi Sonic team,

You've been asked by teams building on Sonic for a native version of their
product. We're the other side of that request: **U-bot Pay** is a live,
voice-first payments product, and we'd like to bring a native Sonic version
of it to market. Rather than send a deck, here is the product running today
and a concrete list of what we need from you.

**The product, live right now**

You speak, it confirms, USDC settles in under two seconds.
`ubotpay.com` — 8 settled payments, $30.01 volume, a public revenue wallet
on Snowtrace, 40 regression suites green. It charges **0.5%** against a
**5.5%** bank-correspondent benchmark, and it is already a bridge
destination for 16 chains — **including Sonic**.

Today, Avalanche → Sonic works through Polymer: 100 USDC lands as 99.75,
in 18 minutes, for $0.26 total.

**Why a native version is a week of work, not a quarter**

We built our chain layer so that a chain is configuration, not code. Sonic
is EVM, so it reuses an engine that was written with Sonic named in it as a
target. Everything chain-specific lives in a YAML profile: chain id, RPC,
the USDC address, the explorer. The application code doesn't change at all.
The delta is a profile file, a treasury key, an nginx vhost and a service
file.

**What we'd like to ask for**

1. **Gas sponsorship for the first transaction.** Our users arrive holding
   USDC. A user with no S cannot currently pay gas, and that is exactly the
   user we most want to serve. Sonic's "sponsored by design" programme and
   Circle's USDC paymaster may already cover this — we'd like to know which
   applies and how to enrol.

2. **FeeM registration.** FeeM pays builders 90% of the network fees their
   app generates, and a payments app settles in USDC but keeps the app
   contract hot. That is a genuine revenue line for us rather than a grant,
   and we intend to register the contract. Could you confirm the
   registration path for a non-upgradeable payment contract?

3. **The Innovator Fund.** We'd like to apply for support for the native
   Sonic deployment — treasury liquidity, an S balance, and the
   go-to-market. We're filling in the intake form this week. If there's a
   particular shape of application that lands better with your team, we'd
   rather write it that way.

4. **A listing on MySonic**, and a mention in the ecosystem posts or
   newsletter if the launch goes well.

5. **A technical conversation** about the bridge route. Circle's CCTP is
   not yet deployed on Sonic, and Stargate's Sonic pool currently holds
   about $28K, so the paths in are Polymer and the Sonic Gateway. We want to
   build the native version on the route that will carry real volume — and
   we're ready to be a recurring consumer of it.

**On the USDC address**, since it has bitten people before: the USDC token
that circulates on the web for Sonic is wrong. The correct one is
`0x29219dd400f2Bf60E5a23d13Be72B486D4038894` — confirmed in Circle's own
contract registry, and confirmed on-chain (`decimals() = 6`, `symbol() =
"USDC"`, 1798 bytes, proxy with `owner() = 0xfb5c45c9…`). We read contracts
rather than copying websites, and we'd suggest anyone integrating do the
same.

**On timing** — we won't guess. The engineering is about a week, but we want
to build on the right route, so we'd rather agree a date after the first
few points are answered.

Happy to jump on a call, or to come to your builders' Telegram. And
genuinely — if the shape of a native Sonic U-bot Pay isn't useful to you,
tell us and we'll say so.

Best,
Pedro
U-bot Pay · https://ubotpay.com · security@ubotpay.com

---

## 2. Stargate Foundation

**To:** Stargate DAO Discourse → "New Chain Deployments"
**Thread:** `stargate.discourse.group/t/stargate-deployment-to-sonic/62`
**Subject:** Sonic USDC pool is live but cannot route — here's a consumer with volume

Hi Stargate team,

The Sonic deployment was ratified at 95.55% with USDC and USDT pools
promised on day one, and the contracts are deployed. But the USDC pool
currently holds **27,874 USDC**, and it cannot service a transfer in
practice. I wanted to share that with a concrete number, because we are
already routing volume into Sonic and would rather it went through you.

**What we measured, today**

The Stargate V2 USDC pool on Sonic:
`0xa272ffe20cffe769cdfc4b63088dcd2c82a2d8f9` — **27,339 USDC held**,
read straight from the contract on 2026-10-28. It was 27,874 USDC
the day before, so the liquidity is draining rather than being
supplied.

And with Stargate as the only allowed bridge, the aggregator returns no
route at any size:

```
10 USDC      -> No available quotes for the requested transfer
100 USDC     -> No available quotes for the requested transfer
1,000 USDC   -> No available quotes for the requested transfer
10,000 USDC  -> No available quotes for the requested transfer
100,000 USDC -> No available quotes for the requested transfer
```

For context, DefiLlama has Stargate V2 TVL at $75.58M, of which Ethereum
alone holds $53.02M and Sonic holds $27,836. Sonic's 30-day bridge volume
through Stargate is $118K against $301M on Ethereum.

**Why we are the right kind of consumer**

We are U-bot Pay — a voice-first payments product running in production on
Avalanche (`ubotpay.com`, 8 settled payments, 40 regression suites green,
0.5% fee against a 5.5% bank benchmark). It already bridges to Sonic: today
100 USDC goes Avalanche → Sonic via Polymer in 18 minutes for $0.26 total.

**We are building a native Sonic version of it** — USDC settling on Sonic
as the home chain, quoted in local currency, with a voice interface. A
payments product is recurring flow, not a one-off bridge. If the Stargate
Sonic pool were liquid, our route would go through it. Right now it can't,
so it goes through Polymer.

**What we're asking for**

1. **A liquidity proposal for the Sonic USDC pool** at a size that can
   service real payment volume rather than a few hundred thousand. The
   original proposal asked for 1m USDC and 1m USDT to be migrated; that
   would change the picture entirely.
2. **A named technical contact** so we can point our bridge at Stargate
   directly rather than through an aggregator — we would rather not take a
   fixed fee out of a 0.5% payment.
3. **A view on the path**: does Stargate on Sonic bridge via the Sonic
   Gateway, and do you intend to add CCTP when Circle ships
   `MessageTransmitter` on chain 146? We will design around the answer.

We are not asking for a grant or a waiver — a working rail and a technical
contact is enough. We're happy to be a first consumer and to say publicly
that the route goes through Stargate.

Best,
Pedro
U-bot Pay · https://ubotpay.com · security@ubotpay.com

---

## Sending notes

- **Send the Sonic Labs one first.** It is the relationship, and three of
  the five asks (sponsorship, FeeM, Innovator Fund) need a human answer.
  The Innovator Fund intake form is a separate, faster path than the
  contact form — do both.
- **The Stargate one goes in the existing Sonic deployment thread**, not a
  new one. A follow-up on a live integration is on-topic there, and the
  thread already has attention.
- **Lead with the number.** "$27,874" and "no route at $100" are the
  whole message. Everything else is context.
- **Do not promise a date.** If asked directly: "about a week of
  engineering once the route is settled, and we'd rather agree a date
  together than guess."
- **Re-check the pool balance before sending.** It moves. If it has been
  refilled, drop the "cannot route" claim and lead with the consumer
  volume instead.
