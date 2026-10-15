# Demo script — U-bot Pay (3 minutes)

**Setup before going on stage**
- Open https://www.ubotpay.com on the laptop (mic allowed), full screen. Console on the right is the "proof" screen.
- Phone with Telegram open on **@Ubotpaybot** (backup channel if the room wifi is bad).
- Treasury funded on **Avalanche MAINNET**: `GET https://api.ubotpay.com/health` shows `avax > 0.05`, `usdc > 20`. Real money — one full run costs ~20 USDC and it all lands in wallets we control.
- Co-signer set: on the approver's phone `/start` on @Ubotpaybot, then owner runs `/cosigner <approver_telegram_id>`.
- ElevenLabs quota ≥ 5,000 chars left (Starter plan recommended for demo day).

---

## 0:00 — Hook (20s)
> "Canton settles 9 trillion a month. Stellar moves money to 480,000 cash points. Nobody *talks* to either of them. U-topia's promise is finance for everyone — and everyone can talk. Meet U-bot."

## 0:20 — Balance in Thai (20s)  🎙 press *Talk to U-bot*
🎙 **"ฉันมีเงินเท่าไหร่"** *(How much do I have?)*
🤖 answers in Thai with USDC and baht. Point at the console: live balance from Avalanche.

> "Ninety languages. Same brain."

## 0:40 — Remittance with FX + voice confirmation (45s)
🎙 **"Send 100 baht to mom"**
🤖 *"That's 2.91 USDC to Mom in Chiang Mai, FX 34.4, zero fees. Shall I send it?"*
→ console shows the QUOTE badge.
🎙 **"Yes"**
🤖 *"Done. Sent in 0.9 seconds."* → console shows SETTLED + tx link. Click it → Snowtrace.

> "That was a cross-border payment. Not T+2. Not 3%. Under a second, zero fees, confirmed by voice."

## 1:25 — Invoices, in Spanish (30s)
🎙 **"¿Qué facturas tengo pendientes?"**
🤖 lists 3 invoices in Spanish.
🎙 **"Paga la de VietSupply"**
🤖 quotes 5 USDC → 🎙 **"Sí"** → settled.

> "A supplier in Vietnam just got paid by a Thai SME in Spanish. Settlement on Avalanche."

## 1:55 — Dual control (30s)
🎙 **"Pay the office rent"** (12 USDC ≥ 10 threshold)
🤖 *"That amount needs a second approval. I've asked your co-signer."*
→ Approver's phone buzzes with the Telegram request → they tap `/approve pay_xxx` → owner gets "Approved & sent in 0.8s".

> "Institutional policy — limits, dual control, hashed memo on-chain — with consumer UX. This is what Canton does for banks; we did it for a voice."

## 2:25 — Agents paying agents (25s)
Open https://api.ubotpay.com/docs → `POST /x402/quote` → shows the **402** payload.
> "When the counterparty is another AI agent, U-bot pays it per request with an EIP-3009 signed authorization — the x402 pattern Avalanche's own hackathon just rewarded. No human, no card, no API key — money as an HTTP header."

## 2:50 — Close (10s)
> "Voice is the interface. Avalanche is the rail. U-topia is the ecosystem. Talk to U-bot — money moves."
Show GitHub QR / URL: github.com/PedroYellow/U-bot-Pay

---

## If something breaks
| Symptom | Do |
|---|---|
| Voice agent doesn't connect | Use the text console — same brain, same settlement. Say "mic trouble, same flow typed". |
| "Payment failed: insufficient funds" | Treasury out of AVAX gas → top up at core.app faucet. |
| ElevenLabs quota exhausted | Switch to Telegram text; or type in console. |
| Room wifi dead | Phone hotspot; Telegram on 4G works independently. |

## Judge FAQ (prepared answers)
- **Custody?** Demo uses a treasury wallet; roadmap = per-user account-abstraction wallets, voice as a signer.
- **FX source?** Static table for demo; production = Chainlink price feeds / Circle FX.
- **Fraud / voice spoofing?** Voice confirmation + limits + dual control today; ElevenLabs speaker verification next.
- **Why Avalanche?** Sub-second finality makes "it's done" true before the sentence ends; ICM lets U-topia run its own L1 later; x402/ERC-8004 for agents.
- **Why not just a chatbot?** The whole product is the *confirmation loop* — quote → spoken yes → settlement → proof. That loop is the trust layer.
