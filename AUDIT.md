# Audit — 15 Oct 2026 (pre-demo)

## Result: READY ✅

### Public surface (12/12 OK)
| URL | Status |
|---|---|
| https://ubotpay.com · www | 200, HTTPS, HTTP→301 |
| https://ubotpay.com/pitch (video 83 s, 3 MB) | 200 |
| https://agent.ubotpay.com (DataAgent, 402 on /report) | 200 / 402 |
| https://api.ubotpay.com/health · /docs | 200 |
| @Ubotpaybot (Telegram) | live, polling |
| GitHub PedroYellow/U-bot-Pay | public, 20+ commits |
| TLS cert (4 SANs) | valid until 2027-01-13, auto-renew on |

### Functional (13/13 OK, live API, mainnet)
balance EN/TH · send ES (quote 2.91 USDC) · cancel · invoices · pay invoice (quote) · dual-control (12 ≥ 10 → co-signer) · daily limit block (5000 THB > 40 USDC) · unknown recipient → asks · smalltalk identity "ubotpay" · voice widget → `open_url` opens /pitch (E2E verified) · x402 agent payment (settled on-chain earlier, tx in README).

Real mainnet txs: remittance 2.91 USDC in 1.78 s · x402 0.50 USDC in 2.23 s · recording tx 1.86 s.

### Security
- `/turn`, `/demo/seed`, `/agent/query` → 401 without / with wrong API key ✅
- x402: forged signature → 402 rejected; tampered amount → rejected; nonce replay → rejected ✅
- Ports 8090/8091 bound to 127.0.0.1 only; ufw 22/80/443 ✅
- nginx rate-limit 20 r/min per IP (burst 8) on `/turn` and `/agent/` — verified: 20 parallel → 9×200, 11×429 ✅
- SSH: `PasswordAuthentication no` (fixed — `50-cloud-init.conf` was overriding), root key-only, fail2ban active ✅
- Secrets: `.env` files 600; none tracked in git; HTML in repo uses placeholders; remote URL has no token ✅
- ElevenLabs agent: embed allowlist = ubotpay.com / www / agent only ✅
- Daily backup of DBs + env to `/root/backups` (14-day retention) ✅

### Known, accepted for demo
- **`UBOT_API_KEY` is in the public JS** (web console calls the API directly). Anyone can drive user `demo`. Blast radius: treasury 43.68 USDC, daily limit 40, dual-control ≥ 10, recipients are only pre-seeded wallets we control, rate-limited. Production fix: move calls behind a session/backend proxy.
- Demo treasury private key lives on the VPS (`/opt/ubotpay/.env`). Fine for ≤ 50 USDC; never fund it with more.
- `.agent_id` and the ElevenLabs quota (Starter, 40k chars, ~3k used) — a full demo run uses ~1.5k chars.

### Treasury at audit time
0.2988 AVAX (gas for ~80 txs) · 43.68 USDC · DataAgent payee 0.5 USDC

### Before going on stage
1. `curl https://api.ubotpay.com/health` → `usdc > 20`, `avax > 0.05`
2. Open https://ubotpay.com in Chrome, allow mic once, press 🎙, say "how much do I have?" — if it answers, you're good.
3. Have the MP4 downloaded locally (Download button on /pitch) in case Wi-Fi dies.
4. Phone hotspot ready; Telegram @Ubotpaybot works on 4G independently.
