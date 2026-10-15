# U-bot Pay

> **Live on Avalanche mainnet.** Two real transactions from the demo treasury:
> - 🗣 *"Send 100 baht to mom"* → 2.91 USDC settled in 1.78 s — [tx](https://snowtrace.io/tx/0xc0f12055984a3ddf34b889624416330f52b1799f72cd7fcf0a358a550028623d)
> - 🤖 *"Ask DataAgent about Sequoia"* → U-bot paid another agent 0.50 USDC via x402 (EIP-3009) in 2.23 s — [tx](https://snowtrace.io/tx/0x3d21c0b74c0f8a1ea3097258049702ff2dd7afa5dfa624e6e73a5d3b03f067fe)
>
> Try it: **https://ubotpay.com** (press 🎙) · **[@Ubotpaybot](https://t.me/Ubotpaybot)** · API **https://api.ubotpay.com/docs** · paid agent **https://agent.ubotpay.com**
 🎙⚡

**Talk to U-bot. Money moves.**
A voice-first finance agent for the [U-topia](https://u-topia.com) ecosystem — speak in any language, settle in USDC on **Avalanche** in under a second.

Built in 48h at **U-Hackathon 2026** (FLOW Summit, Bangkok) · Partners: **ElevenLabs** · **Avalanche**

---

## Why

Institutional rails like Canton and Stellar move trillions — but nobody *talks* to them.
Consumer finance apps have buttons, not conversations.
U-bot Pay is the missing layer: **a voice you can trust with your money.**

> 🎙 *"Send 2,000 baht to mom"*
> 🤖 *"That's 58.14 USDC to Mom in Chiang Mai, FX 34.4, zero fees. Shall I send it?"*
> 🎙 *"Yes"*
> 🤖 *"Done. Sent in 0.8 seconds."*

## What it does

| Feature | How |
|---|---|
| **Voice in, voice out** | ElevenLabs STT + TTS, 90+ languages (Thai, Burmese, Vietnamese, Spanish, English…) |
| **Natural-language intents** | Claude parses *"pay the Vietnam one"*, *"mándale 500 baht a mi madre"*, *"ส่งเงิน 300 บาทให้แม่"* |
| **Instant settlement** | USDC on Avalanche C-Chain (Fuji) — sub-second finality, tx link on every payment |
| **Transparent FX** | Local-currency quote → USDC, rate shown, zero fees |
| **Voice-confirmed payments** | Nothing moves without a spoken *yes* |
| **Dual control** | Payments above a threshold require a co-signer (Canton-style institutional policy) |
| **Daily limits** | Per-user spend caps |
| **Invoices** | U-bot reads your open invoices and pays them on command |
| **Private audit trail** | Memo hashed on-chain, detail off-chain |
| **Agent-to-agent (x402)** | EIP-3009 `transferWithAuthorization` — U-bot pays other AI agents per request |
| **Channels** | Telegram (voice notes), ElevenLabs Agents (phone/web), REST API |

## Architecture

```
 ┌──────────────┐   ┌──────────────┐   ┌─────────────┐
 │  Telegram    │   │  ElevenLabs  │   │   Web PWA   │
 │  voice note  │   │  Agent (call)│   │  (avatar)   │
 └──────┬───────┘   └──────┬───────┘   └──────┬──────┘
        │ STT               │ webhook tool     │
        ▼                   ▼                  ▼
 ┌──────────────────────────────────────────────────┐
 │                 POST /turn   (FastAPI)           │
 │   brain.py  →  Claude intent parser              │
 │   core.py   →  users · contacts · FX · policy    │
 │   chain.py  →  Avalanche C-Chain (mainnet; Fuji for tests) · USDC · EIP-3009  │
 └──────────────────────┬───────────────────────────┘
                        ▼
              Avalanche C-Chain (USDC)
              sub-second finality
```

One endpoint, one brain, every channel.

## Quick start

```bash
git clone https://github.com/<you>/ubotpay && cd ubotpay
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in keys
python main.py         # API on :8090 + Telegram polling
```

Fund the treasury wallet on Fuji: [AVAX faucet](https://core.app/tools/testnet-faucet/) · [USDC faucet](https://faucet.circle.com).

### Try the API

```bash
curl -X POST localhost:8090/demo/seed -H "x-api-key: $UBOT_API_KEY"

curl -X POST localhost:8090/turn -H "x-api-key: $UBOT_API_KEY" -H "Content-Type: application/json" \
  -d '{"user_key":"demo","text":"send 2000 baht to mom"}'
# → {"say":"That's 58.14 USDC to Mom (Chiang Mai), FX 34.4, zero fees. Shall I send it?","action":"confirm_needed",...}

curl -X POST localhost:8090/turn -H "x-api-key: $UBOT_API_KEY" -H "Content-Type: application/json" \
  -d '{"user_key":"demo","text":"yes"}'
# → {"say":"Done. Sent in 0.9 seconds.","action":"settled","data":{"tx":"0x…","explorer":"https://testnet.snowtrace.io/tx/0x…"}}
```

### ElevenLabs Agent setup

Create an agent in [ElevenLabs Agents](https://elevenlabs.io/app/agents) and add one **webhook tool**:

- **Name:** `ubot_turn`
- **URL:** `https://ubotpay.com/turn` · method `POST` · header `x-api-key`
- **Body:** `{"user_key": "{{user_id}}", "text": "{{user_message}}", "channel": "voice"}`
- **System prompt:** *"You are U-bot. For every user message call `ubot_turn` and speak back exactly the `say` field."*

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/turn` | One conversation turn → `{say, action, data}` |
| `POST` | `/approve` | Co-signer approves/rejects a pending payment |
| `GET` | `/balance/{user}` · `/history/{user}` · `/invoices/{user}` | Read models |
| `POST` | `/agent/query` | U-bot hires **DataAgent** and pays it via x402 (see below) |
| `POST` | `/x402/quote` → `402` · `/x402/settle` | Low-level x402 primitives (EIP-3009) |
| `POST` | `/demo/seed` | Demo contacts + invoices |
| `GET` | `/health` | Chain + treasury status |

## Business model (live, on every quote)

| Stream | Rate | Where it goes |
|---|---|---|
| Transfer fee | 0.5 % (min 0.01 USDC) | revenue wallet, 2nd transfer in the same settlement |
| FX spread | 0.3 % on local-currency quotes | same |
| Agent rail (x402) | 1 % of every agent-to-agent payment | same |
| Business plan | monthly SaaS (dual control, invoices, audit) | roadmap |

Recipient always receives the full amount; sender sees "our fee is 2 cents" in the spoken quote and "you save 0.14 USDC vs bank" on screen. `GET /pricing` returns the model, a worked example and live revenue stats. Configure with `UBOT_FEE_PCT`, `UBOT_FX_SPREAD_PCT`, `UBOT_AGENT_TAKE_PCT`, `UBOT_REVENUE_ADDRESS`.

## Agents paying agents — x402 live demo

`dataagent.py` is an independent paid AI service at **https://agent.ubotpay.com** (0.50 USDC per company report).

```
U-bot  → GET https://agent.ubotpay.com/report?company=Sequoia
       ← 402 Payment Required  {network: avalanche-fuji, asset: USDC, payTo: 0xebF8…, amount: 0.5, scheme: eip3009}
U-bot  → signs TransferWithAuthorization offline (EIP-712, no gas, no tx)
       → GET … again with  X-PAYMENT: <base64 signed authorization>
Agent  → recovers signer, checks payee/amount/expiry/nonce, settles on Avalanche, returns report + tx receipt
```

Say it: *"Ask DataAgent about Sequoia"* → U-bot pays, reads the brief back, shows the tx. No API key, no card, no human — money as an HTTP header.

## Telegram

`/start` · send a voice note or text · `/cosigner <telegram_id>` · `/approve <payment_id>` · `/reject <payment_id>`

## Roadmap

- [ ] Voice biometrics as the second factor (ElevenLabs speaker verification)
- [ ] Real FX oracle (Chainlink / Circle)
- [ ] Off-ramp to cash (MoneyGram-style anchors)
- [ ] ERC-8004 agent identity registry on Avalanche
- [ ] U-Card top-ups by voice
- [ ] Account abstraction wallets per user (no shared treasury)

## Team

Built by Pedro Miranda & team · U-Hackathon 2026 · Bangkok

## License

MIT
